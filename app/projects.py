from datetime import UTC, datetime
from random import SystemRandom
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Response, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user
from app.models import Project, ProjectMember, User
from app.schemas import ProjectArchiveRequest, ProjectCreateRequest, ProjectDeleteRequest, ProjectJoinRequest, ProjectListResponse, ProjectResponse, ProjectUpdateRequest
from app.team_realtime import broadcast_member_joined

router = APIRouter(prefix="/projects", tags=["projects"])
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
random = SystemRandom()


def normalize_teamy_code(teamy_code: str) -> str:
    return teamy_code.strip().upper().replace(" ", "").replace("_", "-")


def format_teamy_code(raw: str) -> str:
    return f"TMY-{raw[:4]}-{raw[4:]}"


async def generate_teamy_code(db: AsyncSession) -> str:
    for _ in range(10):
        raw = "".join(random.choice(CODE_ALPHABET) for _ in range(7))
        code = format_teamy_code(raw)
        result = await db.execute(select(Project.id).where(Project.teamy_code == code))
        if result.scalar_one_or_none() is None:
            return code
    raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Could not generate a unique Teamy code")


async def get_member_count(db: AsyncSession, project_id: UUID) -> int:
    result = await db.execute(select(func.count(ProjectMember.id)).where(ProjectMember.project_id == project_id))
    return int(result.scalar_one())


async def serialize_project(db: AsyncSession, project: Project, membership: ProjectMember) -> ProjectResponse:
    return ProjectResponse(
        id=project.id,
        name=project.name,
        description=project.description,
        teamy_code=project.teamy_code,
        role=membership.role,
        member_count=await get_member_count(db, project.id),
        archived_at=project.archived_at,
        created_at=project.created_at,
        updated_at=project.updated_at,
    )


def require_project_leader(membership: ProjectMember) -> None:
    if membership.role != "leader":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only workspace owners can perform this action")


def require_project_active(project: Project) -> None:
    if project.archived_at is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This workspace is archived and read-only")


async def get_project_membership(
    project_id: Annotated[UUID, Path()],
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> tuple[Project, ProjectMember]:
    result = await db.execute(
        select(Project, ProjectMember)
        .join(ProjectMember, ProjectMember.project_id == Project.id)
        .where(Project.id == project_id, ProjectMember.user_id == user.id)
    )
    row = result.one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return row[0], row[1]


@router.get("", response_model=ProjectListResponse)
async def list_projects(user: Annotated[User, Depends(get_current_user)], db: AsyncSession = Depends(get_db)) -> ProjectListResponse:
    result = await db.execute(
        select(Project, ProjectMember, func.count(ProjectMember.id).over(partition_by=Project.id).label("member_count"))
        .join(ProjectMember, ProjectMember.project_id == Project.id)
        .where(Project.id.in_(select(ProjectMember.project_id).where(ProjectMember.user_id == user.id)))
        .order_by(Project.updated_at.desc(), Project.created_at.desc())
    )
    projects = [
        ProjectResponse(
            id=project.id,
            name=project.name,
            description=project.description,
            teamy_code=project.teamy_code,
            role=membership.role,
            member_count=int(member_count),
            archived_at=project.archived_at,
            created_at=project.created_at,
            updated_at=project.updated_at,
        )
        for project, membership, member_count in result.all()
        if membership.user_id == user.id
    ]
    return ProjectListResponse(projects=projects)


@router.post("", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
async def create_project(
    payload: ProjectCreateRequest,
    user: Annotated[User, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
) -> ProjectResponse:
    project = Project(
        name=payload.name.strip(),
        description=payload.description.strip() if payload.description else None,
        teamy_code=await generate_teamy_code(db),
        created_by_user_id=user.id,
    )
    db.add(project)
    await db.flush()

    membership = ProjectMember(project_id=project.id, user_id=user.id, role="leader")
    db.add(membership)
    await db.commit()
    await db.refresh(project)
    await db.refresh(membership)
    return await serialize_project(db, project, membership)


@router.post("/join", response_model=ProjectResponse)
async def join_project(
    payload: ProjectJoinRequest,
    user: Annotated[User, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
) -> ProjectResponse:
    teamy_code = normalize_teamy_code(payload.teamy_code)
    project_result = await db.execute(select(Project).where(Project.teamy_code == teamy_code))
    project = project_result.scalar_one_or_none()
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No project found for that Teamy code")

    member_result = await db.execute(select(ProjectMember).where(ProjectMember.project_id == project.id, ProjectMember.user_id == user.id))
    membership = member_result.scalar_one_or_none()
    is_new_member = False
    if membership is None:
        membership = ProjectMember(project_id=project.id, user_id=user.id, role="member")
        db.add(membership)
        await db.commit()
        await db.refresh(membership)
        is_new_member = True

    if is_new_member:
        await broadcast_member_joined(db, project.id, membership)

    return await serialize_project(db, project, membership)


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(
    membership: Annotated[tuple[Project, ProjectMember], Depends(get_project_membership)],
    db: AsyncSession = Depends(get_db),
) -> ProjectResponse:
    project, project_member = membership
    return await serialize_project(db, project, project_member)


@router.patch("/{project_id}", response_model=ProjectResponse)
async def update_project(
    payload: ProjectUpdateRequest,
    membership: Annotated[tuple[Project, ProjectMember], Depends(get_project_membership)],
    db: AsyncSession = Depends(get_db),
) -> ProjectResponse:
    project, project_member = membership
    require_project_leader(project_member)

    if payload.name is not None:
        next_name = payload.name.strip()
        if not next_name:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Workspace name is required")
        project.name = next_name

    await db.commit()
    await db.refresh(project)
    return await serialize_project(db, project, project_member)


@router.post("/{project_id}/archive", response_model=ProjectResponse)
async def archive_project(
    payload: ProjectArchiveRequest,
    membership: Annotated[tuple[Project, ProjectMember], Depends(get_project_membership)],
    db: AsyncSession = Depends(get_db),
) -> ProjectResponse:
    project, project_member = membership
    require_project_leader(project_member)
    if payload.confirm_archive:
        project.archived_at = project.archived_at or datetime.now(UTC)

    await db.commit()
    await db.refresh(project)
    return await serialize_project(db, project, project_member)


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    payload: ProjectDeleteRequest,
    membership: Annotated[tuple[Project, ProjectMember], Depends(get_project_membership)],
    db: AsyncSession = Depends(get_db),
) -> Response:
    project, project_member = membership
    require_project_leader(project_member)
    if payload.confirm_name != project.name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Workspace name confirmation did not match")

    await db.delete(project)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
