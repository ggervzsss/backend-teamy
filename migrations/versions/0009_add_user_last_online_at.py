"""add user last online timestamp

Revision ID: 0009_add_user_last_online_at
Revises: 0008_add_google_avatar_url
Create Date: 2026-05-10
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0009_add_user_last_online_at"
down_revision: str | None = "0008_add_google_avatar_url"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("last_online_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "last_online_at")
