"""add task start date

Revision ID: 0015_add_task_start_date
Revises: 0014_email_verification_codes
Create Date: 2026-05-24 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0015_add_task_start_date'
down_revision: Union[str, None] = '0014_email_verification_codes'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('tasks', sa.Column('start_date', sa.Date(), nullable=True))
    op.execute("UPDATE tasks SET start_date = CURRENT_DATE() WHERE start_date IS NULL")
    op.alter_column('tasks', 'start_date', existing_type=sa.Date(), nullable=False)


def downgrade() -> None:
    op.drop_column('tasks', 'start_date')
