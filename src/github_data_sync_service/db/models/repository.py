from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from github_data_sync_service.db.base import Base

if TYPE_CHECKING:
    from github_data_sync_service.db.models.issue import Issue
    from github_data_sync_service.db.models.sync_job import SyncJob


class Repository(Base):
    __tablename__ = "repositories"
    __table_args__ = (
        UniqueConstraint("github_id", name="uq_repositories_github_id"),
        Index(
            "ix_repositories_owner_name_lower",
            text("lower(owner)"),
            text("lower(name)"),
            unique=True,
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    github_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    owner: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(511), nullable=False)
    html_url: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    default_branch: Mapped[str] = mapped_column(String(255), nullable=False)
    is_fork: Mapped[bool] = mapped_column(Boolean, nullable=False)
    is_archived: Mapped[bool] = mapped_column(Boolean, nullable=False)
    github_created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    github_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_validated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    sync_jobs: Mapped[list[SyncJob]] = relationship(back_populates="repository")
    issues: Mapped[list[Issue]] = relationship(back_populates="repository")
