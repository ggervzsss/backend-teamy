from datetime import UTC, date, datetime
import json
from random import SystemRandom
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, HTTPException, Path, Response, UploadFile, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user
from app.models import Announcement, AnnouncementRead, FileResource, Notification, Project, ProjectMember, Task, TaskAssignee, TaskFileLink, User
from app.schemas import ProjectArchiveRequest, ProjectCreateRequest, ProjectDeleteRequest, ProjectJoinRequest, ProjectListResponse, ProjectResponse, ProjectUpdateRequest
from app.team_realtime import broadcast_member_joined

router = APIRouter(prefix="/projects", tags=["projects"])
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
random = SystemRandom()


def normalize_teamy_code(teamy_code: str) -> str:
    return teamy_code.strip().upper().replace(" ", "").replace("_", "-")


def format_teamy_code(raw: str) -> str:
    return f"TMY-{raw[:4]}-{raw[4:]}"


async def generate_teamy_code(db: AsyncSession) -> str:
    for _ in range(10):
        raw = "".join(random.choice(CODE_ALPHABET) for _ in range(7))
        code = format_teamy_code(raw)
        result = await db.execute(select(Project.id).where(Project.teamy_code == code))
        if result.scalar_one_or_none() is None:
            return code
    raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Could not generate a unique Teamy code")


async def get_member_count(db: AsyncSession, project_id: UUID) -> int:
    result = await db.execute(select(func.count(ProjectMember.id)).where(ProjectMember.project_id == project_id))
    return int(result.scalar_one())


async def serialize_project(db: AsyncSession, project: Project, membership: ProjectMember) -> ProjectResponse:
    return ProjectResponse(
        id=project.id,
        name=project.name,
        description=project.description,
        teamy_code=project.teamy_code,
        role=membership.role,
        member_count=await get_member_count(db, project.id),
        archived_at=project.archived_at,
        created_at=project.created_at,
        updated_at=project.updated_at,
    )


def export_record(instance: object, fields: tuple[str, ...]) -> dict[str, object]:
    return {field: getattr(instance, field) for field in fields}


def parse_uuid(value: object, field_name: str) -> UUID:
    try:
        return UUID(str(value))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Invalid backup UUID for {field_name}") from exc


def parse_backup_datetime(value: object, field_name: str, required: bool = True) -> datetime | None:
    if value is None:
        if required:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Missing backup datetime for {field_name}")
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Invalid backup datetime for {field_name}") from exc


def parse_backup_date(value: object, field_name: str, required: bool = True) -> date | None:
    if value is None:
        if required:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Missing backup date for {field_name}")
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Invalid backup date for {field_name}") from exc


def require_backup_record(payload: dict[str, object], key: str) -> dict[str, object]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Backup is missing {key}")
    return value


def backup_records(payload: dict[str, object], key: str) -> list[dict[str, object]]:
    value = payload.get(key, [])
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Backup field {key} must be a list")
    return value


def require_project_leader(membership: ProjectMember) -> None:
    if membership.role != "leader":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only workspace owners can perform this action")


def require_project_active(project: Project) -> None:
    if project.archived_at is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This workspace is archived and read-only")


