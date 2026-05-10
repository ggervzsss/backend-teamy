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
from app.html_sanitizer import sanitize_html
from app.models import FileResource, Project, ProjectMember, Task, TaskAssignee, TaskFileLink, User
from app.projects import get_project_membership, require_project_active
from app.schemas import (
    FileResourceSummaryResponse,
    LinkedTaskResponse,
    ProjectMemberListResponse,
    ProjectMemberResponse,
    TaskAssigneeResponse,
    TaskAssigneeUpdateRequest,
    TaskCreateRequest,
    TaskListResponse,
    TaskResponse,
    TaskReviewRequest,
    TaskSocketTicketResponse,
    UserResponse,
)
from app.security import create_task_socket_ticket, decode_session_token, decode_task_socket_ticket

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


async def serialize_task_linked_files(db: AsyncSession, task: Task) -> list[FileResourceSummaryResponse]:
    result = await db.execute(
        select(FileResource, User)
        .join(TaskFileLink, TaskFileLink.file_resource_id == FileResource.id)
        .join(User, User.id == FileResource.created_by_user_id)
        .where(TaskFileLink.task_id == task.id)
        .order_by(FileResource.updated_at.desc(), FileResource.created_at.desc())
    )
    linked_task = LinkedTaskResponse(id=task.id, title=task.title, status=task.status)
    return [
        FileResourceSummaryResponse(
            id=resource.id,
            project_id=resource.project_id,
            title=resource.title,
            kind=resource.kind,
            url=resource.url,
            created_by=UserResponse.model_validate(creator),
            linked_tasks=[linked_task],
            created_at=resource.created_at,
            updated_at=resource.updated_at,
        )
        for resource, creator in result.all()
    ]


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
        review_remarks=task.review_remarks,
        assignees=assignees,
        linked_files=await serialize_task_linked_files(db, task),
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
    if any(assignee.status in {"in_progress", "ready_for_review"} for assignee in assignees):
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


@router.get("/tasks/ws-ticket", response_model=TaskSocketTicketResponse)
async def create_task_socket_ticket_endpoint(
    user: Annotated[User, Depends(get_current_user)],
    membership: Annotated[tuple[Project, ProjectMember], Depends(get_project_membership)],
    settings=Depends(get_settings),
) -> TaskSocketTicketResponse:
    project, _ = membership
    return TaskSocketTicketResponse(ticket=create_task_socket_ticket(user.id, project.id, settings))


@router.post("/tasks", response_model=TaskResponse, status_code=status.HTTP_201_CREATED)
async def create_task(
    payload: TaskCreateRequest,
    user: Annotated[User, Depends(get_current_user)],
    membership: Annotated[tuple[Project, ProjectMember], Depends(get_project_membership)],
    db: AsyncSession = Depends(get_db),
) -> TaskResponse:
    project, project_member = membership
    require_project_active(project)
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

    if payload.linked_file is not None:
        linked_file_title = (payload.linked_file.title or payload.title).strip()
        if not linked_file_title:
            linked_file_title = payload.title.strip()
        linked_file_url = payload.linked_file.url.strip() if payload.linked_file.url else None
        if payload.linked_file.mode == "link" and not linked_file_url:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="URL is required for linked external files")
        resource = FileResource(
            project_id=project.id,
            title=linked_file_title,
            kind=payload.linked_file.mode,
            url=linked_file_url if payload.linked_file.mode == "link" else None,
            content_html=sanitize_html("<p></p>") if payload.linked_file.mode == "doc" else None,
            created_by_user_id=user.id,
        )
        db.add(resource)
        await db.flush()
        db.add(TaskFileLink(task_id=task.id, file_resource_id=resource.id))

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
    require_project_active(project)
    task = await get_task_for_project(db, project.id, task_id)
    assignee_result = await db.execute(select(TaskAssignee).where(TaskAssignee.task_id == task.id, TaskAssignee.user_id == user.id))
    assignee = assignee_result.scalar_one_or_none()
    if assignee is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only assigned members can update this task")
    if task.status == "done":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Done tasks cannot be updated")
    if task.status == "for_review":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Tasks in review cannot be updated until changes are requested")

    assignee.status = payload.status
    assignee.completed_at = datetime.now(UTC) if payload.status == "ready_for_review" else None
    await move_task_after_assignee_update(db, task)
    await db.commit()
    await db.refresh(task)
    response = await serialize_task(db, task)
    await manager.broadcast(project.id, "task.updated", response)
    return response


@router.post("/tasks/{task_id}/submit-review", response_model=TaskResponse)
async def submit_task_for_review(
    task_id: UUID,
    user: Annotated[User, Depends(get_current_user)],
    membership: Annotated[tuple[Project, ProjectMember], Depends(get_project_membership)],
    db: AsyncSession = Depends(get_db),
) -> TaskResponse:
    project, _ = membership
    require_project_active(project)
    task = await get_task_for_project(db, project.id, task_id)
    if task.status == "done":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Done tasks cannot be submitted for review")
    if task.status == "for_review":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Task is already for review")

    assignee_result = await db.execute(select(TaskAssignee).where(TaskAssignee.task_id == task.id))
    assignees = list(assignee_result.scalars().all())
    if not any(assignee.user_id == user.id for assignee in assignees):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only assigned members can submit this task for review")
    if not assignees or any(assignee.status != "ready_for_review" for assignee in assignees):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Every assignee must be ready for review first")

    task.status = "for_review"
    task.reviewed_by_user_id = None
    task.reviewed_at = None
    await db.commit()
    await db.refresh(task)
    response = await serialize_task(db, task)
    await manager.broadcast(project.id, "task.submitted", response)
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
    require_project_active(project)
    require_leader(project_member)
    task = await get_task_for_project(db, project.id, task_id)
    if task.status != "for_review":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Only tasks for review can be reviewed")

    if payload.action == "approve":
        task.status = "done"
        task.reviewed_by_user_id = user.id
        task.reviewed_at = datetime.now(UTC)
        task.review_remarks = None
    else:
        task.status = "in_progress"
        task.reviewed_by_user_id = None
        task.reviewed_at = None
        task.review_remarks = payload.remarks.strip() if payload.remarks else None
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
    ticket = websocket.query_params.get("ticket")

    if ticket:
        try:
            user_id, ticket_project_id = decode_task_socket_ticket(ticket, settings)
        except HTTPException:
            await websocket.accept()
            await websocket.close(code=1008)
            return
        if ticket_project_id != project_id:
            await websocket.accept()
            await websocket.close(code=1008)
            return
    else:
        session_cookie = websocket.cookies.get(settings.session_cookie_name)
        if not session_cookie:
            await websocket.accept()
            await websocket.close(code=1008)
            return

        try:
            user_id = decode_session_token(session_cookie, settings)
        except HTTPException:
            await websocket.accept()
            await websocket.close(code=1008)
            return

    async with SessionLocal() as db:
        result = await db.execute(select(ProjectMember).where(ProjectMember.project_id == project_id, ProjectMember.user_id == user_id))
        if result.scalar_one_or_none() is None:
            await websocket.accept()
            await websocket.close(code=1008)
            return

    await manager.connect(project_id, websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(project_id, websocket)
