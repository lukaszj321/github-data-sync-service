from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from github_data_sync_service.db.base import Base

if TYPE_CHECKING:
    from github_data_sync_service.db.models.repository import Repository
    from github_data_sync_service.db.models.sync_job import SyncJob


class ResourceSyncState(Base):
    __tablename__ = "resource_sync_states"
    __table_args__ = (
        UniqueConstraint(
            "repository_id",
            "resource_type",
            name="uq_resource_sync_states_repository_resource",
        ),
        CheckConstraint(
            "last_sync_mode IS NULL OR last_sync_mode IN ('full', 'incremental')",
            name="ck_resource_sync_states_last_sync_mode",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    repository_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False
    )
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False)
    cursor_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_successful_job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sync_jobs.id", ondelete="SET NULL")
    )
    last_sync_mode: Mapped[str | None] = mapped_column(String(32))
    last_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    repository: Mapped[Repository] = relationship(back_populates="resource_sync_states")
    last_successful_job: Mapped[SyncJob | None] = relationship()
