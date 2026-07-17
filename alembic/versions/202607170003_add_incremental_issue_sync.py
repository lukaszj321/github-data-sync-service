"""add incremental issue synchronization

Revision ID: 202607170003
Revises: 202607170002
Create Date: 2026-07-17 00:03:00
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "202607170003"
down_revision = "202607170002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sync_jobs",
        sa.Column("sync_mode", sa.String(length=32), server_default="full", nullable=False),
    )
    op.add_column("sync_jobs", sa.Column("cursor_before", sa.DateTime(timezone=True)))
    op.add_column("sync_jobs", sa.Column("since_at", sa.DateTime(timezone=True)))
    op.add_column("sync_jobs", sa.Column("cursor_after", sa.DateTime(timezone=True)))
    op.add_column("sync_jobs", sa.Column("sync_window_started_at", sa.DateTime(timezone=True)))
    op.create_check_constraint(
        "ck_sync_jobs_sync_mode",
        "sync_jobs",
        "sync_mode in ('full', 'incremental')",
    )
    op.create_table(
        "resource_sync_states",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("repository_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("resource_type", sa.String(length=64), nullable=False),
        sa.Column("cursor_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_successful_job_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("last_sync_mode", sa.String(length=32), nullable=True),
        sa.Column("last_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "last_sync_mode IS NULL OR last_sync_mode IN ('full', 'incremental')",
            name="ck_resource_sync_states_last_sync_mode",
        ),
        sa.ForeignKeyConstraint(["repository_id"], ["repositories.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["last_successful_job_id"], ["sync_jobs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name="pk_resource_sync_states"),
        sa.UniqueConstraint(
            "repository_id",
            "resource_type",
            name="uq_resource_sync_states_repository_resource",
        ),
    )
    op.create_index(
        "ix_resource_sync_states_repository_id",
        "resource_sync_states",
        ["repository_id"],
    )
    op.create_index(
        "ix_resource_sync_states_repository_resource",
        "resource_sync_states",
        ["repository_id", "resource_type"],
    )
    op.create_index(
        "ix_resource_sync_states_cursor_at",
        "resource_sync_states",
        ["cursor_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_resource_sync_states_cursor_at", table_name="resource_sync_states")
    op.drop_index("ix_resource_sync_states_repository_resource", table_name="resource_sync_states")
    op.drop_index("ix_resource_sync_states_repository_id", table_name="resource_sync_states")
    op.drop_table("resource_sync_states")
    op.drop_constraint("ck_sync_jobs_sync_mode", "sync_jobs", type_="check")
    op.drop_column("sync_jobs", "sync_window_started_at")
    op.drop_column("sync_jobs", "cursor_after")
    op.drop_column("sync_jobs", "since_at")
    op.drop_column("sync_jobs", "cursor_before")
    op.drop_column("sync_jobs", "sync_mode")
