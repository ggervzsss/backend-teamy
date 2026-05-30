"""add announcement deadline done timestamp

Revision ID: 0020_add_ann_deadline_done
Revises: 0019_drop_task_priority
Create Date: 2026-05-28 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0020_add_ann_deadline_done"
down_revision: str | None = "0019_drop_task_priority"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("announcements", sa.Column("deadline_done_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("announcements", "deadline_done_at")
