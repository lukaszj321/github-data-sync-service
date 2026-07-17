from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from github_data_sync_service.db.base import Base

if TYPE_CHECKING:
    from github_data_sync_service.db.models.repository import Repository


class Issue(Base):
    __tablename__ = "issues"
    __table_args__ = (
        UniqueConstraint("repository_id", "github_id", name="uq_issues_repository_github_id"),
        UniqueConstraint("repository_id", "number", name="uq_issues_repository_number"),
        CheckConstraint("state in ('open', 'closed')", name="ck_issues_state"),
        Index("ix_issues_repository_id", "repository_id"),
        Index("ix_issues_repository_state", "repository_id", "state"),
        Index("ix_issues_repository_number", "repository_id", "number"),
        Index("ix_issues_repository_github_updated_at", "repository_id", "github_updated_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    repository_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False
    )
    github_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str | None] = mapped_column(Text)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    state_reason: Mapped[str | None] = mapped_column(String(64))
    html_url: Mapped[str] = mapped_column(Text, nullable=False)
    author_login: Mapped[str | None] = mapped_column(String(255))
    comments_count: Mapped[int] = mapped_column(Integer, nullable=False)
    is_locked: Mapped[bool] = mapped_column(Boolean, nullable=False)
    github_created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    github_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    github_closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    repository: Mapped[Repository] = relationship(back_populates="issues")
