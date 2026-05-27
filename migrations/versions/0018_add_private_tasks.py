"""add private task metadata

Revision ID: 0018_add_private_tasks
Revises: 0017_add_record_only_flags
Create Date: 2026-05-27 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0018_add_private_tasks"
down_revision: str | None = "0017_add_record_only_flags"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("is_private", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_index(op.f("ix_tasks_is_private"), "tasks", ["is_private"], unique=False)
    op.add_column("tasks", sa.Column("personal_kind", sa.String(length=16), nullable=False, server_default="task"))
    op.create_index(op.f("ix_tasks_personal_kind"), "tasks", ["personal_kind"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_tasks_personal_kind"), table_name="tasks")
    op.drop_column("tasks", "personal_kind")
    op.drop_index(op.f("ix_tasks_is_private"), table_name="tasks")
    op.drop_column("tasks", "is_private")
