from app.models import ProjectMember, User
from app.schemas import UserResponse
from app.time_utils import ensure_utc


def serialize_account_user(user: User) -> UserResponse:
    return UserResponse.model_validate(user).model_copy(update={"username": None, "last_online_at": ensure_utc(user.last_online_at)})


def serialize_project_user(user: User, member: ProjectMember | None) -> UserResponse:
    nickname = member.nickname if member is not None else None
    return UserResponse.model_validate(user).model_copy(update={"username": nickname, "last_online_at": ensure_utc(user.last_online_at)})
