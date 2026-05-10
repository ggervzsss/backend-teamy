from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from app.config import get_settings
from app.database import SessionLocal
from app.dependencies import get_current_user
from app.models import Project, ProjectMember, User
from app.projects import get_project_membership
from app.schemas import TeamSocketTicketResponse
from app.security import create_team_socket_ticket, decode_session_token, decode_team_socket_ticket
from app.team_realtime import manager

router = APIRouter(prefix="/projects/{project_id}/members", tags=["team"])


@router.get("/ws-ticket", response_model=TeamSocketTicketResponse)
async def create_team_socket_ticket_endpoint(
    user: Annotated[User, Depends(get_current_user)],
    membership: Annotated[tuple[Project, ProjectMember], Depends(get_project_membership)],
    settings=Depends(get_settings),
) -> TeamSocketTicketResponse:
    project, _ = membership
    return TeamSocketTicketResponse(ticket=create_team_socket_ticket(user.id, project.id, settings))


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

    await manager.connect(project_id, websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(project_id, websocket)
