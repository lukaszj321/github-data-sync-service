from __future__ import annotations

import uuid

from github_data_sync_service.core.errors import AppError
from github_data_sync_service.db.models.issue import Issue
from github_data_sync_service.issues.repository import IssuesStore
from github_data_sync_service.repositories.repository import RepositoryStore


class IssueService:
    def __init__(self, issue_store: IssuesStore, repository_store: RepositoryStore) -> None:
        self._issue_store = issue_store
        self._repository_store = repository_store

    def list(
        self,
        *,
        repository_id: uuid.UUID,
        limit: int,
        offset: int,
        state: str | None,
    ) -> list[Issue]:
        if state is not None and state not in {"open", "closed"}:
            raise AppError("invalid_issue_state", "Issue state must be open or closed.", 422)
        if self._repository_store.get(repository_id) is None:
            raise AppError("repository_not_found", "The local repository was not found.", 404)
        return self._issue_store.list(
            repository_id=repository_id,
            limit=limit,
            offset=offset,
            state=state,
        )
