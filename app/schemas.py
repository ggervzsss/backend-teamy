from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.time_utils import utc_isoformat


class ApiModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, json_encoders={datetime: utc_isoformat})


class UserProfileUpdateRequest(BaseModel):
    full_name: str | None = Field(default=None, min_length=1, max_length=160)


class UserResponse(ApiModel):

    id: UUID
    email: EmailStr
    full_name: str
    username: str | None = None
    avatar_url: str | None = None
    google_avatar_url: str | None = None
    last_online_at: datetime | None = None


class AuthResponse(ApiModel):
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


class ProjectResponse(ApiModel):
    id: UUID
    name: str
    description: str | None = None
    teamy_code: str
    role: str
    member_count: int
    archived_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.time_utils import utc_isoformat


class ApiModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, json_encoders={datetime: utc_isoformat})


class UserProfileUpdateRequest(BaseModel):
    full_name: str | None = Field(default=None, min_length=1, max_length=160)


class UserResponse(ApiModel):

    id: UUID
    email: EmailStr
    full_name: str
    username: str | None = None
    avatar_url: str | None = None
    google_avatar_url: str | None = None
    last_online_at: datetime | None = None


class AuthResponse(ApiModel):
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


class ProjectResponse(ApiModel):
    id: UUID
    name: str
    description: str | None = None
    teamy_code: str
    role: str
    member_count: int
    archived_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class ProjectListResponse(ApiModel):
    projects: list[ProjectResponse]


TaskStatus = Literal["todo", "in_progress", "for_review", "done"]
AssigneeStatus = Literal["todo", "in_progress", "ready_for_review"]
PersonalTaskKind = Literal["task", "ticket", "note"]
FileResourceKind = Literal["doc", "link"]


class ProjectMemberResponse(ApiModel):
    id: UUID
    user: UserResponse
    role: str
    nickname: str | None = None
    joined_at: datetime


class ProjectMemberListResponse(ApiModel):
    members: list[ProjectMemberResponse]


class ProjectMemberPresenceResponse(ProjectMemberResponse):
    is_online: bool
    last_online_at: datetime | None = None


class ProjectPresenceResponse(ApiModel):
    members: list[ProjectMemberPresenceResponse]


class TeamSocketTicketResponse(ApiModel):
    ticket: str


class ProjectMemberNicknameUpdateRequest(BaseModel):
    nickname: str | None = Field(default=None, max_length=40)


class TaskAssigneeResponse(ApiModel):
    id: UUID
    user: UserResponse
    status: AssigneeStatus
    completed_at: datetime | None = None


class LinkedTaskResponse(ApiModel):
    id: UUID
    title: str
    status: TaskStatus


class FileResourceSummaryResponse(ApiModel):
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


class FileResourceListResponse(ApiModel):
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


class TaskExistingFileLinkRequest(BaseModel):
    file_id: UUID


class TaskResponse(ApiModel):
    id: UUID
    project_id: UUID
    title: str
    description: str | None = None
    start_date: date
    due_date: date | None = None
    status: TaskStatus
    is_record_only: bool
    is_private: bool
    personal_kind: PersonalTaskKind
    created_by: UserResponse
    reviewed_by: UserResponse | None = None
    reviewed_at: datetime | None = None
    review_remarks: str | None = None
    assignees: list[TaskAssigneeResponse]
    linked_files: list[FileResourceSummaryResponse] = []
    created_at: datetime
    updated_at: datetime


class TaskListResponse(ApiModel):
    tasks: list[TaskResponse]


class TaskSocketTicketResponse(ApiModel):
    ticket: str


class AnnouncementCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=4000)
    is_pinned: bool = False
    deadline_date: date | None = None
    is_record_only: bool = False


class AnnouncementUpdateRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    body: str | None = Field(default=None, min_length=1, max_length=4000)
    is_pinned: bool | None = None
    deadline_date: date | None = None
    is_record_only: bool | None = None


class AnnouncementPinRequest(BaseModel):
    is_pinned: bool


class NotificationResponse(ApiModel):
    id: UUID
    project_id: UUID | None = None
    kind: str
    title: str
    body: str | None = None
    target_path: str | None = None
    is_email_backed: bool
    read_at: datetime | None = None
    created_at: datetime


class NotificationListResponse(ApiModel):
    notifications: list[NotificationResponse]
    unread_count: int


class NotificationSocketTicketResponse(ApiModel):
    ticket: str


class AnnouncementResponse(ApiModel):
    id: UUID
    project_id: UUID
    title: str
    body: str
    is_pinned: bool
    deadline_date: date | None = None
    deadline_done_at: datetime | None = None
    is_record_only: bool
    is_read: bool
    created_by: UserResponse
    created_at: datetime
    updated_at: datetime


class AnnouncementListResponse(ApiModel):
    announcements: list[AnnouncementResponse]


class AnnouncementSocketTicketResponse(ApiModel):
    ticket: str


class TaskCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    assignee_ids: list[UUID] = Field(min_length=1)
    start_date: date | None = None
    due_date: date | None = None
    initial_status: Literal["todo", "in_progress"] = "todo"
    linked_file: TaskLinkedFileCreateRequest | None = None
    is_record_only: bool = False
    is_private: bool = False
    personal_kind: PersonalTaskKind = "task"


class TaskUpdateRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    assignee_ids: list[UUID] | None = None
    start_date: date | None = None
    due_date: date | None = None


class TaskAssigneeUpdateRequest(BaseModel):
    status: Literal["todo", "in_progress", "ready_for_review", "done"]


class TaskReviewRequest(BaseModel):
    action: Literal["approve", "request_changes"]
    remarks: str | None = Field(default=None, max_length=4000)
