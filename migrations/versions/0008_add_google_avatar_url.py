"""add google avatar url

Revision ID: 0008_add_google_avatar_url
Revises: 0007_add_user_profile_fields
Create Date: 2026-05-10
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0008_add_google_avatar_url"
down_revision: str | None = "0007_add_user_profile_fields"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("google_avatar_url", sa.String(length=1024), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "google_avatar_url")
