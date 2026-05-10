"""create file hub

Revision ID: 0004_create_file_hub
Revises: 0003_create_tasks
Create Date: 2026-05-10
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0004_create_file_hub"
down_revision: str | None = "0003_create_tasks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "file_resources",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=240), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("url", sa.String(length=2048), nullable=True),
        sa.Column("content_html", sa.Text(), nullable=True),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_file_resources_created_by_user_id"), "file_resources", ["created_by_user_id"], unique=False)
    op.create_index(op.f("ix_file_resources_kind"), "file_resources", ["kind"], unique=False)
    op.create_index(op.f("ix_file_resources_project_id"), "file_resources", ["project_id"], unique=False)

    op.create_table(
        "task_file_links",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("file_resource_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["file_resource_id"], ["file_resources.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_id", "file_resource_id", name="uq_task_file_links_task_file"),
    )
    op.create_index(op.f("ix_task_file_links_file_resource_id"), "task_file_links", ["file_resource_id"], unique=False)
    op.create_index(op.f("ix_task_file_links_task_id"), "task_file_links", ["task_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_task_file_links_task_id"), table_name="task_file_links")
    op.drop_index(op.f("ix_task_file_links_file_resource_id"), table_name="task_file_links")
    op.drop_table("task_file_links")
    op.drop_index(op.f("ix_file_resources_project_id"), table_name="file_resources")
    op.drop_index(op.f("ix_file_resources_kind"), table_name="file_resources")
    op.drop_index(op.f("ix_file_resources_created_by_user_id"), table_name="file_resources")
    op.drop_table("file_resources")
