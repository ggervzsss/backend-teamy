"""add project member nickname

Revision ID: 0012_project_member_nickname
Revises: 0011_announcement_deadline
Create Date: 2026-05-12 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0012_project_member_nickname"
down_revision: str | None = "0011_announcement_deadline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("project_members", sa.Column("nickname", sa.String(length=40), nullable=True))


def downgrade() -> None:
    op.drop_column("project_members", "nickname")
