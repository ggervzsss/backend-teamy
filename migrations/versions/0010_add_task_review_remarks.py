"""add task review remarks

Revision ID: 0010_add_task_review_remarks
Revises: 0009_add_user_last_online_at
Create Date: 2026-05-11
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0010_add_task_review_remarks"
down_revision: str | None = "0009_add_user_last_online_at"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("review_remarks", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("tasks", "review_remarks")
