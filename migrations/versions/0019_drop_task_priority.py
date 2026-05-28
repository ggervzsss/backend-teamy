"""drop task priority column

Revision ID: 0019_drop_task_priority
Revises: 0018_add_private_tasks
Create Date: 2026-05-28 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0019_drop_task_priority"
down_revision: str | None = "0018_add_private_tasks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("tasks", "priority")


def downgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column("priority", sa.String(length=16), nullable=False, server_default="medium"),
    )
