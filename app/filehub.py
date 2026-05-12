from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user
from app.html_sanitizer import sanitize_html
from app.models import FileResource, Project, ProjectMember, Task, TaskFileLink, User
from app.projects import get_project_membership, require_project_active
from app.schemas import (
    FileResourceCreateRequest,
    FileResourceListResponse,
    FileResourceResponse,
    FileResourceSummaryResponse,
    FileResourceUpdateRequest,
    LinkedTaskResponse,
)
from app.user_responses import serialize_project_user

router = APIRouter(prefix="/projects/{project_id}/files", tags=["file-hub"])


def validate_file_payload(kind: str, title: str, url: str | None) -> str | None:
    clean_title = title.strip()
    if not clean_title:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Title is required")
    clean_url = url.strip() if url else None
    if kind == "link" and not clean_url:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="URL is required for links")
    if kind == "doc":
        return None
    return clean_url


async def get_linked_tasks(db: AsyncSession, file_id: UUID) -> list[LinkedTaskResponse]:
    result = await db.execute(
        select(Task)
        .join(TaskFileLink, TaskFileLink.task_id == Task.id)
        .where(TaskFileLink.file_resource_id == file_id)
        .order_by(Task.created_at.desc())
    )
    return [LinkedTaskResponse(id=task.id, title=task.title, status=task.status) for task in result.scalars().all()]


async def serialize_file_resource(db: AsyncSession, resource: FileResource, include_content: bool = False) -> FileResourceSummaryResponse:
    creator = await db.get(User, resource.created_by_user_id)
    if creator is None:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="File creator could not be loaded")
    member_result = await db.execute(select(ProjectMember).where(ProjectMember.project_id == resource.project_id, ProjectMember.user_id == creator.id))
    creator_member = member_result.scalar_one_or_none()

    base = {
        "id": resource.id,
        "project_id": resource.project_id,
        "title": resource.title,
        "kind": resource.kind,
        "url": resource.url,
        "created_by": serialize_project_user(creator, creator_member),
        "linked_tasks": await get_linked_tasks(db, resource.id),
        "created_at": resource.created_at,
        "updated_at": resource.updated_at,
    }
    if include_content:
        return FileResourceResponse(**base, content_html=resource.content_html)
    return FileResourceSummaryResponse(**base)


async def get_file_for_project(db: AsyncSession, project_id: UUID, file_id: UUID) -> FileResource:
    result = await db.execute(select(FileResource).where(FileResource.project_id == project_id, FileResource.id == file_id))
    resource = result.scalar_one_or_none()
    if resource is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File resource not found")
    return resource


@router.get("", response_model=FileResourceListResponse)
async def list_file_resources(
    membership: Annotated[tuple[Project, ProjectMember], Depends(get_project_membership)],
    db: AsyncSession = Depends(get_db),
) -> FileResourceListResponse:
    project, _ = membership
    result = await db.execute(select(FileResource).where(FileResource.project_id == project.id).order_by(FileResource.updated_at.desc(), FileResource.created_at.desc()))
    return FileResourceListResponse(files=[await serialize_file_resource(db, resource) for resource in result.scalars().all()])


@router.post("", response_model=FileResourceResponse, status_code=status.HTTP_201_CREATED)
async def create_file_resource(
    payload: FileResourceCreateRequest,
    user: Annotated[User, Depends(get_current_user)],
    membership: Annotated[tuple[Project, ProjectMember], Depends(get_project_membership)],
    db: AsyncSession = Depends(get_db),
) -> FileResourceResponse:
    project, _ = membership
    require_project_active(project)
    clean_url = validate_file_payload(payload.kind, payload.title, payload.url)
    resource = FileResource(
        project_id=project.id,
        title=payload.title.strip(),
        kind=payload.kind,
        url=clean_url,
        content_html=sanitize_html(payload.content_html) if payload.kind == "doc" else None,
        created_by_user_id=user.id,
    )
    db.add(resource)
    await db.commit()
    await db.refresh(resource)
    return await serialize_file_resource(db, resource, include_content=True)  # type: ignore[return-value]


@router.get("/{file_id}", response_model=FileResourceResponse)
async def get_file_resource(
    file_id: UUID,
    membership: Annotated[tuple[Project, ProjectMember], Depends(get_project_membership)],
    db: AsyncSession = Depends(get_db),
) -> FileResourceResponse:
    project, _ = membership
    require_project_active(project)
    resource = await get_file_for_project(db, project.id, file_id)
    return await serialize_file_resource(db, resource, include_content=True)  # type: ignore[return-value]


@router.patch("/{file_id}", response_model=FileResourceResponse)
async def update_file_resource(
    file_id: UUID,
    payload: FileResourceUpdateRequest,
    membership: Annotated[tuple[Project, ProjectMember], Depends(get_project_membership)],
    db: AsyncSession = Depends(get_db),
) -> FileResourceResponse:
    project, _ = membership
    resource = await get_file_for_project(db, project.id, file_id)
    if payload.title is not None:
        resource.title = payload.title.strip()
    if resource.kind == "link" and payload.url is not None:
        resource.url = validate_file_payload(resource.kind, resource.title, payload.url)
    if resource.kind == "doc" and payload.content_html is not None:
        resource.content_html = sanitize_html(payload.content_html)
    await db.commit()
    await db.refresh(resource)
    return await serialize_file_resource(db, resource, include_content=True)  # type: ignore[return-value]
