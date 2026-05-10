from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class SignupRequest(BaseModel):
    full_name: str = Field(min_length=1, max_length=160)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: EmailStr
    full_name: str
    avatar_url: str | None = None


class AuthResponse(BaseModel):
    user: UserResponse


class ProjectCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=2000)


class ProjectJoinRequest(BaseModel):
    teamy_code: str = Field(min_length=6, max_length=32)


class ProjectResponse(BaseModel):
    id: UUID
    name: str
    description: str | None = None
    teamy_code: str
    role: str
    member_count: int


class ProjectListResponse(BaseModel):
    projects: list[ProjectResponse]
