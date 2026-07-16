from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Select, literal_column, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from github_data_sync_service.db.models.repository import Repository
from github_data_sync_service.github.models import GitHubRepository


@dataclass(frozen=True, slots=True)
class UpsertResult:
    repository: Repository
    created: bool


class RepositoryStore:
    def __init__(self, session: Session) -> None:
        self._session = session

    def upsert_from_github(self, repo: GitHubRepository) -> UpsertResult:
        now = datetime.now(UTC)
        values = {
            "id": uuid.uuid4(),
            "github_id": repo.github_id,
            "owner": repo.owner,
            "name": repo.name,
            "full_name": repo.full_name,
            "html_url": repo.html_url,
            "description": repo.description,
            "default_branch": repo.default_branch,
            "is_fork": repo.is_fork,
            "is_archived": repo.is_archived,
            "github_created_at": repo.github_created_at,
            "github_updated_at": repo.github_updated_at,
            "last_validated_at": now,
            "created_at": now,
            "updated_at": now,
        }
        stmt = insert(Repository).values(**values)
        update_values = {
            key: values[key] for key in values if key not in {"id", "github_id", "created_at"}
        }
        upsert_stmt: Any = stmt.on_conflict_do_update(
            constraint="uq_repositories_github_id",
            set_=update_values,
        ).returning(Repository, literal_column("xmax = 0").label("created"))
        row = self._session.execute(upsert_stmt).one()
        self._session.commit()
        repository = row[0]
        self._session.refresh(repository)
        return UpsertResult(repository=repository, created=bool(row[1]))

    def list(self, *, limit: int, offset: int) -> list[Repository]:
        stmt: Select[tuple[Repository]] = (
            select(Repository)
            .order_by(Repository.owner, Repository.name, Repository.id)
            .limit(limit)
            .offset(offset)
        )
        return list(self._session.scalars(stmt))

    def get(self, repository_id: uuid.UUID) -> Repository | None:
        return self._session.get(Repository, repository_id)
