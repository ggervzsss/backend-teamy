"""create email verification codes

Revision ID: 0014_email_verification_codes
Revises: 0013_create_notifications
Create Date: 2026-05-13 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0014_email_verification_codes"
down_revision: str | None = "0013_create_notifications"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "email_verification_codes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("code_hash", sa.String(length=128), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_email_verification_codes_consumed_at"), "email_verification_codes", ["consumed_at"], unique=False)
    op.create_index(op.f("ix_email_verification_codes_email"), "email_verification_codes", ["email"], unique=False)
    op.create_index(op.f("ix_email_verification_codes_expires_at"), "email_verification_codes", ["expires_at"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_email_verification_codes_expires_at"), table_name="email_verification_codes")
    op.drop_index(op.f("ix_email_verification_codes_email"), table_name="email_verification_codes")
    op.drop_index(op.f("ix_email_verification_codes_consumed_at"), table_name="email_verification_codes")
    op.drop_table("email_verification_codes")
