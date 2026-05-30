from collections import defaultdict
from datetime import UTC, date, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, WebSocket, WebSocketDisconnect, status
from fastapi.encoders import jsonable_encoder
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db, SessionLocal
from app.dependencies import get_current_user
from app.html_sanitizer import sanitize_html
from app.models import FileResource, Project, ProjectMember, Task, TaskAssignee, TaskFileLink, User
from app.notifications import (
    create_user_notifications,
    get_project_leader_recipients,
    get_user_recipients,
    send_task_assignment_email,
    send_task_changes_requested_email,
    send_task_ready_for_review_email,
)
from app.projects import get_project_membership, require_project_active
from app.schemas import (
    FileResourceSummaryResponse,
    LinkedTaskResponse,
    ProjectMemberListResponse,
    ProjectMemberResponse,
    TaskAssigneeResponse,
    TaskAssigneeUpdateRequest,
    TaskCreateRequest,
    TaskExistingFileLinkRequest,
    TaskLinkedFileCreateRequest,
    TaskListResponse,
    TaskResponse,
    TaskReviewRequest,
    TaskSocketTicketResponse,
    TaskUpdateRequest,
)
from app.security import create_task_socket_ticket, decode_session_token, decode_task_socket_ticket
from app.user_responses import serialize_project_user

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


async def get_project_leader_user_ids(db: AsyncSession, project_id: UUID) -> set[UUID]:
    result = await db.execute(select(ProjectMember.user_id).where(ProjectMember.project_id == project_id, ProjectMember.role == "leader"))
    return set(result.scalars().all())


async def user_is_task_assignee(db: AsyncSession, task_id: UUID, user_id: UUID) -> bool:
    result = await db.execute(select(TaskAssignee.id).where(TaskAssignee.task_id == task_id, TaskAssignee.user_id == user_id))
    return result.scalar_one_or_none() is not None


def require_private_task_owner(task: Task, user: User) -> None:
    if task.is_private and task.created_by_user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")


def require_task_manager(project_member: ProjectMember, task: Task, user: User) -> None:
    if project_member.role != "leader" and task.created_by_user_id != user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only the task creator or project leader can edit this task")


def resolve_task_start_date(start_date: date | None, due_date: date | None) -> date:
    if start_date is not None:
        return start_date
    today = datetime.now(UTC).date()
    if due_date is not None and due_date < today:
        return due_date
    return today


def validate_task_date_range(start_date: date, due_date: date | None) -> None:
    if due_date is not None and due_date < start_date:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Due date cannot be before start date")


def build_linked_file_resource(project_id: UUID, user_id: UUID, fallback_title: str, payload: TaskLinkedFileCreateRequest) -> FileResource:
    linked_file_title = (payload.title or fallback_title).strip()
    if not linked_file_title:
        linked_file_title = fallback_title.strip()
    linked_file_url = payload.url.strip() if payload.url else None
    if payload.mode == "link" and not linked_file_url:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="URL is required for linked external files")
    return FileResource(
        project_id=project_id,
        title=linked_file_title,
        kind=payload.mode,
        url=linked_file_url if payload.mode == "link" else None,
        content_html=sanitize_html("<p></p>") if payload.mode == "doc" else None,
        created_by_user_id=user_id,
    )


async def serialize_task_linked_files(db: AsyncSession, task: Task) -> list[FileResourceSummaryResponse]:
    result = await db.execute(
        select(FileResource, User, ProjectMember)
        .join(TaskFileLink, TaskFileLink.file_resource_id == FileResource.id)
        .join(User, User.id == FileResource.created_by_user_id)
        .join(ProjectMember, (ProjectMember.user_id == User.id) & (ProjectMember.project_id == FileResource.project_id))
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
            created_by=serialize_project_user(creator, member),
            linked_tasks=[linked_task],
            created_at=resource.created_at,
            updated_at=resource.updated_at,
        )
        for resource, creator, member in result.all()
    ]


async def get_project_member_for_user(db: AsyncSession, project_id: UUID, user_id: UUID) -> ProjectMember | None:
    result = await db.execute(select(ProjectMember).where(ProjectMember.project_id == project_id, ProjectMember.user_id == user_id))
    return result.scalar_one_or_none()