async def get_project_membership(
    project_id: Annotated[UUID, Path()],
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> tuple[Project, ProjectMember]:
    result = await db.execute(
        select(Project, ProjectMember)
        .join(ProjectMember, ProjectMember.project_id == Project.id)
        .where(Project.id == project_id, ProjectMember.user_id == user.id)
    )
    row = result.one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return row[0], row[1]


@router.get("", response_model=ProjectListResponse)
async def list_projects(user: Annotated[User, Depends(get_current_user)], db: AsyncSession = Depends(get_db)) -> ProjectListResponse:
    result = await db.execute(
        select(Project, ProjectMember, func.count(ProjectMember.id).over(partition_by=Project.id).label("member_count"))
        .join(ProjectMember, ProjectMember.project_id == Project.id)
        .where(Project.id.in_(select(ProjectMember.project_id).where(ProjectMember.user_id == user.id)))
        .order_by(Project.updated_at.desc(), Project.created_at.desc())
    )
    projects = [
        ProjectResponse(
            id=project.id,
            name=project.name,
            description=project.description,
            teamy_code=project.teamy_code,
            role=membership.role,
            member_count=int(member_count),
            archived_at=project.archived_at,
            created_at=project.created_at,
            updated_at=project.updated_at,
        )
        for project, membership, member_count in result.all()
        if membership.user_id == user.id
    ]
    return ProjectListResponse(projects=projects)


@router.post("", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
async def create_project(
    payload: ProjectCreateRequest,
    user: Annotated[User, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
) -> ProjectResponse:
    project = Project(
        name=payload.name.strip(),
        description=payload.description.strip() if payload.description else None,
        teamy_code=await generate_teamy_code(db),
        created_by_user_id=user.id,
    )
    db.add(project)
    await db.flush()

    membership = ProjectMember(project_id=project.id, user_id=user.id, role="leader")
    db.add(membership)
    await db.commit()
    await db.refresh(project)
    await db.refresh(membership)
    return await serialize_project(db, project, membership)


@router.post("/join", response_model=ProjectResponse)
async def join_project(
    payload: ProjectJoinRequest,
    user: Annotated[User, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
) -> ProjectResponse:
    teamy_code = normalize_teamy_code(payload.teamy_code)
    project_result = await db.execute(select(Project).where(Project.teamy_code == teamy_code))
    project = project_result.scalar_one_or_none()
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No project found for that Teamy code")

    member_result = await db.execute(select(ProjectMember).where(ProjectMember.project_id == project.id, ProjectMember.user_id == user.id))
    membership = member_result.scalar_one_or_none()
    is_new_member = False
    if membership is None:
        membership = ProjectMember(project_id=project.id, user_id=user.id, role="member")
        db.add(membership)
        await db.commit()
        await db.refresh(membership)
        is_new_member = True

    if is_new_member:
        await broadcast_member_joined(db, project.id, membership)

    return await serialize_project(db, project, membership)


@router.post("/import", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
async def import_project_backup(
    backup: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ProjectResponse:
    try:
        raw_backup = json.loads((await backup.read()).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Upload a valid Teamy project backup JSON file") from exc
    if not isinstance(raw_backup, dict) or raw_backup.get("format") != "teamy_project_backup":
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Upload a valid Teamy project backup JSON file")

    project_record = require_backup_record(raw_backup, "project")
    project_id = parse_uuid(project_record.get("id"), "project.id")
    existing_project = await db.get(Project, project_id)
    if existing_project is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This project backup has already been imported")

    teamy_code = str(project_record.get("teamy_code") or "").strip()
    if not teamy_code:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Backup project is missing its Teamy code")
    code_result = await db.execute(select(Project.id).where(Project.teamy_code == teamy_code))
    if code_result.scalar_one_or_none() is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A project with this Teamy code already exists")

    backup_users = backup_records(raw_backup, "users")
    user_id_map: dict[UUID, UUID] = {}
    users_to_create: list[User] = []
    for user_record in backup_users:
        source_user_id = parse_uuid(user_record.get("id"), "users.id")
        email = str(user_record.get("email") or "").strip().lower()
        if not email:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Backup user is missing an email")

        existing_by_email_result = await db.execute(select(User).where(User.email == email))
        existing_by_email = existing_by_email_result.scalar_one_or_none()
        if existing_by_email is not None:
            user_id_map[source_user_id] = existing_by_email.id
            continue

        existing_by_id = await db.get(User, source_user_id)
        target_user_id = source_user_id if existing_by_id is None else uuid4()
        username = user_record.get("username")
        clean_username = str(username).strip() if username else None
        if clean_username:
            username_result = await db.execute(select(User.id).where(User.username == clean_username))
            if username_result.scalar_one_or_none() is not None:
                clean_username = None

        users_to_create.append(
            User(
                id=target_user_id,
                email=email,
                full_name=str(user_record.get("full_name") or email.split("@")[0])[:160],
                username=clean_username,
                password_hash=None,
                auth_provider=str(user_record.get("auth_provider") or "imported")[:32],
                provider_subject=None,
                avatar_url=str(user_record["avatar_url"]) if user_record.get("avatar_url") else None,
                google_avatar_url=str(user_record["google_avatar_url"]) if user_record.get("google_avatar_url") else None,
                last_online_at=parse_backup_datetime(user_record.get("last_online_at"), "users.last_online_at", required=False),
                created_at=parse_backup_datetime(user_record.get("created_at"), "users.created_at") or datetime.now(UTC),
                updated_at=parse_backup_datetime(user_record.get("updated_at"), "users.updated_at") or datetime.now(UTC),
            )
        )
        user_id_map[source_user_id] = target_user_id

    db.add_all(users_to_create)
    await db.flush()

    def mapped_user_id(value: object, field_name: str, fallback: UUID | None = None) -> UUID:
        source_id = parse_uuid(value, field_name)
        mapped_id = user_id_map.get(source_id)
        if mapped_id is None:
            if fallback is not None:
                return fallback
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Backup references unknown user in {field_name}")
        return mapped_id

    project = Project(
        id=project_id,
        name=str(project_record.get("name") or "Imported Workspace")[:160],
        description=str(project_record["description"]) if project_record.get("description") is not None else None,
        teamy_code=teamy_code,
        created_by_user_id=mapped_user_id(project_record.get("created_by_user_id"), "project.created_by_user_id", fallback=user.id),
        archived_at=parse_backup_datetime(project_record.get("archived_at"), "project.archived_at", required=False),
        created_at=parse_backup_datetime(project_record.get("created_at"), "project.created_at") or datetime.now(UTC),
        updated_at=parse_backup_datetime(project_record.get("updated_at"), "project.updated_at") or datetime.now(UTC),
    )
    db.add(project)
    await db.flush()

    current_user_membership: ProjectMember | None = None
    seen_memberships: set[UUID] = set()
    for member_record in backup_records(raw_backup, "members"):
        member_user_id = mapped_user_id(member_record.get("user_id"), "members.user_id")
        if member_user_id in seen_memberships:
            continue
        membership = ProjectMember(
            id=parse_uuid(member_record.get("id"), "members.id"),
            project_id=project.id,
            user_id=member_user_id,
            role=str(member_record.get("role") or "member")[:16],
            nickname=str(member_record["nickname"])[:40] if member_record.get("nickname") is not None else None,
            joined_at=parse_backup_datetime(member_record.get("joined_at"), "members.joined_at") or datetime.now(UTC),
        )
        db.add(membership)
        seen_memberships.add(member_user_id)
        if member_user_id == user.id:
            current_user_membership = membership

    if current_user_membership is None:
        current_user_membership = ProjectMember(project_id=project.id, user_id=user.id, role="leader", joined_at=datetime.now(UTC))
        db.add(current_user_membership)

    await db.flush()

    valid_task_ids: set[UUID] = set()
    for task_record in backup_records(raw_backup, "tasks"):
        task_id = parse_uuid(task_record.get("id"), "tasks.id")
        db.add(
            Task(
                id=task_id,
                project_id=project.id,
                title=str(task_record.get("title") or "Imported task")[:200],
                description=str(task_record["description"]) if task_record.get("description") is not None else None,
                start_date=parse_backup_date(task_record.get("start_date"), "tasks.start_date") or date.today(),
                due_date=parse_backup_date(task_record.get("due_date"), "tasks.due_date", required=False),
                status=str(task_record.get("status") or "todo")[:24],
                is_record_only=bool(task_record.get("is_record_only", False)),
                is_private=bool(task_record.get("is_private", False)),
                personal_kind=str(task_record.get("personal_kind") or "task")[:16],
                created_by_user_id=mapped_user_id(task_record.get("created_by_user_id"), "tasks.created_by_user_id", fallback=user.id),
                reviewed_by_user_id=mapped_user_id(task_record["reviewed_by_user_id"], "tasks.reviewed_by_user_id") if task_record.get("reviewed_by_user_id") else None,
                reviewed_at=parse_backup_datetime(task_record.get("reviewed_at"), "tasks.reviewed_at", required=False),
                review_remarks=str(task_record["review_remarks"]) if task_record.get("review_remarks") is not None else None,
                created_at=parse_backup_datetime(task_record.get("created_at"), "tasks.created_at") or datetime.now(UTC),
                updated_at=parse_backup_datetime(task_record.get("updated_at"), "tasks.updated_at") or datetime.now(UTC),
            )
        )
        valid_task_ids.add(task_id)

    valid_file_ids: set[UUID] = set()
    for file_record in backup_records(raw_backup, "file_resources"):
        file_id = parse_uuid(file_record.get("id"), "file_resources.id")
        db.add(
            FileResource(
                id=file_id,
                project_id=project.id,
                title=str(file_record.get("title") or "Imported resource")[:240],
                kind=str(file_record.get("kind") or "doc")[:16],
                url=str(file_record["url"])[:2048] if file_record.get("url") is not None else None,
                content_html=str(file_record["content_html"]) if file_record.get("content_html") is not None else None,
                created_by_user_id=mapped_user_id(file_record.get("created_by_user_id"), "file_resources.created_by_user_id", fallback=user.id),
                created_at=parse_backup_datetime(file_record.get("created_at"), "file_resources.created_at") or datetime.now(UTC),
                updated_at=parse_backup_datetime(file_record.get("updated_at"), "file_resources.updated_at") or datetime.now(UTC),
            )
        )
        valid_file_ids.add(file_id)

    valid_announcement_ids: set[UUID] = set()
    for announcement_record in backup_records(raw_backup, "announcements"):
        announcement_id = parse_uuid(announcement_record.get("id"), "announcements.id")
        db.add(
            Announcement(
                id=announcement_id,
                project_id=project.id,
                title=str(announcement_record.get("title") or "Imported announcement")[:200],
                body=str(announcement_record.get("body") or ""),
                is_pinned=bool(announcement_record.get("is_pinned", False)),
                deadline_date=parse_backup_date(announcement_record.get("deadline_date"), "announcements.deadline_date", required=False),
                deadline_done_at=parse_backup_datetime(announcement_record.get("deadline_done_at"), "announcements.deadline_done_at", required=False),
                is_record_only=bool(announcement_record.get("is_record_only", False)),
                created_by_user_id=mapped_user_id(announcement_record.get("created_by_user_id"), "announcements.created_by_user_id", fallback=user.id),
                created_at=parse_backup_datetime(announcement_record.get("created_at"), "announcements.created_at") or datetime.now(UTC),
                updated_at=parse_backup_datetime(announcement_record.get("updated_at"), "announcements.updated_at") or datetime.now(UTC),
            )
        )
        valid_announcement_ids.add(announcement_id)

    await db.flush()

    seen_assignees: set[tuple[UUID, UUID]] = set()
    for assignee_record in backup_records(raw_backup, "task_assignees"):
        task_id = parse_uuid(assignee_record.get("task_id"), "task_assignees.task_id")
        assignee_user_id = mapped_user_id(assignee_record.get("user_id"), "task_assignees.user_id")
        if task_id not in valid_task_ids or (task_id, assignee_user_id) in seen_assignees:
            continue
        db.add(
            TaskAssignee(
                id=parse_uuid(assignee_record.get("id"), "task_assignees.id"),
                task_id=task_id,
                user_id=assignee_user_id,
                status=str(assignee_record.get("status") or "todo")[:24],
                completed_at=parse_backup_datetime(assignee_record.get("completed_at"), "task_assignees.completed_at", required=False),
                created_at=parse_backup_datetime(assignee_record.get("created_at"), "task_assignees.created_at") or datetime.now(UTC),
                updated_at=parse_backup_datetime(assignee_record.get("updated_at"), "task_assignees.updated_at") or datetime.now(UTC),
            )
        )
        seen_assignees.add((task_id, assignee_user_id))

    seen_file_links: set[tuple[UUID, UUID]] = set()
    for link_record in backup_records(raw_backup, "task_file_links"):
        task_id = parse_uuid(link_record.get("task_id"), "task_file_links.task_id")
        file_id = parse_uuid(link_record.get("file_resource_id"), "task_file_links.file_resource_id")
        if task_id not in valid_task_ids or file_id not in valid_file_ids or (task_id, file_id) in seen_file_links:
            continue
        db.add(
            TaskFileLink(
                id=parse_uuid(link_record.get("id"), "task_file_links.id"),
                task_id=task_id,
                file_resource_id=file_id,
                created_at=parse_backup_datetime(link_record.get("created_at"), "task_file_links.created_at") or datetime.now(UTC),
            )
        )
        seen_file_links.add((task_id, file_id))

    seen_reads: set[tuple[UUID, UUID]] = set()
    for read_record in backup_records(raw_backup, "announcement_reads"):
        announcement_id = parse_uuid(read_record.get("announcement_id"), "announcement_reads.announcement_id")
        read_user_id = mapped_user_id(read_record.get("user_id"), "announcement_reads.user_id")
        if announcement_id not in valid_announcement_ids or (announcement_id, read_user_id) in seen_reads:
            continue
        db.add(
            AnnouncementRead(
                id=parse_uuid(read_record.get("id"), "announcement_reads.id"),
                announcement_id=announcement_id,
                user_id=read_user_id,
                read_at=parse_backup_datetime(read_record.get("read_at"), "announcement_reads.read_at") or datetime.now(UTC),
            )
        )
        seen_reads.add((announcement_id, read_user_id))

    for notification_record in backup_records(raw_backup, "notifications"):
        db.add(
            Notification(
                id=parse_uuid(notification_record.get("id"), "notifications.id"),
                user_id=mapped_user_id(notification_record.get("user_id"), "notifications.user_id"),
                project_id=project.id,
                kind=str(notification_record.get("kind") or "project.imported")[:40],
                title=str(notification_record.get("title") or "Imported notification")[:240],
                body=str(notification_record["body"]) if notification_record.get("body") is not None else None,
                target_path=str(notification_record["target_path"])[:1024] if notification_record.get("target_path") is not None else None,
                is_email_backed=bool(notification_record.get("is_email_backed", False)),
                read_at=parse_backup_datetime(notification_record.get("read_at"), "notifications.read_at", required=False),
                created_at=parse_backup_datetime(notification_record.get("created_at"), "notifications.created_at") or datetime.now(UTC),
            )
        )

    await db.commit()
    await db.refresh(project)
    await db.refresh(current_user_membership)
    return await serialize_project(db, project, current_user_membership)


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(
    membership: Annotated[tuple[Project, ProjectMember], Depends(get_project_membership)],
    db: AsyncSession = Depends(get_db),
) -> ProjectResponse:
    project, project_member = membership
    return await serialize_project(db, project, project_member)


@router.patch("/{project_id}", response_model=ProjectResponse)
async def update_project(
    payload: ProjectUpdateRequest,
    membership: Annotated[tuple[Project, ProjectMember], Depends(get_project_membership)],
    db: AsyncSession = Depends(get_db),
) -> ProjectResponse:
    project, project_member = membership
    require_project_leader(project_member)

    if payload.name is not None:
        next_name = payload.name.strip()
        if not next_name:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Workspace name is required")
        project.name = next_name

    await db.commit()
    await db.refresh(project)
    return await serialize_project(db, project, project_member)


@router.post("/{project_id}/archive", response_model=ProjectResponse)
async def archive_project(
    payload: ProjectArchiveRequest,
    membership: Annotated[tuple[Project, ProjectMember], Depends(get_project_membership)],
    db: AsyncSession = Depends(get_db),
) -> ProjectResponse:
    project, project_member = membership
    require_project_leader(project_member)
    if payload.confirm_archive:
        project.archived_at = project.archived_at or datetime.now(UTC)

    await db.commit()
    await db.refresh(project)
    return await serialize_project(db, project, project_member)


@router.get("/{project_id}/export")
async def export_project_backup(
    user: Annotated[User, Depends(get_current_user)],
    membership: Annotated[tuple[Project, ProjectMember], Depends(get_project_membership)],
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    project, project_member = membership
    require_project_leader(project_member)

    member_result = await db.execute(select(ProjectMember).where(ProjectMember.project_id == project.id).order_by(ProjectMember.joined_at.asc()))
    members = list(member_result.scalars().all())

    task_result = await db.execute(select(Task).where(Task.project_id == project.id).order_by(Task.created_at.asc()))
    tasks = list(task_result.scalars().all())
    task_ids = [task.id for task in tasks]

    if task_ids:
        assignee_result = await db.execute(select(TaskAssignee).where(TaskAssignee.task_id.in_(task_ids)).order_by(TaskAssignee.created_at.asc()))
        task_assignees = list(assignee_result.scalars().all())
        link_result = await db.execute(select(TaskFileLink).where(TaskFileLink.task_id.in_(task_ids)).order_by(TaskFileLink.created_at.asc()))
        task_file_links = list(link_result.scalars().all())
    else:
        task_assignees = []
        task_file_links = []

    file_result = await db.execute(select(FileResource).where(FileResource.project_id == project.id).order_by(FileResource.created_at.asc()))
    files = list(file_result.scalars().all())

    announcement_result = await db.execute(select(Announcement).where(Announcement.project_id == project.id).order_by(Announcement.created_at.asc()))
    announcements = list(announcement_result.scalars().all())
    announcement_ids = [announcement.id for announcement in announcements]

    if announcement_ids:
        read_result = await db.execute(select(AnnouncementRead).where(AnnouncementRead.announcement_id.in_(announcement_ids)).order_by(AnnouncementRead.read_at.asc()))
        announcement_reads = list(read_result.scalars().all())
    else:
        announcement_reads = []

    notification_result = await db.execute(select(Notification).where(Notification.project_id == project.id).order_by(Notification.created_at.asc()))
    notifications = list(notification_result.scalars().all())

    user_ids = {project.created_by_user_id, user.id}
    user_ids.update(member.user_id for member in members)
    user_ids.update(task.created_by_user_id for task in tasks)
    user_ids.update(task.reviewed_by_user_id for task in tasks if task.reviewed_by_user_id)
    user_ids.update(assignee.user_id for assignee in task_assignees)
    user_ids.update(file.created_by_user_id for file in files)
    user_ids.update(announcement.created_by_user_id for announcement in announcements)
    user_ids.update(read.user_id for read in announcement_reads)
    user_ids.update(notification.user_id for notification in notifications)

    user_result = await db.execute(select(User).where(User.id.in_(user_ids)).order_by(User.email.asc()))
    users = list(user_result.scalars().all())

    backup = {
        "format": "teamy_project_backup",
        "schema_version": 1,
        "exported_at": datetime.now(UTC),
        "exported_by_user_id": user.id,
        "project": export_record(project, ("id", "name", "description", "teamy_code", "created_by_user_id", "archived_at", "created_at", "updated_at")),
        "users": [
            export_record(user_record, ("id", "email", "full_name", "username", "auth_provider", "avatar_url", "google_avatar_url", "last_online_at", "created_at", "updated_at"))
            for user_record in users
        ],
        "members": [export_record(member, ("id", "project_id", "user_id", "role", "nickname", "joined_at")) for member in members],
        "tasks": [
            export_record(
                task,
                (
                    "id",
                    "project_id",
                    "title",
                    "description",
                    "start_date",
                    "due_date",
                    "status",
                    "is_record_only",
                    "is_private",
                    "personal_kind",
                    "created_by_user_id",
                    "reviewed_by_user_id",
                    "reviewed_at",
                    "review_remarks",
                    "created_at",
                    "updated_at",
                ),
            )
            for task in tasks
        ],
        "task_assignees": [export_record(assignee, ("id", "task_id", "user_id", "status", "completed_at", "created_at", "updated_at")) for assignee in task_assignees],
        "file_resources": [
            export_record(file, ("id", "project_id", "title", "kind", "url", "content_html", "created_by_user_id", "created_at", "updated_at")) for file in files
        ],
        "task_file_links": [export_record(link, ("id", "task_id", "file_resource_id", "created_at")) for link in task_file_links],
        "announcements": [
            export_record(
                announcement,
                (
                    "id",
                    "project_id",
                    "title",
                    "body",
                    "is_pinned",
                    "deadline_date",
                    "deadline_done_at",
                    "is_record_only",
                    "created_by_user_id",
                    "created_at",
                    "updated_at",
                ),
            )
            for announcement in announcements
        ],
        "announcement_reads": [export_record(read, ("id", "announcement_id", "user_id", "read_at")) for read in announcement_reads],
        "notifications": [
            export_record(notification, ("id", "user_id", "project_id", "kind", "title", "body", "target_path", "is_email_backed", "read_at", "created_at"))
            for notification in notifications
        ],
    }
    backup["counts"] = {key: len(value) for key, value in backup.items() if isinstance(value, list)}

    safe_project_name = "-".join(filter(None, ("".join(character.lower() if character.isalnum() else "-" for character in project.name)).split("-"))) or "workspace"
    filename = f"teamy-{safe_project_name}-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}.json"
    return JSONResponse(
        content=jsonable_encoder(backup),
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    payload: ProjectDeleteRequest,
    membership: Annotated[tuple[Project, ProjectMember], Depends(get_project_membership)],
    db: AsyncSession = Depends(get_db),
) -> Response:
    project, project_member = membership
    require_project_leader(project_member)
    if payload.confirm_name != project.name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Workspace name confirmation did not match")

    await db.delete(project)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
