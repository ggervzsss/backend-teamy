from datetime import UTC, datetime
from random import SystemRandom
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Response, status
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
