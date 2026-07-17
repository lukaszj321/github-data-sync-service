from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import Select, literal_column, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import Result
from sqlalchemy.orm import Session

from github_data_sync_service.db.models.issue import Issue
from github_data_sync_service.github.models import GitHubIssue


@dataclass(frozen=True, slots=True)
class IssueUpsertCounts:
    created: int
    updated: int
    unchanged: int


class IssuesStore:
    def __init__(self, session: Session) -> None:
        self._session = session

    def upsert_page(
        self,
        *,
        repository_id: uuid.UUID,
        issues: tuple[GitHubIssue, ...],
        synced_at: datetime | None = None,
    ) -> IssueUpsertCounts:
        if not issues:
            return IssueUpsertCounts(created=0, updated=0, unchanged=0)
        now = synced_at or datetime.now(UTC)
        values = [
            {
                "id": uuid.uuid4(),
                "repository_id": repository_id,
                "github_id": issue.github_id,
                "number": issue.number,
                "title": issue.title,
                "body": issue.body,
                "state": issue.state,
                "state_reason": issue.state_reason,
                "html_url": issue.html_url,
                "author_login": issue.author_login,
                "comments_count": issue.comments_count,
                "is_locked": issue.is_locked,
                "github_created_at": issue.github_created_at,
                "github_updated_at": issue.github_updated_at,
                "github_closed_at": issue.github_closed_at,
                "last_synced_at": now,
                "created_at": now,
                "updated_at": now,
            }
            for issue in issues
        ]
        stmt = insert(Issue).values(values)
        excluded = stmt.excluded
        domain_fields = (
            "number",
            "title",
            "body",
            "state",
            "state_reason",
            "html_url",
            "author_login",
            "comments_count",
            "is_locked",
            "github_created_at",
            "github_updated_at",
            "github_closed_at",
        )
        update_values = {
            field: getattr(excluded, field)
            for field in (
                *domain_fields,
                "last_synced_at",
                "updated_at",
            )
        }
        distinct_conditions = [
            getattr(Issue, field).is_distinct_from(getattr(excluded, field))
            for field in domain_fields
        ]
        # PostgreSQL exposes xmax in RETURNING; here it lets us distinguish INSERT from
        # conflict UPDATE without a race-prone preflight SELECT.
        created_result: Result[tuple[bool]] = self._session.execute(
            stmt.on_conflict_do_update(
                constraint="uq_issues_repository_github_id",
                set_=update_values,
                where=or_(*distinct_conditions),
            ).returning(literal_column("xmax = 0").label("created"))
        )
        created_flags = [bool(value) for value in created_result.scalars()]
        created = sum(1 for created_flag in created_flags if created_flag)
        updated = len(created_flags) - created
        unchanged = len(issues) - len(created_flags)
        return IssueUpsertCounts(created=created, updated=updated, unchanged=unchanged)

    def list(
        self,
        *,
        repository_id: uuid.UUID,
        limit: int,
        offset: int,
        state: str | None = None,
    ) -> list[Issue]:
        stmt: Select[tuple[Issue]] = (
            select(Issue)
            .where(Issue.repository_id == repository_id)
            .order_by(Issue.number.desc(), Issue.id)
            .limit(limit)
            .offset(offset)
        )
        if state is not None:
            stmt = stmt.where(Issue.state == state)
        return list(self._session.scalars(stmt))
