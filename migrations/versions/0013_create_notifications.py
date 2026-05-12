"""create notifications

Revision ID: 0013_create_notifications
Revises: 0012_project_member_nickname
Create Date: 2026-05-13 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0013_create_notifications"
down_revision: str | None = "0012_project_member_nickname"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "notifications",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=True),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("title", sa.String(length=240), nullable=False),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("target_path", sa.String(length=1024), nullable=True),
        sa.Column("is_email_backed", sa.Boolean(), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_notifications_created_at"), "notifications", ["created_at"], unique=False)
    op.create_index(op.f("ix_notifications_is_email_backed"), "notifications", ["is_email_backed"], unique=False)
    op.create_index(op.f("ix_notifications_kind"), "notifications", ["kind"], unique=False)
    op.create_index(op.f("ix_notifications_project_id"), "notifications", ["project_id"], unique=False)
    op.create_index(op.f("ix_notifications_user_id"), "notifications", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_notifications_user_id"), table_name="notifications")
    op.drop_index(op.f("ix_notifications_project_id"), table_name="notifications")
    op.drop_index(op.f("ix_notifications_kind"), table_name="notifications")
    op.drop_index(op.f("ix_notifications_is_email_backed"), table_name="notifications")
    op.drop_index(op.f("ix_notifications_created_at"), table_name="notifications")
    op.drop_table("notifications")
