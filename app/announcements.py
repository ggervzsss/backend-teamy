from collections import defaultdict
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect, status
from fastapi.encoders import jsonable_encoder
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import SessionLocal, get_db
from app.dependencies import get_current_user
from app.models import Announcement, AnnouncementRead, Project, ProjectMember, User
from app.projects import get_project_membership, require_project_active
from app.schemas import (
    AnnouncementCreateRequest,
    AnnouncementListResponse,
    AnnouncementPinRequest,
    AnnouncementResponse,
    AnnouncementSocketTicketResponse,
    AnnouncementUpdateRequest,
    UserResponse,
)
from app.security import create_announcement_socket_ticket, decode_announcement_socket_ticket, decode_session_token

router = APIRouter(prefix="/projects/{project_id}/announcements", tags=["announcements"])


class AnnouncementConnectionManager:
    def __init__(self) -> None:
        self.active_connections: dict[UUID, set[WebSocket]] = defaultdict(set)

    async def connect(self, project_id: UUID, websocket: WebSocket) -> None:
        await websocket.accept()
        self.active_connections[project_id].add(websocket)

    def disconnect(self, project_id: UUID, websocket: WebSocket) -> None:
        self.active_connections[project_id].discard(websocket)
        if not self.active_connections[project_id]:
            del self.active_connections[project_id]

    async def broadcast(self, project_id: UUID, payload: dict) -> None:
        encoded_payload = jsonable_encoder(payload)
        dead_connections: list[WebSocket] = []
        for websocket in self.active_connections.get(project_id, set()).copy():
            try:
                await websocket.send_json(encoded_payload)
            except RuntimeError:
                dead_connections.append(websocket)

        for websocket in dead_connections:
            self.disconnect(project_id, websocket)


manager = AnnouncementConnectionManager()


async def has_read_announcement(db: AsyncSession, announcement_id: UUID, user_id: UUID) -> bool:
    result = await db.execute(
        select(AnnouncementRead.id).where(AnnouncementRead.announcement_id == announcement_id, AnnouncementRead.user_id == user_id)
    )
    return result.scalar_one_or_none() is not None


async def mark_announcement_read(db: AsyncSession, announcement_id: UUID, user_id: UUID) -> tuple[bool, datetime]:
    result = await db.execute(
        select(AnnouncementRead).where(AnnouncementRead.announcement_id == announcement_id, AnnouncementRead.user_id == user_id)
    )
    existing_read = result.scalar_one_or_none()
    if existing_read is not None:
        return False, existing_read.read_at

    read = AnnouncementRead(announcement_id=announcement_id, user_id=user_id)
    db.add(read)
    await db.flush()
    read_at = read.read_at or datetime.now(UTC)
    return True, read_at


async def serialize_announcement(db: AsyncSession, announcement: Announcement, user_id: UUID, is_read: bool | None = None) -> AnnouncementResponse:
    creator = await db.get(User, announcement.created_by_user_id)
    if creator is None:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Announcement creator could not be loaded")

    return AnnouncementResponse(
        id=announcement.id,
        project_id=announcement.project_id,
        title=announcement.title,
        body=announcement.body,
        is_pinned=announcement.is_pinned,
        deadline_date=announcement.deadline_date,
        is_read=await has_read_announcement(db, announcement.id, user_id) if is_read is None else is_read,
        created_by=UserResponse.model_validate(creator),
        created_at=announcement.created_at,
        updated_at=announcement.updated_at,
    )


async def get_announcement_for_project(db: AsyncSession, project_id: UUID, announcement_id: UUID) -> Announcement:
    result = await db.execute(select(Announcement).where(Announcement.project_id == project_id, Announcement.id == announcement_id))
    announcement = result.scalar_one_or_none()
    if announcement is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Announcement not found")
    return announcement


def require_announcement_manager(project_member: ProjectMember, announcement: Announcement, user: User) -> None:
    if project_member.role != "leader" and announcement.created_by_user_id != user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only the announcement creator or project leader can edit this announcement")


@router.get("", response_model=AnnouncementListResponse)
async def list_announcements(
    user: Annotated[User, Depends(get_current_user)],
    membership: Annotated[tuple[Project, ProjectMember], Depends(get_project_membership)],
    db: AsyncSession = Depends(get_db),
) -> AnnouncementListResponse:
    project, _ = membership
    result = await db.execute(
        select(Announcement)
        .where(Announcement.project_id == project.id)
        .order_by(Announcement.is_pinned.desc(), Announcement.created_at.desc(), Announcement.updated_at.desc())
    )
    return AnnouncementListResponse(
        announcements=[await serialize_announcement(db, announcement, user.id) for announcement in result.scalars().all()]
    )


@router.get("/ws-ticket", response_model=AnnouncementSocketTicketResponse)
async def create_announcement_socket_ticket_endpoint(
    user: Annotated[User, Depends(get_current_user)],
    membership: Annotated[tuple[Project, ProjectMember], Depends(get_project_membership)],
    settings=Depends(get_settings),
) -> AnnouncementSocketTicketResponse:
    project, _ = membership
    return AnnouncementSocketTicketResponse(ticket=create_announcement_socket_ticket(user.id, project.id, settings))