async def serialize_task_assignees(db: AsyncSession, task: Task) -> list[TaskAssigneeResponse]:
    assignee_result = await db.execute(
        select(TaskAssignee, User, ProjectMember)
        .join(User, User.id == TaskAssignee.user_id)
        .join(ProjectMember, (ProjectMember.user_id == User.id) & (ProjectMember.project_id == task.project_id))
        .where(TaskAssignee.task_id == task.id)
        .order_by(User.full_name.asc(), User.email.asc())
    )
    return [
        TaskAssigneeResponse(
            id=assignee.id,
            user=serialize_project_user(user, member),
            status=assignee.status,
            completed_at=assignee.completed_at,
        )
        for assignee, user, member in assignee_result.all()
    ]


async def serialize_task(db: AsyncSession, task: Task) -> TaskResponse:
    creator = await db.get(User, task.created_by_user_id)
    reviewed_by = await db.get(User, task.reviewed_by_user_id) if task.reviewed_by_user_id else None
    if creator is None:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Task creator could not be loaded")
    creator_member = await get_project_member_for_user(db, task.project_id, creator.id)
    reviewed_by_member = await get_project_member_for_user(db, task.project_id, reviewed_by.id) if reviewed_by else None

    return TaskResponse(
        id=task.id,
        project_id=task.project_id,
        title=task.title,
        description=task.description,
        start_date=task.start_date,
        due_date=task.due_date,
        status=task.status,
        is_record_only=task.is_record_only,
        is_private=task.is_private,
        personal_kind=task.personal_kind,
        created_by=serialize_project_user(creator, creator_member),
        reviewed_by=serialize_project_user(reviewed_by, reviewed_by_member) if reviewed_by else None,
        reviewed_at=task.reviewed_at,
        review_remarks=task.review_remarks,
        assignees=await serialize_task_assignees(db, task),
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


async def serialize_tasks_batch(db: AsyncSession, tasks: list[Task], project_id: UUID) -> list[TaskResponse]:
    if not tasks:
        return []

    task_ids = [t.id for t in tasks]
    task_map = {t.id: t for t in tasks}

    # Collect all user IDs needed (creators + reviewers)
    user_ids: set[UUID] = set()
    for t in tasks:
        user_ids.add(t.created_by_user_id)
        if t.reviewed_by_user_id:
            user_ids.add(t.reviewed_by_user_id)

    # Batch 1: Fetch all needed Users — ONE query
    user_result = await db.execute(select(User).where(User.id.in_(user_ids)))
    users_by_id: dict[UUID, User] = {u.id: u for u in user_result.scalars().all()}

    # Batch 2: Fetch all ProjectMembers for those users — ONE query
    member_result = await db.execute(
        select(ProjectMember).where(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id.in_(user_ids),
        )
    )
    members_by_user_id: dict[UUID, ProjectMember] = {m.user_id: m for m in member_result.scalars().all()}

    # Batch 3: Fetch ALL assignees with user + member data — ONE query
    assignee_result = await db.execute(
        select(TaskAssignee, User, ProjectMember)
        .join(User, User.id == TaskAssignee.user_id)
        .join(ProjectMember, (ProjectMember.user_id == User.id) & (ProjectMember.project_id == project_id))
        .where(TaskAssignee.task_id.in_(task_ids))
        .order_by(User.full_name.asc(), User.email.asc())
    )
    assignees_by_task: dict[UUID, list[TaskAssigneeResponse]] = defaultdict(list)
    for assignee, user, member in assignee_result.all():
        assignees_by_task[assignee.task_id].append(
            TaskAssigneeResponse(
                id=assignee.id,
                user=serialize_project_user(user, member),
                status=assignee.status,
                completed_at=assignee.completed_at,
            )
        )

    # Batch 4: Fetch ALL linked files with creator data — ONE query
    linked_result = await db.execute(
        select(TaskFileLink, FileResource, User, ProjectMember)
        .join(FileResource, FileResource.id == TaskFileLink.file_resource_id)
        .join(User, User.id == FileResource.created_by_user_id)
        .join(ProjectMember, (ProjectMember.user_id == User.id) & (ProjectMember.project_id == project_id))
        .where(TaskFileLink.task_id.in_(task_ids))
        .order_by(FileResource.updated_at.desc(), FileResource.created_at.desc())
    )
    files_by_task: dict[UUID, list[FileResourceSummaryResponse]] = defaultdict(list)
    for link, resource, creator, member in linked_result.all():
        task = task_map[link.task_id]
        linked_task = LinkedTaskResponse(id=task.id, title=task.title, status=task.status)
        files_by_task[link.task_id].append(
            FileResourceSummaryResponse(
                id=resource.id,
                project_id=resource.project_id,
                title=resource.title,
                kind=resource.kind,
                url=resource.url,
                created_by=serialize_project_user(creator, member),
                linked_tasks=[linked_task],
                created_at=resource.created_at,
                updated_at=resource.updated_at,
            )
        )

    # Assemble TaskResponse objects
    results: list[TaskResponse] = []
    for task in tasks:
        creator = users_by_id.get(task.created_by_user_id)
        if creator is None:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Task creator could not be loaded")
        creator_member = members_by_user_id.get(creator.id)
        reviewed_by = users_by_id.get(task.reviewed_by_user_id) if task.reviewed_by_user_id else None
        reviewed_by_member = members_by_user_id.get(reviewed_by.id) if reviewed_by else None

        results.append(TaskResponse(
            id=task.id,
            project_id=task.project_id,
            title=task.title,
            description=task.description,
            start_date=task.start_date,
            due_date=task.due_date,
            status=task.status,
            is_record_only=task.is_record_only,
            is_private=task.is_private,
            personal_kind=task.personal_kind,
            created_by=serialize_project_user(creator, creator_member),
            reviewed_by=serialize_project_user(reviewed_by, reviewed_by_member) if reviewed_by else None,
            reviewed_at=task.reviewed_at,
            review_remarks=task.review_remarks,
            assignees=assignees_by_task.get(task.id, []),
            linked_files=files_by_task.get(task.id, []),
            created_at=task.created_at,
            updated_at=task.updated_at,
        ))
    return results


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
                user=serialize_project_user(user, member),
                role=member.role,
                nickname=member.nickname,
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
    result = await db.execute(select(Task).where(Task.project_id == project.id, Task.is_private.is_(False)).order_by(Task.created_at.desc()))
    tasks = list(result.scalars().all())
    return TaskListResponse(tasks=await serialize_tasks_batch(db, tasks, project.id))


@router.get("/tasks/me", response_model=TaskListResponse)
async def list_my_tasks(
    user: Annotated[User, Depends(get_current_user)],
    membership: Annotated[tuple[Project, ProjectMember], Depends(get_project_membership)],
    db: AsyncSession = Depends(get_db),
) -> TaskListResponse:
    project, _ = membership
    result = await db.execute(
        select(Task)
        .join(TaskAssignee, TaskAssignee.task_id == Task.id)
        .where(Task.project_id == project.id, TaskAssignee.user_id == user.id)
        .order_by(Task.created_at.desc())
    )
    tasks = list(result.scalars().all())
    return TaskListResponse(tasks=await serialize_tasks_batch(db, tasks, project.id))


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
    background_tasks: BackgroundTasks,
    user: Annotated[User, Depends(get_current_user)],
    membership: Annotated[tuple[Project, ProjectMember], Depends(get_project_membership)],
    db: AsyncSession = Depends(get_db),
    settings=Depends(get_settings),
) -> TaskResponse:
    project, project_member = membership
    require_project_active(project)
    if payload.is_private and payload.is_record_only:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Private tasks cannot be record-only")
    if payload.is_private and payload.linked_file is not None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Private items cannot link shared resources")
    if payload.is_record_only:
        require_leader(project_member)
    if payload.initial_status == "done" and not payload.is_record_only:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Only record-only tasks can be created as done")

    assignee_ids = list(dict.fromkeys(payload.assignee_ids))
    if payload.is_private:
        if set(assignee_ids) != {user.id}:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Private items can only be assigned to you")
        assignee_ids = [user.id]

    member_user_ids = await get_project_member_user_ids(db, project.id)
    invalid_assignees = [assignee_id for assignee_id in assignee_ids if assignee_id not in member_user_ids]
    if invalid_assignees:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Every assignee must be a project member")

    start_date = resolve_task_start_date(payload.start_date, payload.due_date)
    validate_task_date_range(start_date, payload.due_date)

    is_done_on_create = payload.initial_status == "done"
    completed_at = datetime.now(UTC) if is_done_on_create else None
    task = Task(
        project_id=project.id,
        title=payload.title.strip(),
        description=payload.description.strip() if payload.description else None,
        start_date=start_date,
        due_date=payload.due_date,
        status=payload.initial_status,
        is_record_only=payload.is_record_only,
        is_private=payload.is_private,
        personal_kind=payload.personal_kind if payload.is_private else "task",
        created_by_user_id=user.id,
        reviewed_by_user_id=user.id if is_done_on_create else None,
        reviewed_at=completed_at,
    )
    db.add(task)
    await db.flush()

    for assignee_id in assignee_ids:
        db.add(
            TaskAssignee(
                task_id=task.id,
                user_id=assignee_id,
                status="ready_for_review" if is_done_on_create else payload.initial_status,
                completed_at=completed_at,
            )
        )

    if payload.linked_file is not None:
        resource = build_linked_file_resource(project.id, user.id, payload.title, payload.linked_file)
        db.add(resource)
        await db.flush()
        db.add(TaskFileLink(task_id=task.id, file_resource_id=resource.id))

    if not is_done_on_create and not payload.is_private:
        await create_user_notifications(
            db,
            set(assignee_ids),
            project_id=project.id,
            kind="task.assigned",
            title=f"New task assigned: {task.title}",
            body=f"You have been assigned to {task.title} in {project.name}.",
            target_path=f"/projects/{project.id}/task-board",
            is_email_backed=True,
        )
    await db.commit()
    await db.refresh(task)
    response = await serialize_task(db, task)
    if not is_done_on_create and not payload.is_private:
        recipients = await get_user_recipients(db, set(assignee_ids))
        background_tasks.add_task(send_task_assignment_email, settings, recipients, project.id, project.name, task.title, task.due_date)
    if not payload.is_private:
        await manager.broadcast(project.id, "task.created", response)
    return response


