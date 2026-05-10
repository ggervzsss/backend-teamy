"""add announcement deadline date

Revision ID: 0011_announcement_deadline
Revises: 0010_add_task_review_remarks
Create Date: 2026-05-11 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0011_announcement_deadline"
down_revision: str | None = "0010_add_task_review_remarks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("announcements", sa.Column("deadline_date", sa.Date(), nullable=True))


def downgrade() -> None:
    op.drop_column("announcements", "deadline_date")
