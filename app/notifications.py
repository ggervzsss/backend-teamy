import html
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Annotated
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.database import get_db
from app.models import Announcement, Project, ProjectMember, Task, TaskAssignee, User

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/notifications", tags=["notifications"])


@dataclass(frozen=True)
class EmailRecipient:
    email: str
    full_name: str


class ReminderRunResponse(BaseModel):
    target_dates: list[date]
    task_reminders_queued: int
    announcement_reminders_queued: int


def get_frontend_url(settings: Settings, project_id: UUID, path: str) -> str:
    base_url = str(settings.frontend_url).rstrip("/")
    return f"{base_url}/projects/{project_id}{path}"


def format_date(value: date | None) -> str:
    if value is None:
        return "No due date"
    return value.strftime("%b %d, %Y")


def normalize_recipients(recipients: list[EmailRecipient]) -> list[EmailRecipient]:
    deduped: dict[str, EmailRecipient] = {}
    for recipient in recipients:
        email = recipient.email.strip().lower()
        if email and email not in deduped:
            deduped[email] = EmailRecipient(email=email, full_name=recipient.full_name.strip() or email)
    return list(deduped.values())


async def send_email(settings: Settings, recipients: list[EmailRecipient], subject: str, html_body: str, text_body: str) -> None:
    if not settings.resend_api_key:
        logger.info("Skipping email notification because RESEND_API_KEY is not configured")
        return

    unique_recipients = normalize_recipients(recipients)
    if not unique_recipients:
        return

    headers = {"Authorization": f"Bearer {settings.resend_api_key}", "Content-Type": "application/json"}
    timeout = httpx.Timeout(10.0, connect=5.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        for recipient in unique_recipients:
            payload = {
                "from": settings.resend_from_email,
                "to": [recipient.email],
                "subject": subject,
                "html": html_body,
                "text": text_body,
            }
            try:
                response = await client.post("https://api.resend.com/emails", headers=headers, json=payload)
                response.raise_for_status()
            except httpx.HTTPError:
                logger.exception("Could not send Teamy notification email to %s", recipient.email)


async def get_project_member_recipients(db: AsyncSession, project_id: UUID) -> list[EmailRecipient]:
    result = await db.execute(
        select(User)
        .join(ProjectMember, ProjectMember.user_id == User.id)
        .where(ProjectMember.project_id == project_id)
        .order_by(User.full_name.asc(), User.email.asc())
    )
    return [EmailRecipient(email=user.email, full_name=user.full_name) for user in result.scalars().all()]


async def get_project_leader_recipients(db: AsyncSession, project_id: UUID) -> list[EmailRecipient]:
    result = await db.execute(
        select(User)
        .join(ProjectMember, ProjectMember.user_id == User.id)
        .where(ProjectMember.project_id == project_id, ProjectMember.role == "leader")
        .order_by(User.full_name.asc(), User.email.asc())
    )
    return [EmailRecipient(email=user.email, full_name=user.full_name) for user in result.scalars().all()]


async def get_user_recipients(db: AsyncSession, user_ids: set[UUID]) -> list[EmailRecipient]:
    if not user_ids:
        return []
    result = await db.execute(select(User).where(User.id.in_(user_ids)).order_by(User.full_name.asc(), User.email.asc()))
    return [EmailRecipient(email=user.email, full_name=user.full_name) for user in result.scalars().all()]


def build_email_shell(title: str, body: str, action_url: str, action_label: str = "Open in Teamy") -> str:
    safe_title = html.escape(title)
    safe_body = body
    safe_url = html.escape(action_url, quote=True)
    safe_label = html.escape(action_label)
    return f"""
    <div style="font-family:Arial,sans-serif;color:#131317;line-height:1.5">
      <h1 style="font-size:22px;margin:0 0 12px">{safe_title}</h1>
      <div style="font-size:15px;margin:0 0 20px">{safe_body}</div>
      <a href="{safe_url}" style="display:inline-block;background:#131317;color:#fff;text-decoration:none;padding:10px 14px;border-radius:8px">{safe_label}</a>
    </div>
    """


async def send_task_assignment_email(
    settings: Settings,
    recipients: list[EmailRecipient],
    project_id: UUID,
    project_name: str,
    task_title: str,
    due_date: date | None,
) -> None:
    task_url = get_frontend_url(settings, project_id, "/task-board")
    safe_project = html.escape(project_name)
    safe_task = html.escape(task_title)
    safe_due = html.escape(format_date(due_date))
    body = f"<p>You have been assigned to <strong>{safe_task}</strong> in {safe_project}.</p><p>Due date: {safe_due}</p>"
    text = f"You have been assigned to {task_title} in {project_name}. Due date: {format_date(due_date)}. Open: {task_url}"
    await send_email(settings, recipients, f"New task assigned: {task_title}", build_email_shell("New task assignment", body, task_url), text)


async def send_announcement_email(
    settings: Settings,
    recipients: list[EmailRecipient],
    project_id: UUID,
    project_name: str,
    announcement_title: str,
    body_text: str,
    deadline_date: date | None,
) -> None:
    announcement_url = get_frontend_url(settings, project_id, "/announcements")
    safe_project = html.escape(project_name)
    safe_title = html.escape(announcement_title)
    safe_body = html.escape(body_text).replace("\n", "<br>")
    deadline = f"<p>Scheduled date: {html.escape(format_date(deadline_date))}</p>" if deadline_date else ""
    body = f"<p>A new announcement was posted in {safe_project}: <strong>{safe_title}</strong>.</p><p>{safe_body}</p>{deadline}"
    text = f"New announcement in {project_name}: {announcement_title}\n\n{body_text}\nOpen: {announcement_url}"
    await send_email(settings, recipients, f"New announcement: {announcement_title}", build_email_shell("New announcement", body, announcement_url), text)


async def send_task_ready_for_review_email(
    settings: Settings,
    recipients: list[EmailRecipient],
    project_id: UUID,
    project_name: str,
    task_title: str,
) -> None:
    task_url = get_frontend_url(settings, project_id, "/task-board")
    body = f"<p>All assignees have marked <strong>{html.escape(task_title)}</strong> as ready for review in {html.escape(project_name)}.</p>"
    text = f"All assignees have marked {task_title} as ready for review in {project_name}. Open: {task_url}"
    await send_email(settings, recipients, f"Task ready for review: {task_title}", build_email_shell("Task ready for review", body, task_url), text)


async def send_task_changes_requested_email(
    settings: Settings,
    recipients: list[EmailRecipient],
    project_id: UUID,
    project_name: str,
    task_title: str,
    remarks: str | None,
) -> None:
    task_url = get_frontend_url(settings, project_id, "/task-board")
    remarks_html = f"<p><strong>Remarks:</strong><br>{html.escape(remarks).replace(chr(10), '<br>')}</p>" if remarks else ""
    body = f"<p><strong>{html.escape(task_title)}</strong> in {html.escape(project_name)} needs revisions or additional changes.</p>{remarks_html}"
    text = f"{task_title} in {project_name} needs revisions or additional changes."
    if remarks:
        text = f"{text}\n\nRemarks: {remarks}"
    text = f"{text}\nOpen: {task_url}"
    await send_email(settings, recipients, f"Changes requested: {task_title}", build_email_shell("Changes requested", body, task_url), text)


async def send_task_due_reminder_email(
    settings: Settings,
    recipient: EmailRecipient,
    project_id: UUID,
    project_name: str,
    task_title: str,
    due_date: date,
) -> None:
    task_url = get_frontend_url(settings, project_id, "/task-board")
    body = f"<p><strong>{html.escape(task_title)}</strong> in {html.escape(project_name)} is due on {html.escape(format_date(due_date))}.</p>"
    text = f"Reminder: {task_title} in {project_name} is due on {format_date(due_date)}. Open: {task_url}"
    await send_email(settings, [recipient], f"Task due reminder: {task_title}", build_email_shell("Task due reminder", body, task_url), text)


async def send_announcement_reminder_email(
    settings: Settings,
    recipient: EmailRecipient,
    project_id: UUID,
    project_name: str,
    announcement_title: str,
    deadline_date: date,
) -> None:
    announcement_url = get_frontend_url(settings, project_id, "/announcements")
    body = f"<p><strong>{html.escape(announcement_title)}</strong> in {html.escape(project_name)} is scheduled for {html.escape(format_date(deadline_date))}.</p>"
    text = f"Reminder: {announcement_title} in {project_name} is scheduled for {format_date(deadline_date)}. Open: {announcement_url}"
    await send_email(settings, [recipient], f"Announcement reminder: {announcement_title}", build_email_shell("Announcement reminder", body, announcement_url), text)


def get_reminder_dates(settings: Settings) -> list[date]:
    try:
        timezone = ZoneInfo(settings.notification_reminder_timezone)
    except ZoneInfoNotFoundError:
        logger.warning("Invalid notification reminder timezone %s; falling back to UTC", settings.notification_reminder_timezone)
        timezone = ZoneInfo("UTC")
    today = datetime.now(timezone).date()
    return [today, today + timedelta(days=1)]


@router.post("/reminders/due", response_model=ReminderRunResponse)
async def queue_due_reminders(
    background_tasks: BackgroundTasks,
    x_teamy_reminder_secret: Annotated[str | None, Header(alias="X-Teamy-Reminder-Secret")] = None,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> ReminderRunResponse:
    if not settings.notification_reminder_secret:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Notification reminder secret is not configured")
    if x_teamy_reminder_secret != settings.notification_reminder_secret:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid notification reminder secret")

    target_dates = get_reminder_dates(settings)
    task_result = await db.execute(
        select(Task, Project, User)
        .join(Project, Project.id == Task.project_id)
        .join(TaskAssignee, TaskAssignee.task_id == Task.id)
        .join(User, User.id == TaskAssignee.user_id)
        .where(Task.due_date.in_(target_dates), Task.status != "done", Project.archived_at.is_(None))
        .order_by(Task.due_date.asc(), Project.name.asc(), Task.title.asc(), User.email.asc())
    )
    task_count = 0
    for task, project, user in task_result.all():
        if task.due_date is None:
            continue
        task_count += 1
        background_tasks.add_task(
            send_task_due_reminder_email,
            settings,
            EmailRecipient(email=user.email, full_name=user.full_name),
            project.id,
            project.name,
            task.title,
            task.due_date,
        )

    announcement_result = await db.execute(
        select(Announcement, Project, User)
        .join(Project, Project.id == Announcement.project_id)
        .join(ProjectMember, ProjectMember.project_id == Project.id)
        .join(User, User.id == ProjectMember.user_id)
        .where(Announcement.deadline_date.in_(target_dates), Project.archived_at.is_(None))
        .order_by(Announcement.deadline_date.asc(), Project.name.asc(), Announcement.title.asc(), User.email.asc())
    )
    announcement_count = 0
    for announcement, project, user in announcement_result.all():
        if announcement.deadline_date is None:
            continue
        announcement_count += 1
        background_tasks.add_task(
            send_announcement_reminder_email,
            settings,
            EmailRecipient(email=user.email, full_name=user.full_name),
            project.id,
            project.name,
            announcement.title,
            announcement.deadline_date,
        )

    return ReminderRunResponse(
        target_dates=target_dates,
        task_reminders_queued=task_count,
        announcement_reminders_queued=announcement_count,
    )