@router.patch("/tasks/{task_id}", response_model=TaskResponse)
async def update_task(
    task_id: UUID,
    payload: TaskUpdateRequest,
    background_tasks: BackgroundTasks,
    user: Annotated[User, Depends(get_current_user)],
    membership: Annotated[tuple[Project, ProjectMember], Depends(get_project_membership)],
    db: AsyncSession = Depends(get_db),
    settings=Depends(get_settings),
) -> TaskResponse:
    project, project_member = membership
    require_project_active(project)
    task = await get_task_for_project(db, project.id, task_id)
    require_private_task_owner(task, user)
    require_task_manager(project_member, task, user)
    if task.is_private and payload.assignee_ids is not None and set(payload.assignee_ids) != {user.id}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Private items can only be assigned to you")

    if "title" in payload.model_fields_set and payload.title is not None:
        task.title = payload.title.strip()
    if "description" in payload.model_fields_set:
        task.description = payload.description.strip() if payload.description else None
    next_due_date = payload.due_date if "due_date" in payload.model_fields_set else task.due_date
    if "start_date" in payload.model_fields_set:
        task.start_date = resolve_task_start_date(payload.start_date, next_due_date)
    if "due_date" in payload.model_fields_set:
        task.due_date = next_due_date
        if "start_date" not in payload.model_fields_set and task.due_date is not None and task.due_date < task.start_date:
            task.start_date = task.due_date
    validate_task_date_range(task.start_date, task.due_date)

    assignees_changed = False
    newly_assigned_user_ids: set[UUID] = set()
    if payload.assignee_ids is not None:
        assignee_ids = list(dict.fromkeys(payload.assignee_ids))
        if not assignee_ids:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Choose at least one assignee")
        member_user_ids = await get_project_member_user_ids(db, project.id)
        invalid_assignees = [assignee_id for assignee_id in assignee_ids if assignee_id not in member_user_ids]
        if invalid_assignees:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Every assignee must be a project member")

        assignee_result = await db.execute(select(TaskAssignee).where(TaskAssignee.task_id == task.id))
        current_assignees = {assignee.user_id: assignee for assignee in assignee_result.scalars().all()}
        next_assignee_ids = set(assignee_ids)
        if set(current_assignees) != next_assignee_ids:
            assignees_changed = True
            newly_assigned_user_ids = next_assignee_ids - set(current_assignees)

        for assignee_user_id, assignee in current_assignees.items():
            if assignee_user_id not in next_assignee_ids:
                await db.delete(assignee)

        for assignee_id in assignee_ids:
            if assignee_id not in current_assignees:
                completed_at = datetime.now(UTC) if task.status == "done" else None
                db.add(
                    TaskAssignee(
                        task_id=task.id,
                        user_id=assignee_id,
                        status="ready_for_review" if task.status == "done" else "todo" if task.status == "todo" else "in_progress",
                        completed_at=completed_at,
                    )
                )

    if assignees_changed and task.status == "for_review":
        task.status = "in_progress"
        task.reviewed_by_user_id = None
        task.reviewed_at = None

    if newly_assigned_user_ids and not task.is_private:
        await create_user_notifications(
            db,
            newly_assigned_user_ids,
            project_id=project.id,
            kind="task.assigned",
            title=f"New task assigned: {task.title}",
            body=f"You have been assigned to {task.title} in {project.name}.",
            target_path=f"/projects/{project.id}/task-board",
            is_email_backed=True,
        )
    await db.commit()
    await db.refresh(task)
    response = await serialize_task(db, task)
    if newly_assigned_user_ids and not task.is_private:
        recipients = await get_user_recipients(db, newly_assigned_user_ids)
        background_tasks.add_task(send_task_assignment_email, settings, recipients, project.id, project.name, task.title, task.due_date)
    if not task.is_private:
        await manager.broadcast(project.id, "task.updated", response)
    return response


