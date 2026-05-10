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


class UserProfileUpdateRequest(BaseModel):
    full_name: str | None = Field(default=None, min_length=1, max_length=160)
    username: str | None = Field(default=None, max_length=40)


class PasswordChangeRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: EmailStr
    full_name: str
    username: str | None = None
    avatar_url: str | None = None
    google_avatar_url: str | None = None
    last_online_at: datetime | None = None


class AuthResponse(BaseModel):
    user: UserResponse


class ProjectCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=2000)


class ProjectJoinRequest(BaseModel):
    teamy_code: str = Field(min_length=6, max_length=32)


class ProjectUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)


class ProjectArchiveRequest(BaseModel):
    confirm_archive: Literal[True]


class ProjectDeleteRequest(BaseModel):
    confirm_name: str = Field(min_length=1, max_length=160)


class ProjectResponse(BaseModel):
    id: UUID
    name: str
    description: str | None = None
    teamy_code: str
    role: str
    member_count: int
    archived_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class ProjectListResponse(BaseModel):
    projects: list[ProjectResponse]


TaskStatus = Literal["todo", "in_progress", "for_review", "done"]
TaskPriority = Literal["low", "medium", "high"]
AssigneeStatus = Literal["todo", "in_progress", "ready_for_review"]
FileResourceKind = Literal["doc", "link"]


class ProjectMemberResponse(BaseModel):
    id: UUID
    user: UserResponse
    role: str
    joined_at: datetime


class ProjectMemberListResponse(BaseModel):
    members: list[ProjectMemberResponse]


class ProjectMemberPresenceResponse(ProjectMemberResponse):
    is_online: bool
    last_online_at: datetime | None = None


class ProjectPresenceResponse(BaseModel):
    members: list[ProjectMemberPresenceResponse]


class TeamSocketTicketResponse(BaseModel):
    ticket: str


class TaskAssigneeResponse(BaseModel):
    id: UUID
    user: UserResponse
    status: AssigneeStatus
    completed_at: datetime | None = None


class LinkedTaskResponse(BaseModel):
    id: UUID
    title: str
    status: TaskStatus


class FileResourceSummaryResponse(BaseModel):
    id: UUID
    project_id: UUID
    title: str
    kind: FileResourceKind
    url: str | None = None
    created_by: UserResponse
    linked_tasks: list[LinkedTaskResponse] = []
    created_at: datetime
    updated_at: datetime


class FileResourceResponse(FileResourceSummaryResponse):
    content_html: str | None = None


class FileResourceListResponse(BaseModel):
    files: list[FileResourceSummaryResponse]


class FileResourceCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=240)
    kind: FileResourceKind
    url: str | None = Field(default=None, max_length=2048)
    content_html: str | None = Field(default=None, max_length=500000)


class FileResourceUpdateRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=240)
    url: str | None = Field(default=None, max_length=2048)
    content_html: str | None = Field(default=None, max_length=500000)


class TaskLinkedFileCreateRequest(BaseModel):
    mode: Literal["doc", "link"]
    title: str | None = Field(default=None, max_length=240)
    url: str | None = Field(default=None, max_length=2048)


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
    review_remarks: str | None = None
    assignees: list[TaskAssigneeResponse]
    linked_files: list[FileResourceSummaryResponse] = []
    created_at: datetime
    updated_at: datetime


class TaskListResponse(BaseModel):
    tasks: list[TaskResponse]


class TaskSocketTicketResponse(BaseModel):
    ticket: str


class AnnouncementCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=4000)
    is_pinned: bool = False


class AnnouncementPinRequest(BaseModel):
    is_pinned: bool


class AnnouncementResponse(BaseModel):
    id: UUID
    project_id: UUID
    title: str
    body: str
    is_pinned: bool
    is_read: bool
    created_by: UserResponse
    created_at: datetime
    updated_at: datetime


class AnnouncementListResponse(BaseModel):
    announcements: list[AnnouncementResponse]


class AnnouncementSocketTicketResponse(BaseModel):
    ticket: str


class TaskCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    assignee_ids: list[UUID] = Field(min_length=1)
    priority: TaskPriority = "medium"
    due_date: date | None = None
    initial_status: Literal["todo", "in_progress"] = "todo"
    linked_file: TaskLinkedFileCreateRequest | None = None


class TaskAssigneeUpdateRequest(BaseModel):
    status: Literal["in_progress", "ready_for_review"]


class TaskReviewRequest(BaseModel):
    action: Literal["approve", "request_changes"]
    remarks: str | None = Field(default=None, max_length=4000)
