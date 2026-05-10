from collections import defaultdict
from uuid import UUID

from fastapi import HTTPException, WebSocket, status
from fastapi.encoders import jsonable_encoder
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ProjectMember, User
from app.schemas import ProjectMemberResponse, UserResponse


class TeamConnectionManager:
    def __init__(self) -> None:
        self.active_connections: dict[UUID, set[WebSocket]] = defaultdict(set)

    async def connect(self, project_id: UUID, websocket: WebSocket) -> None:
        await websocket.accept()
        self.active_connections[project_id].add(websocket)

    def disconnect(self, project_id: UUID, websocket: WebSocket) -> None:
        self.active_connections[project_id].discard(websocket)
        if not self.active_connections[project_id]:
            del self.active_connections[project_id]

    async def broadcast_member_joined(self, project_id: UUID, member: ProjectMemberResponse) -> None:
        payload = jsonable_encoder({"event": "team.member_joined", "member": member})
        dead_connections: list[WebSocket] = []
        for websocket in self.active_connections.get(project_id, set()).copy():
            try:
                await websocket.send_json(payload)
            except RuntimeError:
                dead_connections.append(websocket)

        for websocket in dead_connections:
            self.disconnect(project_id, websocket)


manager = TeamConnectionManager()


async def serialize_project_member(db: AsyncSession, member: ProjectMember) -> ProjectMemberResponse:
    user = await db.get(User, member.user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Project member user could not be loaded")

    return ProjectMemberResponse(
        id=member.id,
        user=UserResponse.model_validate(user),
        role=member.role,
        joined_at=member.joined_at,
    )


async def broadcast_member_joined(db: AsyncSession, project_id: UUID, member: ProjectMember) -> None:
    await manager.broadcast_member_joined(project_id, await serialize_project_member(db, member))
