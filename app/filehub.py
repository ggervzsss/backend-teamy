from collections import defaultdict
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, select
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


async def serialize_file_resources_batch(
    db: AsyncSession, resources: list[FileResource]
) -> list[FileResourceSummaryResponse]:
    if not resources:
        return []

    creator_ids = list({r.created_by_user_id for r in resources})
    file_ids = [r.id for r in resources]
    project_id = resources[0].project_id

    # Batch-fetch all creator Users — ONE query
    users_result = await db.execute(select(User).where(User.id.in_(creator_ids)))
    users_by_id = {u.id: u for u in users_result.scalars().all()}

    # Batch-fetch all ProjectMembers for creators — ONE query
    members_result = await db.execute(
        select(ProjectMember).where(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id.in_(creator_ids),
        )
    )
    members_by_user_id = {m.user_id: m for m in members_result.scalars().all()}

    # Batch-fetch all linked tasks for every file — ONE query
    tasks_result = await db.execute(
        select(TaskFileLink, Task)
        .join(Task, Task.id == TaskFileLink.task_id)
        .where(TaskFileLink.file_resource_id.in_(file_ids))
        .order_by(Task.created_at.desc())
    )
    linked_tasks_by_file_id: dict[UUID, list[LinkedTaskResponse]] = defaultdict(list)
    for link, task in tasks_result.all():
        linked_tasks_by_file_id[link.file_resource_id].append(
            LinkedTaskResponse(id=task.id, title=task.title, status=task.status)
        )

    summaries: list[FileResourceSummaryResponse] = []
    for resource in resources:
        creator = users_by_id.get(resource.created_by_user_id)
        if creator is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="File creator could not be loaded",
            )
        creator_member = members_by_user_id.get(creator.id)
        summaries.append(
            FileResourceSummaryResponse(
                id=resource.id,
                project_id=resource.project_id,
                title=resource.title,
                kind=resource.kind,
                url=resource.url,
                created_by=serialize_project_user(creator, creator_member),
                linked_tasks=linked_tasks_by_file_id.get(resource.id, []),
                created_at=resource.created_at,
                updated_at=resource.updated_at,
            )
        )
    return summaries


@router.get("", response_model=FileResourceListResponse)
async def list_file_resources(
    membership: Annotated[tuple[Project, ProjectMember], Depends(get_project_membership)],
    db: AsyncSession = Depends(get_db),
) -> FileResourceListResponse:
    project, _ = membership
    result = await db.execute(select(FileResource).where(FileResource.project_id == project.id).order_by(FileResource.updated_at.desc(), FileResource.created_at.desc()))
    resources = result.scalars().all()
    return FileResourceListResponse(files=await serialize_file_resources_batch(db, list(resources)))


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
        clean_title = payload.title.strip()
        if not clean_title:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Title is required")
        resource.title = clean_title
    if resource.kind == "link" and payload.url is not None:
        resource.url = validate_file_payload(resource.kind, resource.title, payload.url)
    if resource.kind == "doc" and payload.content_html is not None:
        resource.content_html = sanitize_html(payload.content_html)
    await db.commit()
    await db.refresh(resource)
    return await serialize_file_resource(db, resource, include_content=True)  # type: ignore[return-value]


@router.delete("/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_file_resource(
    file_id: UUID,
    membership: Annotated[tuple[Project, ProjectMember], Depends(get_project_membership)],
    db: AsyncSession = Depends(get_db),
) -> None:
    project, _ = membership
    resource = await get_file_for_project(db, project.id, file_id)
    await db.execute(delete(TaskFileLink).where(TaskFileLink.file_resource_id == resource.id))
    await db.delete(resource)
    await db.commit()
