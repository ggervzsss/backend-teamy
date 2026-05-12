from collections import defaultdict
from datetime import UTC, datetime
from uuid import UUID

from fastapi import HTTPException, WebSocket, status
from fastapi.encoders import jsonable_encoder
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ProjectMember, User
from app.schemas import ProjectMemberPresenceResponse, ProjectMemberResponse
from app.user_responses import serialize_project_user


class TeamConnectionManager:
    def __init__(self) -> None:
        self.active_connections: dict[UUID, set[WebSocket]] = defaultdict(set)
        self.connection_users: dict[int, tuple[UUID, UUID]] = {}
        self.project_user_connection_counts: dict[UUID, dict[UUID, int]] = defaultdict(lambda: defaultdict(int))

    async def connect(self, project_id: UUID, user_id: UUID, websocket: WebSocket) -> None:
        await websocket.accept()
        self.active_connections[project_id].add(websocket)
        self.connection_users[id(websocket)] = (project_id, user_id)
        self.project_user_connection_counts[project_id][user_id] += 1

    def disconnect(self, websocket: WebSocket) -> tuple[UUID, UUID, bool] | None:
        connection = self.connection_users.pop(id(websocket), None)
        if connection is None:
            return None

        project_id, user_id = connection
        self.active_connections[project_id].discard(websocket)
        if not self.active_connections[project_id]:
            del self.active_connections[project_id]

        user_counts = self.project_user_connection_counts[project_id]
        user_counts[user_id] -= 1
        became_offline = user_counts[user_id] <= 0
        if became_offline:
            del user_counts[user_id]
        if not user_counts:
            del self.project_user_connection_counts[project_id]
        return project_id, user_id, became_offline

    def is_user_online(self, project_id: UUID, user_id: UUID) -> bool:
        return self.project_user_connection_counts.get(project_id, {}).get(user_id, 0) > 0

    async def broadcast_member_joined(self, project_id: UUID, member: ProjectMemberResponse) -> None:
        payload = jsonable_encoder({"event": "team.member_joined", "member": member})
        await self.broadcast(project_id, payload)

    async def broadcast_presence(self, project_id: UUID, members: list[ProjectMemberPresenceResponse]) -> None:
        payload = jsonable_encoder({"event": "team.presence", "members": members})
        await self.broadcast(project_id, payload)

    async def send_presence(self, websocket: WebSocket, members: list[ProjectMemberPresenceResponse]) -> None:
        await websocket.send_json(jsonable_encoder({"event": "team.presence", "members": members}))

    async def broadcast(self, project_id: UUID, payload: dict) -> None:
        encoded_payload = jsonable_encoder(payload)
        dead_connections: list[WebSocket] = []
        for websocket in self.active_connections.get(project_id, set()).copy():
            try:
                await websocket.send_json(encoded_payload)
            except RuntimeError:
                dead_connections.append(websocket)

        for websocket in dead_connections:
            self.disconnect(websocket)


manager = TeamConnectionManager()


async def serialize_project_member(db: AsyncSession, member: ProjectMember) -> ProjectMemberResponse:
    user = await db.get(User, member.user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Project member user could not be loaded")

    return ProjectMemberResponse(
        id=member.id,
        user=serialize_project_user(user, member),
        role=member.role,
        nickname=member.nickname,
        joined_at=member.joined_at,
    )


async def serialize_project_presence_members(db: AsyncSession, project_id: UUID) -> list[ProjectMemberPresenceResponse]:
    result = await db.execute(
        select(ProjectMember, User)
        .join(User, User.id == ProjectMember.user_id)
        .where(ProjectMember.project_id == project_id)
        .order_by(ProjectMember.role.asc(), User.full_name.asc(), User.email.asc())
    )
    return [
        ProjectMemberPresenceResponse(
            id=member.id,
            user=serialize_project_user(user, member),
            role=member.role,
            nickname=member.nickname,
            joined_at=member.joined_at,
            is_online=manager.is_user_online(project_id, user.id),
            last_online_at=user.last_online_at,
        )
        for member, user in result.all()
    ]


async def touch_user_last_online(db: AsyncSession, user_id: UUID) -> None:
    user = await db.get(User, user_id)
    if user is not None:
        user.last_online_at = datetime.now(UTC)
        await db.commit()


async def broadcast_presence(db: AsyncSession, project_id: UUID) -> None:
    await manager.broadcast_presence(project_id, await serialize_project_presence_members(db, project_id))


async def broadcast_member_joined(db: AsyncSession, project_id: UUID, member: ProjectMember) -> None:
    await manager.broadcast_member_joined(project_id, await serialize_project_member(db, member))
