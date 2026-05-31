from datetime import UTC, date, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.announcements import serialize_announcements_batch
from app.database import get_db
from app.dependencies import get_current_user
from app.models import Announcement, FileResource, Project, ProjectMember, Task, TaskAssignee, User
from app.projects import get_project_membership
from app.schemas import DashboardActivityItemResponse, DashboardDeadlineItemResponse, DashboardSummaryResponse
from app.tasks import serialize_tasks_batch

router = APIRouter(prefix="/projects/{project_id}/dashboard", tags=["dashboard"])


def timestamps_differ(first: datetime, second: datetime) -> bool:
    return abs((second - first).total_seconds()) > 1


def display_name(user: User | None, member: ProjectMember | None = None) -> str:
    if user is None:
        return "Teamy member"
    return (member.nickname if member is not None else None) or user.full_name or user.email


async def get_user_member_maps(db: AsyncSession, project_id: UUID, user_ids: set[UUID]) -> tuple[dict[UUID, User], dict[UUID, ProjectMember]]:
    if not user_ids:
        return {}, {}

    user_result = await db.execute(select(User).where(User.id.in_(user_ids)))
    users_by_id = {user.id: user for user in user_result.scalars().all()}
    member_result = await db.execute(select(ProjectMember).where(ProjectMember.project_id == project_id, ProjectMember.user_id.in_(user_ids)))
    members_by_user_id = {member.user_id: member for member in member_result.scalars().all()}
    return users_by_id, members_by_user_id


async def build_activity_items(
    db: AsyncSession,
    project_id: UUID,
    announcements: list[Announcement],
    tasks: list[Task],
    files: list[FileResource],
) -> list[DashboardActivityItemResponse]:
    user_ids: set[UUID] = set()
    user_ids.update(announcement.created_by_user_id for announcement in announcements)
    user_ids.update(task.created_by_user_id for task in tasks)
    user_ids.update(task.reviewed_by_user_id for task in tasks if task.reviewed_by_user_id)
    user_ids.update(file.created_by_user_id for file in files)
    users_by_id, members_by_user_id = await get_user_member_maps(db, project_id, user_ids)

    activity_items: list[DashboardActivityItemResponse] = []

    for announcement in announcements:
        actor = display_name(users_by_id.get(announcement.created_by_user_id), members_by_user_id.get(announcement.created_by_user_id))
        target_path = f"/projects/{project_id}/announcements"
        activity_items.append(
            DashboardActivityItemResponse(
                id=f"announcement:{announcement.id}:created",
                kind="announcement",
                title=announcement.title,
                description="posted a project announcement",
                actor=actor,
                timestamp=announcement.created_at,
                target_path=target_path,
            )
        )
        if timestamps_differ(announcement.created_at, announcement.updated_at):
            activity_items.append(
                DashboardActivityItemResponse(
                    id=f"announcement:{announcement.id}:updated",
                    kind="announcement",
                    title=announcement.title,
                    description="pinned or updated a project announcement" if announcement.is_pinned else "updated a project announcement",
                    actor=actor,
                    timestamp=announcement.updated_at,
                    target_path=target_path,
                )
            )

    for task in tasks:
        creator = display_name(users_by_id.get(task.created_by_user_id), members_by_user_id.get(task.created_by_user_id))
        latest_actor_id = task.reviewed_by_user_id or task.created_by_user_id
        latest_actor = display_name(users_by_id.get(latest_actor_id), members_by_user_id.get(latest_actor_id))
        target_path = f"/projects/{project_id}/task-board"
        activity_items.append(
            DashboardActivityItemResponse(
                id=f"task:{task.id}:created",
                kind="task",
                title=task.title,
                description="created a task",
                actor=creator,
                timestamp=task.created_at,
                target_path=target_path,
            )
        )
        if task.status == "done":
            activity_items.append(
                DashboardActivityItemResponse(
                    id=f"task:{task.id}:completed",
                    kind="task",
                    title=task.title,
                    description="recorded a completed task" if task.is_record_only else "completed a task",
                    actor=latest_actor,
                    timestamp=task.reviewed_at or task.updated_at,
                    target_path=target_path,
                )
            )
        elif timestamps_differ(task.created_at, task.updated_at):
            activity_items.append(
                DashboardActivityItemResponse(
                    id=f"task:{task.id}:updated",
                    kind="task",
                    title=task.title,
                    description="moved a task to review" if task.status == "for_review" else "updated a task",
                    actor=latest_actor,
                    timestamp=task.updated_at,
                    target_path=target_path,
                )
            )

    for file in files:
        actor = display_name(users_by_id.get(file.created_by_user_id), members_by_user_id.get(file.created_by_user_id))
        target_path = f"/projects/{project_id}/file-hub/{file.id}" if file.kind == "doc" else f"/projects/{project_id}/file-hub"
        activity_items.append(
            DashboardActivityItemResponse(
                id=f"file:{file.id}:created",
                kind="file",
                title=file.title,
                description="created a Teamy Doc" if file.kind == "doc" else "shared a resource link",
                actor=actor,
                timestamp=file.created_at,
                target_path=target_path,
            )
        )
        if timestamps_differ(file.created_at, file.updated_at):
            activity_items.append(
                DashboardActivityItemResponse(
                    id=f"file:{file.id}:updated",
                    kind="file",
                    title=file.title,
                    description="updated a Teamy Doc" if file.kind == "doc" else "updated a resource link",
                    actor=actor,
                    timestamp=file.updated_at,
                    target_path=target_path,
                )
            )

    return sorted(activity_items, key=lambda item: item.timestamp, reverse=True)[:5]


