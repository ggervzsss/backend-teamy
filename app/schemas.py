from datetime import date, datetime
from typing import Literal
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


TaskStatus = Literal["todo", "in_progress", "for_review", "done"]
TaskPriority = Literal["low", "medium", "high"]
AssigneeStatus = Literal["todo", "in_progress", "done"]


class ProjectMemberResponse(BaseModel):
    id: UUID
    user: UserResponse
    role: str
    joined_at: datetime


class ProjectMemberListResponse(BaseModel):
    members: list[ProjectMemberResponse]


class TaskAssigneeResponse(BaseModel):
    id: UUID
    user: UserResponse
    status: AssigneeStatus
    completed_at: datetime | None = None


class TaskResponse(BaseModel):
    id: UUID
    project_id: UUID
    title: str
    description: str | None = None
    priority: TaskPriority
    due_date: date | None = None
    status: TaskStatus
    created_by: UserResponse
    reviewed_by: UserResponse | None = None
    reviewed_at: datetime | None = None
    assignees: list[TaskAssigneeResponse]
    created_at: datetime
    updated_at: datetime


class TaskListResponse(BaseModel):
    tasks: list[TaskResponse]


class TaskCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    assignee_ids: list[UUID] = Field(min_length=1)
    priority: TaskPriority = "medium"
    due_date: date | None = None
    initial_status: Literal["todo", "in_progress"] = "todo"


class TaskAssigneeUpdateRequest(BaseModel):
    status: Literal["in_progress", "done"]


class TaskReviewRequest(BaseModel):
    action: Literal["approve", "request_changes"]
