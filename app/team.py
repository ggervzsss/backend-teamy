from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect, status
from sqlalchemy import select

from app.config import get_settings
from app.database import SessionLocal, get_db
from app.dependencies import get_current_user
from app.models import Project, ProjectMember, User
from app.projects import get_project_membership, require_project_active
from app.schemas import ProjectMemberNicknameUpdateRequest, ProjectMemberResponse, ProjectPresenceResponse, TeamSocketTicketResponse
from app.security import create_team_socket_ticket, decode_session_token, decode_team_socket_ticket
from app.team_realtime import broadcast_presence, manager, serialize_project_member, serialize_project_presence_members, touch_user_last_online

router = APIRouter(prefix="/projects/{project_id}/members", tags=["team"])


@router.get("/ws-ticket", response_model=TeamSocketTicketResponse)
async def create_team_socket_ticket_endpoint(
    user: Annotated[User, Depends(get_current_user)],
    membership: Annotated[tuple[Project, ProjectMember], Depends(get_project_membership)],
    settings=Depends(get_settings),
) -> TeamSocketTicketResponse:
    project, _ = membership
    return TeamSocketTicketResponse(ticket=create_team_socket_ticket(user.id, project.id, settings))


@router.get("/presence", response_model=ProjectPresenceResponse)
async def list_project_presence(
    membership: Annotated[tuple[Project, ProjectMember], Depends(get_project_membership)],
    db=Depends(get_db),
) -> ProjectPresenceResponse:
    project, _ = membership
    return ProjectPresenceResponse(members=await serialize_project_presence_members(db, project.id))


@router.patch("/{member_id}/nickname", response_model=ProjectMemberResponse)
async def update_project_member_nickname(
    member_id: UUID,
    payload: ProjectMemberNicknameUpdateRequest,
    user: Annotated[User, Depends(get_current_user)],
    membership: Annotated[tuple[Project, ProjectMember], Depends(get_project_membership)],
    db=Depends(get_db),
) -> ProjectMemberResponse:
    project, current_member = membership
    require_project_active(project)
    result = await db.execute(select(ProjectMember).where(ProjectMember.project_id == project.id, ProjectMember.id == member_id))
    member = result.scalar_one_or_none()
    if member is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project member not found")
    if current_member.role != "leader" and member.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only project leaders or the member can change this nickname")

    nickname = payload.nickname.strip() if payload.nickname is not None else ""
    if nickname and (nickname != payload.nickname or nickname[0].isspace() or nickname[-1].isspace()):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Nickname cannot start or end with spaces")
    member.nickname = nickname or None
    await db.commit()
    await db.refresh(member)

    response = await serialize_project_member(db, member)
    await manager.broadcast(project.id, {"event": "team.member_updated", "member": response})
    await broadcast_presence(db, project.id)
    return response


@router.websocket("/ws")
async def team_updates(websocket: WebSocket, project_id: UUID) -> None:
    settings = get_settings()
    ticket = websocket.query_params.get("ticket")

    if ticket:
        try:
            user_id, ticket_project_id = decode_team_socket_ticket(ticket, settings)
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

    await manager.connect(project_id, user_id, websocket)
    async with SessionLocal() as db:
        await broadcast_presence(db, project_id)

    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        disconnected = manager.disconnect(websocket)
        if disconnected is not None:
            disconnected_project_id, disconnected_user_id, became_offline = disconnected
            async with SessionLocal() as db:
                if became_offline:
                    await touch_user_last_online(db, disconnected_user_id)
                await broadcast_presence(db, disconnected_project_id)
