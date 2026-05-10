from collections import defaultdict
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect, status
from fastapi.encoders import jsonable_encoder
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db, SessionLocal
from app.dependencies import get_current_user
from app.models import Project, ProjectMember, Task, TaskAssignee, User
from app.projects import get_project_membership
from app.schemas import (
    ProjectMemberListResponse,
    ProjectMemberResponse,
    TaskAssigneeResponse,
    TaskAssigneeUpdateRequest,
    TaskCreateRequest,
    TaskListResponse,
    TaskResponse,
    TaskReviewRequest,
    UserResponse,
)
from app.security import decode_session_token

router = APIRouter(prefix="/projects/{project_id}", tags=["tasks"])


class TaskConnectionManager:
    def __init__(self) -> None:
        self.active_connections: dict[UUID, set[WebSocket]] = defaultdict(set)

    async def connect(self, project_id: UUID, websocket: WebSocket) -> None:
        await websocket.accept()
        self.active_connections[project_id].add(websocket)

    def disconnect(self, project_id: UUID, websocket: WebSocket) -> None:
        self.active_connections[project_id].discard(websocket)
        if not self.active_connections[project_id]:
            del self.active_connections[project_id]

    async def broadcast(self, project_id: UUID, event: str, task: TaskResponse) -> None:
        payload = jsonable_encoder({"event": event, "task": task})
        dead_connections: list[WebSocket] = []
        for websocket in self.active_connections.get(project_id, set()).copy():
            try:
                await websocket.send_json(payload)
            except RuntimeError:
                dead_connections.append(websocket)

        for websocket in dead_connections:
            self.disconnect(project_id, websocket)


manager = TaskConnectionManager()


def require_leader(membership: ProjectMember) -> None:
    if membership.role != "leader":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only project leaders can perform this action")


async def get_project_member_user_ids(db: AsyncSession, project_id: UUID) -> set[UUID]:
    result = await db.execute(select(ProjectMember.user_id).where(ProjectMember.project_id == project_id))
    return set(result.scalars().all())


async def serialize_task(db: AsyncSession, task: Task) -> TaskResponse:
    creator = await db.get(User, task.created_by_user_id)
    reviewed_by = await db.get(User, task.reviewed_by_user_id) if task.reviewed_by_user_id else None
    assignee_result = await db.execute(
        select(TaskAssignee, User)
        .join(User, User.id == TaskAssignee.user_id)
        .where(TaskAssignee.task_id == task.id)
        .order_by(User.full_name.asc(), User.email.asc())
    )
    assignees = [
        TaskAssigneeResponse(
            id=assignee.id,
            user=UserResponse.model_validate(user),
            status=assignee.status,
            completed_at=assignee.completed_at,
        )
        for assignee, user in assignee_result.all()
    ]
    if creator is None:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Task creator could not be loaded")

    return TaskResponse(
        id=task.id,
        project_id=task.project_id,
        title=task.title,
        description=task.description,
        priority=task.priority,
        due_date=task.due_date,
        status=task.status,
        created_by=UserResponse.model_validate(creator),
        reviewed_by=UserResponse.model_validate(reviewed_by) if reviewed_by else None,
        reviewed_at=task.reviewed_at,
        assignees=assignees,
        created_at=task.created_at,
        updated_at=task.updated_at,
    )


async def get_task_for_project(db: AsyncSession, project_id: UUID, task_id: UUID) -> Task:
    result = await db.execute(select(Task).where(Task.project_id == project_id, Task.id == task_id))
    task = result.scalar_one_or_none()
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    return task


async def move_task_after_assignee_update(db: AsyncSession, task: Task) -> None:
    assignee_result = await db.execute(select(TaskAssignee).where(TaskAssignee.task_id == task.id))
    assignees = list(assignee_result.scalars().all())
    if assignees and all(assignee.status == "done" for assignee in assignees):
        task.status = "for_review"
    elif any(assignee.status == "in_progress" for assignee in assignees):
        task.status = "in_progress"
    else:
        task.status = "todo"
    task.reviewed_by_user_id = None
    task.reviewed_at = None


@router.get("/members", response_model=ProjectMemberListResponse)
async def list_project_members(
    membership: Annotated[tuple[Project, ProjectMember], Depends(get_project_membership)],
    db: AsyncSession = Depends(get_db),
) -> ProjectMemberListResponse:
    project, _ = membership
    result = await db.execute(
        select(ProjectMember, User)
        .join(User, User.id == ProjectMember.user_id)
        .where(ProjectMember.project_id == project.id)
        .order_by(ProjectMember.role.asc(), User.full_name.asc(), User.email.asc())
    )
    return ProjectMemberListResponse(
        members=[
            ProjectMemberResponse(
                id=member.id,
                user=UserResponse.model_validate(user),
                role=member.role,
                joined_at=member.joined_at,
            )
            for member, user in result.all()
        ]
    )


