"""add issue synchronization

Revision ID: 202607170002
Revises: 202607170001
Create Date: 2026-07-17 00:02:00
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "202607170002"
down_revision = "202607170001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sync_jobs",
        sa.Column("skipped_count", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "sync_jobs",
        sa.Column("unchanged_count", sa.Integer(), server_default="0", nullable=False),
    )
    op.create_index(
        "uq_sync_jobs_active_resource",
        "sync_jobs",
        ["repository_id", "resource_type"],
        unique=True,
        postgresql_where=sa.text("status in ('pending', 'running', 'rate_limited')"),
    )
    op.create_table(
        "issues",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("repository_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("github_id", sa.BigInteger(), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("state_reason", sa.String(length=64), nullable=True),
        sa.Column("html_url", sa.Text(), nullable=False),
        sa.Column("author_login", sa.String(length=255), nullable=True),
        sa.Column("comments_count", sa.Integer(), nullable=False),
        sa.Column("is_locked", sa.Boolean(), nullable=False),
        sa.Column("github_created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("github_updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("github_closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("state in ('open', 'closed')", name="ck_issues_state"),
        sa.ForeignKeyConstraint(["repository_id"], ["repositories.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_issues"),
        sa.UniqueConstraint("repository_id", "github_id", name="uq_issues_repository_github_id"),
        sa.UniqueConstraint("repository_id", "number", name="uq_issues_repository_number"),
    )
    op.create_index("ix_issues_repository_id", "issues", ["repository_id"], unique=False)
    op.create_index("ix_issues_repository_state", "issues", ["repository_id", "state"])
    op.create_index("ix_issues_repository_number", "issues", ["repository_id", "number"])
    op.create_index(
        "ix_issues_repository_github_updated_at",
        "issues",
        ["repository_id", "github_updated_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_issues_repository_github_updated_at", table_name="issues")
    op.drop_index("ix_issues_repository_number", table_name="issues")
    op.drop_index("ix_issues_repository_state", table_name="issues")
    op.drop_index("ix_issues_repository_id", table_name="issues")
    op.drop_table("issues")
    op.drop_index("uq_sync_jobs_active_resource", table_name="sync_jobs")
    op.drop_column("sync_jobs", "unchanged_count")
    op.drop_column("sync_jobs", "skipped_count")
