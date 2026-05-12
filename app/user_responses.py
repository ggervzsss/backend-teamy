from app.models import ProjectMember, User
from app.schemas import UserResponse


def serialize_account_user(user: User) -> UserResponse:
    return UserResponse.model_validate(user).model_copy(update={"username": None})


def serialize_project_user(user: User, member: ProjectMember | None) -> UserResponse:
    nickname = member.nickname if member is not None else None
    return UserResponse.model_validate(user).model_copy(update={"username": nickname})