@router.get("/tasks", response_model=TaskListResponse)
async def list_tasks(
    membership: Annotated[tuple[Project, ProjectMember], Depends(get_project_membership)],
    db: AsyncSession = Depends(get_db),
) -> TaskListResponse:
    project, _ = membership
    result = await db.execute(select(Task).where(Task.project_id == project.id).order_by(Task.created_at.desc()))
    tasks = [await serialize_task(db, task) for task in result.scalars().all()]
    return TaskListResponse(tasks=tasks)


@router.post("/tasks", response_model=TaskResponse, status_code=status.HTTP_201_CREATED)
async def create_task(
    payload: TaskCreateRequest,
    user: Annotated[User, Depends(get_current_user)],
    membership: Annotated[tuple[Project, ProjectMember], Depends(get_project_membership)],
    db: AsyncSession = Depends(get_db),
) -> TaskResponse:
    project, project_member = membership
    require_leader(project_member)

    assignee_ids = list(dict.fromkeys(payload.assignee_ids))
    member_user_ids = await get_project_member_user_ids(db, project.id)
    invalid_assignees = [assignee_id for assignee_id in assignee_ids if assignee_id not in member_user_ids]
    if invalid_assignees:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Every assignee must be a project member")

    task = Task(
        project_id=project.id,
        title=payload.title.strip(),
        description=payload.description.strip() if payload.description else None,
        priority=payload.priority,
        due_date=payload.due_date,
        status=payload.initial_status,
        created_by_user_id=user.id,
    )
    db.add(task)
    await db.flush()

    for assignee_id in assignee_ids:
        db.add(TaskAssignee(task_id=task.id, user_id=assignee_id, status=payload.initial_status))

    await db.commit()
    await db.refresh(task)
    response = await serialize_task(db, task)
    await manager.broadcast(project.id, "task.created", response)
    return response


@router.patch("/tasks/{task_id}/assignees/me", response_model=TaskResponse)
async def update_my_task_status(
    task_id: UUID,
    payload: TaskAssigneeUpdateRequest,
    user: Annotated[User, Depends(get_current_user)],
    membership: Annotated[tuple[Project, ProjectMember], Depends(get_project_membership)],
    db: AsyncSession = Depends(get_db),
) -> TaskResponse:
    project, _ = membership
    task = await get_task_for_project(db, project.id, task_id)
    assignee_result = await db.execute(select(TaskAssignee).where(TaskAssignee.task_id == task.id, TaskAssignee.user_id == user.id))
    assignee = assignee_result.scalar_one_or_none()
    if assignee is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only assigned members can update this task")
    if task.status == "done":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Done tasks cannot be updated")

    assignee.status = payload.status
    assignee.completed_at = datetime.now(UTC) if payload.status == "done" else None
    await move_task_after_assignee_update(db, task)
    await db.commit()
    await db.refresh(task)
    response = await serialize_task(db, task)
    await manager.broadcast(project.id, "task.updated", response)
    return response


@router.post("/tasks/{task_id}/review", response_model=TaskResponse)
async def review_task(
    task_id: UUID,
    payload: TaskReviewRequest,
    user: Annotated[User, Depends(get_current_user)],
    membership: Annotated[tuple[Project, ProjectMember], Depends(get_project_membership)],
    db: AsyncSession = Depends(get_db),
) -> TaskResponse:
    project, project_member = membership
    require_leader(project_member)
    task = await get_task_for_project(db, project.id, task_id)
    if task.status != "for_review":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Only tasks for review can be reviewed")

    if payload.action == "approve":
        task.status = "done"
        task.reviewed_by_user_id = user.id
        task.reviewed_at = datetime.now(UTC)
    else:
        task.status = "in_progress"
        task.reviewed_by_user_id = None
        task.reviewed_at = None
        assignee_result = await db.execute(select(TaskAssignee).where(TaskAssignee.task_id == task.id))
        for assignee in assignee_result.scalars().all():
            assignee.status = "in_progress"
            assignee.completed_at = None

    await db.commit()
    await db.refresh(task)
    response = await serialize_task(db, task)
    await manager.broadcast(project.id, "task.reviewed", response)
    return response


@router.websocket("/tasks/ws")
async def task_updates(websocket: WebSocket, project_id: UUID) -> None:
    settings = get_settings()
    session_cookie = websocket.cookies.get(settings.session_cookie_name)
    if not session_cookie:
        await websocket.close(code=1008)
        return

    try:
        user_id = decode_session_token(session_cookie, settings)
    except HTTPException:
        await websocket.close(code=1008)
        return

    async with SessionLocal() as db:
        result = await db.execute(select(ProjectMember).where(ProjectMember.project_id == project_id, ProjectMember.user_id == user_id))
        if result.scalar_one_or_none() is None:
            await websocket.close(code=1008)
            return

    await manager.connect(project_id, websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(project_id, websocket)
