"""create announcements

Revision ID: 0005_create_announcements
Revises: 0004_create_file_hub
Create Date: 2026-05-10
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0005_create_announcements"
down_revision: str | None = "0004_create_file_hub"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "announcements",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("is_pinned", sa.Boolean(), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_announcements_created_by_user_id"), "announcements", ["created_by_user_id"], unique=False)
    op.create_index(op.f("ix_announcements_is_pinned"), "announcements", ["is_pinned"], unique=False)
    op.create_index(op.f("ix_announcements_project_id"), "announcements", ["project_id"], unique=False)

    op.create_table(
        "announcement_reads",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("announcement_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["announcement_id"], ["announcements.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("announcement_id", "user_id", name="uq_announcement_reads_announcement_user"),
    )
    op.create_index(op.f("ix_announcement_reads_announcement_id"), "announcement_reads", ["announcement_id"], unique=False)
    op.create_index(op.f("ix_announcement_reads_user_id"), "announcement_reads", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_announcement_reads_user_id"), table_name="announcement_reads")
    op.drop_index(op.f("ix_announcement_reads_announcement_id"), table_name="announcement_reads")
    op.drop_table("announcement_reads")
    op.drop_index(op.f("ix_announcements_project_id"), table_name="announcements")
    op.drop_index(op.f("ix_announcements_is_pinned"), table_name="announcements")
    op.drop_index(op.f("ix_announcements_created_by_user_id"), table_name="announcements")
    op.drop_table("announcements")
