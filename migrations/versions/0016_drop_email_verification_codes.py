"""drop email verification codes

Revision ID: 0016_drop_email_verification_codes
Revises: 0015_add_task_start_date
Create Date: 2026-05-25 00:00:00.000000
"""

from collections.abc import Sequence

from alembic import op


revision: str = "0016_drop_email_verification_codes"
down_revision: str | None = "0015_add_task_start_date"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index("ix_email_verification_codes_expires_at", table_name="email_verification_codes")
    op.drop_index("ix_email_verification_codes_email", table_name="email_verification_codes")
    op.drop_index("ix_email_verification_codes_consumed_at", table_name="email_verification_codes")
    op.drop_table("email_verification_codes")


def downgrade() -> None:
    import sqlalchemy as sa

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
    op.create_index("ix_email_verification_codes_consumed_at", "email_verification_codes", ["consumed_at"], unique=False)
    op.create_index("ix_email_verification_codes_email", "email_verification_codes", ["email"], unique=False)
    op.create_index("ix_email_verification_codes_expires_at", "email_verification_codes", ["expires_at"], unique=False)