@router.post("", response_model=AnnouncementResponse, status_code=status.HTTP_201_CREATED)
async def create_announcement(
    payload: AnnouncementCreateRequest,
    user: Annotated[User, Depends(get_current_user)],
    membership: Annotated[tuple[Project, ProjectMember], Depends(get_project_membership)],
    db: AsyncSession = Depends(get_db),
) -> AnnouncementResponse:
    project, _ = membership
    require_project_active(project)
    title = payload.title.strip()
    body = payload.body.strip()
    if not title or not body:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Title and body are required")

    announcement = Announcement(
        project_id=project.id,
        title=title,
        body=body,
        is_pinned=payload.is_pinned,
        deadline_date=payload.deadline_date,
        created_by_user_id=user.id,
    )
    db.add(announcement)
    await db.flush()
    await mark_announcement_read(db, announcement.id, user.id)
    await db.commit()
    await db.refresh(announcement)

    response = await serialize_announcement(db, announcement, user.id, is_read=True)
    broadcast_announcement = await serialize_announcement(db, announcement, user.id, is_read=False)
    await manager.broadcast(project.id, {"event": "announcement.created", "announcement": broadcast_announcement})
    return response


@router.patch("/{announcement_id}", response_model=AnnouncementResponse)
async def update_announcement(
    announcement_id: UUID,
    payload: AnnouncementUpdateRequest,
    user: Annotated[User, Depends(get_current_user)],
    membership: Annotated[tuple[Project, ProjectMember], Depends(get_project_membership)],
    db: AsyncSession = Depends(get_db),
) -> AnnouncementResponse:
    project, project_member = membership
    require_project_active(project)
    announcement = await get_announcement_for_project(db, project.id, announcement_id)
    require_announcement_manager(project_member, announcement, user)

    if payload.title is not None:
        title = payload.title.strip()
        if not title:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Title is required")
        announcement.title = title
    if payload.body is not None:
        body = payload.body.strip()
        if not body:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Body is required")
        announcement.body = body
    if payload.is_pinned is not None:
        announcement.is_pinned = payload.is_pinned
    if "deadline_date" in payload.model_fields_set:
        announcement.deadline_date = payload.deadline_date

    await db.commit()
    await db.refresh(announcement)

    response = await serialize_announcement(db, announcement, user.id)
    broadcast_announcement = await serialize_announcement(db, announcement, user.id, is_read=False)
    await manager.broadcast(project.id, {"event": "announcement.updated", "announcement": broadcast_announcement})
    return response


@router.get("/{announcement_id}", response_model=AnnouncementResponse)
async def get_announcement(
    announcement_id: UUID,
    user: Annotated[User, Depends(get_current_user)],
    membership: Annotated[tuple[Project, ProjectMember], Depends(get_project_membership)],
    db: AsyncSession = Depends(get_db),
) -> AnnouncementResponse:
    project, _ = membership
    require_project_active(project)
    announcement = await get_announcement_for_project(db, project.id, announcement_id)
    was_created, read_at = await mark_announcement_read(db, announcement.id, user.id)
    await db.commit()
    if was_created:
        await manager.broadcast(
            project.id,
            {
                "event": "announcement.read",
                "announcement_id": announcement.id,
                "user_id": user.id,
                "read_at": read_at,
            },
        )
    return await serialize_announcement(db, announcement, user.id, is_read=True)


@router.patch("/{announcement_id}/read", response_model=AnnouncementResponse)
async def mark_announcement_as_read(
    announcement_id: UUID,
    user: Annotated[User, Depends(get_current_user)],
    membership: Annotated[tuple[Project, ProjectMember], Depends(get_project_membership)],
    db: AsyncSession = Depends(get_db),
) -> AnnouncementResponse:
    project, _ = membership
    require_project_active(project)
    announcement = await get_announcement_for_project(db, project.id, announcement_id)
    was_created, read_at = await mark_announcement_read(db, announcement.id, user.id)
    await db.commit()
    if was_created:
        await manager.broadcast(
            project.id,
            {
                "event": "announcement.read",
                "announcement_id": announcement.id,
                "user_id": user.id,
                "read_at": read_at,
            },
        )
    return await serialize_announcement(db, announcement, user.id, is_read=True)


@router.patch("/{announcement_id}/pin", response_model=AnnouncementResponse)
async def update_announcement_pin(
    announcement_id: UUID,
    payload: AnnouncementPinRequest,
    user: Annotated[User, Depends(get_current_user)],
    membership: Annotated[tuple[Project, ProjectMember], Depends(get_project_membership)],
    db: AsyncSession = Depends(get_db),
) -> AnnouncementResponse:
    project, _ = membership
    require_project_active(project)
    announcement = await get_announcement_for_project(db, project.id, announcement_id)
    announcement.is_pinned = payload.is_pinned
    await db.commit()
    await db.refresh(announcement)

    response = await serialize_announcement(db, announcement, user.id)
    broadcast_announcement = await serialize_announcement(db, announcement, user.id, is_read=False)
    await manager.broadcast(project.id, {"event": "announcement.updated", "announcement": broadcast_announcement})
    return response


@router.websocket("/ws")
async def announcement_updates(websocket: WebSocket, project_id: UUID) -> None:
    settings = get_settings()
    ticket = websocket.query_params.get("ticket")

    if ticket:
        try:
            user_id, ticket_project_id = decode_announcement_socket_ticket(ticket, settings)
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
