"""add composite performance indexes

Revision ID: 0021_add_perf_indexes
Revises: 0020_add_ann_deadline_done
Create Date: 2026-05-31 00:00:00.000000
"""

from collections.abc import Sequence

from alembic import op


revision: str = "0021_add_perf_indexes"
down_revision: str | None = "0020_add_ann_deadline_done"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index("ix_tasks_project_private_created", "tasks", ["project_id", "is_private", "created_at"], unique=False)
    op.create_index("ix_tasks_project_status_created", "tasks", ["project_id", "status", "created_at"], unique=False)
    op.create_index("ix_file_resources_project_updated_created", "file_resources", ["project_id", "updated_at", "created_at"], unique=False)
    op.create_index(
        "ix_announcements_project_pinned_created_updated",
        "announcements",
        ["project_id", "is_pinned", "created_at", "updated_at"],
        unique=False,
    )
    op.create_index("ix_notifications_user_created", "notifications", ["user_id", "created_at"], unique=False)
    op.create_index("ix_notifications_user_read", "notifications", ["user_id", "read_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_notifications_user_read", table_name="notifications")
    op.drop_index("ix_notifications_user_created", table_name="notifications")
    op.drop_index("ix_announcements_project_pinned_created_updated", table_name="announcements")
    op.drop_index("ix_file_resources_project_updated_created", table_name="file_resources")
    op.drop_index("ix_tasks_project_status_created", table_name="tasks")
    op.drop_index("ix_tasks_project_private_created", table_name="tasks")