@router.post("/tasks/{task_id}/linked-files", response_model=TaskResponse, status_code=status.HTTP_201_CREATED)
async def link_task_file(
    task_id: UUID,
    payload: TaskLinkedFileCreateRequest,
    user: Annotated[User, Depends(get_current_user)],
    membership: Annotated[tuple[Project, ProjectMember], Depends(get_project_membership)],
    db: AsyncSession = Depends(get_db),
) -> TaskResponse:
    project, project_member = membership
    require_project_active(project)
    task = await get_task_for_project(db, project.id, task_id)
    require_private_task_owner(task, user)
    if task.is_private:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Private items cannot link shared resources")
    is_assignee = await user_is_task_assignee(db, task.id, user.id)
    if project_member.role != "leader" and task.created_by_user_id != user.id and not is_assignee:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only assigned members, task creators, or project leaders can link task resources")

    resource = build_linked_file_resource(project.id, user.id, task.title, payload)
    db.add(resource)
    await db.flush()
    db.add(TaskFileLink(task_id=task.id, file_resource_id=resource.id))
    await db.commit()
    await db.refresh(task)
    response = await serialize_task(db, task)
    await manager.broadcast(project.id, "task.updated", response)
    return response


@router.post("/tasks/{task_id}/linked-files/existing", response_model=TaskResponse, status_code=status.HTTP_201_CREATED)
async def link_existing_task_file(
    task_id: UUID,
    payload: TaskExistingFileLinkRequest,
    user: Annotated[User, Depends(get_current_user)],
    membership: Annotated[tuple[Project, ProjectMember], Depends(get_project_membership)],
    db: AsyncSession = Depends(get_db),
) -> TaskResponse:
    project, project_member = membership
    require_project_active(project)
    task = await get_task_for_project(db, project.id, task_id)
    require_private_task_owner(task, user)
    if task.is_private:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Private items cannot link shared resources")
    is_assignee = await user_is_task_assignee(db, task.id, user.id)
    if project_member.role != "leader" and task.created_by_user_id != user.id and not is_assignee:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only assigned members, task creators, or project leaders can link task resources")

    resource_result = await db.execute(select(FileResource).where(FileResource.project_id == project.id, FileResource.id == payload.file_id))
    resource = resource_result.scalar_one_or_none()
    if resource is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File resource not found")

    existing_link_result = await db.execute(select(TaskFileLink).where(TaskFileLink.task_id == task.id, TaskFileLink.file_resource_id == resource.id))
    if existing_link_result.scalar_one_or_none() is None:
        db.add(TaskFileLink(task_id=task.id, file_resource_id=resource.id))
        await db.commit()
        await db.refresh(task)

    response = await serialize_task(db, task)
    await manager.broadcast(project.id, "task.updated", response)
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
    require_private_task_owner(task, user)
    assignee_result = await db.execute(select(TaskAssignee).where(TaskAssignee.task_id == task.id, TaskAssignee.user_id == user.id))
    assignee = assignee_result.scalar_one_or_none()
    if assignee is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only assigned members can update this task")
    if task.is_private:
        if payload.status == "ready_for_review":
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Private items do not use review status")
        assignee.status = "ready_for_review" if payload.status == "done" else payload.status
        assignee.completed_at = datetime.now(UTC) if payload.status == "done" else None
        task.status = payload.status
        task.reviewed_by_user_id = user.id if payload.status == "done" else None
        task.reviewed_at = assignee.completed_at if payload.status == "done" else None
        await db.commit()
        await db.refresh(task)
        return await serialize_task(db, task)
    if task.status == "done":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Done tasks cannot be updated")
    if payload.status not in {"in_progress", "ready_for_review"}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Assigned tasks can only be moved to progress or ready for review")
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
    background_tasks: BackgroundTasks,
    user: Annotated[User, Depends(get_current_user)],
    membership: Annotated[tuple[Project, ProjectMember], Depends(get_project_membership)],
    db: AsyncSession = Depends(get_db),
    settings=Depends(get_settings),
) -> TaskResponse:
    project, _ = membership
    require_project_active(project)
    task = await get_task_for_project(db, project.id, task_id)
    require_private_task_owner(task, user)
    if task.is_private:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Private items do not use review submission")
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
    # Notify project leaders that the task has been submitted for review.
    leader_user_ids = await get_project_leader_user_ids(db, project.id)
    await create_user_notifications(
        db,
        leader_user_ids,
        project_id=project.id,
        kind="task.ready_for_review",
        title=f"Task submitted for review: {task.title}",
        body=f"{task.title} in {project.name} has been submitted for review.",
        target_path=f"/projects/{project.id}/task-board",
        is_email_backed=True,
    )
    await db.commit()
    recipients = await get_project_leader_recipients(db, project.id)
    background_tasks.add_task(send_task_ready_for_review_email, settings, recipients, project.id, project.name, task.title)
    await manager.broadcast(project.id, "task.submitted", response)
    return response