@router.get("", response_model=DashboardSummaryResponse)
async def get_dashboard_summary(
    user: Annotated[User, Depends(get_current_user)],
    membership: Annotated[tuple[Project, ProjectMember], Depends(get_project_membership)],
    db: AsyncSession = Depends(get_db),
) -> DashboardSummaryResponse:
    project, _ = membership
    today = datetime.now(UTC).date()

    active_task_count_result = await db.execute(
        select(func.count()).select_from(Task).where(Task.project_id == project.id, Task.is_private.is_(False), Task.status != "done")
    )
    my_pending_count_result = await db.execute(
        select(func.count())
        .select_from(Task)
        .join(TaskAssignee, TaskAssignee.task_id == Task.id)
        .where(
            Task.project_id == project.id,
            Task.is_private.is_(False),
            Task.status != "done",
            TaskAssignee.user_id == user.id,
            TaskAssignee.status != "ready_for_review",
        )
    )
    review_count_result = await db.execute(
        select(func.count()).select_from(Task).where(Task.project_id == project.id, Task.is_private.is_(False), Task.status == "for_review")
    )

    recent_announcement_result = await db.execute(
        select(Announcement)
        .where(Announcement.project_id == project.id)
        .order_by(Announcement.is_pinned.desc(), Announcement.created_at.desc(), Announcement.updated_at.desc())
        .limit(2)
    )
    recent_announcements = list(recent_announcement_result.scalars().all())

    pending_task_result = await db.execute(
        select(Task)
        .join(TaskAssignee, TaskAssignee.task_id == Task.id)
        .where(
            Task.project_id == project.id,
            Task.is_private.is_(False),
            Task.status != "done",
            TaskAssignee.user_id == user.id,
            TaskAssignee.status != "ready_for_review",
        )
        .order_by(Task.updated_at.desc())
        .limit(20)
    )
    pending_tasks = sorted(
        list(pending_task_result.scalars().unique().all()),
        key=lambda task: (task.due_date is None, task.due_date or date.max, -task.updated_at.timestamp()),
    )[:4]

    review_task_result = await db.execute(
        select(Task)
        .where(Task.project_id == project.id, Task.is_private.is_(False), Task.status == "for_review")
        .order_by(Task.updated_at.desc(), Task.created_at.desc())
        .limit(4)
    )
    tasks_for_review = list(review_task_result.scalars().all())

    task_deadline_result = await db.execute(
        select(Task)
        .where(Task.project_id == project.id, Task.is_private.is_(False), Task.status != "done", Task.due_date.is_not(None))
        .order_by(Task.due_date.asc(), Task.title.asc())
        .limit(20)
    )
    announcement_deadline_result = await db.execute(
        select(Announcement)
        .where(
            Announcement.project_id == project.id,
            Announcement.is_record_only.is_(False),
            Announcement.deadline_done_at.is_(None),
            or_(Announcement.deadline_date.is_(None), Announcement.deadline_date >= today),
        )
        .order_by(Announcement.deadline_date.asc(), Announcement.title.asc())
        .limit(20)
    )
    deadline_items = [
        DashboardDeadlineItemResponse(id=f"task:{task.id}", source_id=task.id, kind="task", title=task.title, due_date=task.due_date)
        for task in task_deadline_result.scalars().all()
    ] + [
        DashboardDeadlineItemResponse(
            id=f"announcement:{announcement.id}",
            source_id=announcement.id,
            kind="announcement",
            title=announcement.title,
            due_date=announcement.deadline_date,
        )
        for announcement in announcement_deadline_result.scalars().all()
    ]
    deadline_items = sorted(deadline_items, key=lambda item: (item.due_date is not None, item.due_date or date.min, item.title))[:4]

    activity_announcement_result = await db.execute(
        select(Announcement).where(Announcement.project_id == project.id).order_by(Announcement.updated_at.desc(), Announcement.created_at.desc()).limit(10)
    )
    activity_task_result = await db.execute(
        select(Task)
        .where(Task.project_id == project.id, Task.is_private.is_(False))
        .order_by(Task.updated_at.desc(), Task.created_at.desc())
        .limit(10)
    )
    activity_file_result = await db.execute(
        select(FileResource).where(FileResource.project_id == project.id).order_by(FileResource.updated_at.desc(), FileResource.created_at.desc()).limit(10)
    )

    return DashboardSummaryResponse(
        recent_announcements=await serialize_announcements_batch(db, recent_announcements, user.id),
        pending_tasks=await serialize_tasks_batch(db, pending_tasks, project.id),
        tasks_for_review=await serialize_tasks_batch(db, tasks_for_review, project.id),
        deadline_items=deadline_items,
        activity_items=await build_activity_items(
            db,
            project.id,
            list(activity_announcement_result.scalars().all()),
            list(activity_task_result.scalars().all()),
            list(activity_file_result.scalars().all()),
        ),
        active_task_count=active_task_count_result.scalar_one(),
        my_pending_task_count=my_pending_count_result.scalar_one(),
        tasks_for_review_count=review_count_result.scalar_one(),
    )
