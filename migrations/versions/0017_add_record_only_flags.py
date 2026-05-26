"""add record-only flags

Revision ID: 0017_add_record_only_flags
Revises: 0016_drop_email_ver_codes
Create Date: 2026-05-26 13:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0017_add_record_only_flags"
down_revision: str | None = "0016_drop_email_ver_codes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("is_record_only", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_index(op.f("ix_tasks_is_record_only"), "tasks", ["is_record_only"], unique=False)
    op.add_column("announcements", sa.Column("is_record_only", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_index(op.f("ix_announcements_is_record_only"), "announcements", ["is_record_only"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_announcements_is_record_only"), table_name="announcements")
    op.drop_column("announcements", "is_record_only")
    op.drop_index(op.f("ix_tasks_is_record_only"), table_name="tasks")
    op.drop_column("tasks", "is_record_only")