@router.post("/tasks/{task_id}/review", response_model=TaskResponse)
async def review_task(
    task_id: UUID,
    payload: TaskReviewRequest,
    background_tasks: BackgroundTasks,
    user: Annotated[User, Depends(get_current_user)],
    membership: Annotated[tuple[Project, ProjectMember], Depends(get_project_membership)],
    db: AsyncSession = Depends(get_db),
    settings=Depends(get_settings),
) -> TaskResponse:
    project, project_member = membership
    require_project_active(project)
    require_leader(project_member)
    task = await get_task_for_project(db, project.id, task_id)
    if task.is_private:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
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
        task_assignees = assignee_result.scalars().all()
        for assignee in task_assignees:
            assignee.status = "in_progress"
            assignee.completed_at = None
        await create_user_notifications(
            db,
            {assignee.user_id for assignee in task_assignees},
            project_id=project.id,
            kind="task.changes_requested",
            title=f"Changes requested: {task.title}",
            body=task.review_remarks or f"{task.title} needs revisions or additional changes.",
            target_path=f"/projects/{project.id}/task-board",
            is_email_backed=True,
        )

    await db.commit()
    await db.refresh(task)
    response = await serialize_task(db, task)
    if payload.action == "request_changes":
        recipients = await get_user_recipients(db, {assignee.user.id for assignee in response.assignees})
        background_tasks.add_task(
            send_task_changes_requested_email,
            settings,
            recipients,
            project.id,
            project.name,
            task.title,
            task.review_remarks,
        )
    await manager.broadcast(project.id, "task.reviewed", response)
    return response


@router.delete("/tasks/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_task(
    task_id: UUID,
    user: Annotated[User, Depends(get_current_user)],
    membership: Annotated[tuple[Project, ProjectMember], Depends(get_project_membership)],
    db: AsyncSession = Depends(get_db),
) -> None:
    project, project_member = membership
    require_project_active(project)
    task = await get_task_for_project(db, project.id, task_id)
    require_private_task_owner(task, user)
    require_task_manager(project_member, task, user)

    response = await serialize_task(db, task)
    await db.delete(task)
    await db.commit()
    if not task.is_private:
        await manager.broadcast(project.id, "task.deleted", response)


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


