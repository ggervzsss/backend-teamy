"""add user profile fields

Revision ID: 0007_add_user_profile_fields
Revises: 0006_add_project_archive_state
Create Date: 2026-05-10
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0007_add_user_profile_fields"
down_revision: str | None = "0006_add_project_archive_state"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("username", sa.String(length=40), nullable=True))
    op.add_column("users", sa.Column("cloudinary_avatar_public_id", sa.String(length=255), nullable=True))
    op.create_index(op.f("ix_users_username"), "users", ["username"], unique=False)
    op.create_unique_constraint("uq_users_username", "users", ["username"])


def downgrade() -> None:
    op.drop_constraint("uq_users_username", "users", type_="unique")
    op.drop_index(op.f("ix_users_username"), table_name="users")
    op.drop_column("users", "cloudinary_avatar_public_id")
    op.drop_column("users", "username")
