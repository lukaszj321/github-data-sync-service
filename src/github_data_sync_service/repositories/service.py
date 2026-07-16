from __future__ import annotations

import uuid
from dataclasses import dataclass

from github_data_sync_service.core.errors import AppError
from github_data_sync_service.db.models.repository import Repository
from github_data_sync_service.github.client import GitHubClient
from github_data_sync_service.github.errors import (
    GitHubBadResponseError,
    GitHubForbiddenError,
    GitHubNotFoundError,
    GitHubRateLimitError,
    GitHubTemporaryError,
    GitHubTimeoutError,
    GitHubUnauthorizedError,
    GitHubValidationError,
)
from github_data_sync_service.repositories.repository import RepositoryStore


@dataclass(frozen=True, slots=True)
class RegisterResult:
    repository: Repository
    created: bool


class RepositoryService:
    def __init__(self, store: RepositoryStore, github_client: GitHubClient) -> None:
        self._store = store
        self._github_client = github_client

    def register(self, owner: str, name: str) -> RegisterResult:
        try:
            github_repo = self._github_client.get_repository(owner, name)
        except GitHubNotFoundError as exc:
            raise AppError(
                "github_repository_not_found",
                "The requested public GitHub repository was not found.",
                404,
            ) from exc
        except (GitHubUnauthorizedError, GitHubForbiddenError, GitHubValidationError) as exc:
            raise AppError(
                "github_repository_inaccessible",
                "The requested GitHub repository is private or inaccessible.",
                404,
            ) from exc
        except GitHubRateLimitError as exc:
            raise AppError(
                "github_rate_limited",
                "GitHub rate limit was reached.",
                429,
                {"retry_after_seconds": exc.details.retry_after_seconds},
            ) from exc
        except GitHubTimeoutError as exc:
            raise AppError("github_timeout", "GitHub did not respond in time.", 504) from exc
        except GitHubTemporaryError as exc:
            raise AppError(
                "github_temporary_error", "GitHub is temporarily unavailable.", 503
            ) from exc
        except GitHubBadResponseError as exc:
            raise AppError(
                "github_bad_response", "GitHub returned an invalid response.", 502
            ) from exc

        if github_repo.is_private:
            raise AppError(
                "github_repository_inaccessible",
                "The requested GitHub repository is private or inaccessible.",
                404,
            )
        result = self._store.upsert_from_github(github_repo)
        return RegisterResult(repository=result.repository, created=result.created)

    def list(self, *, limit: int, offset: int) -> list[Repository]:
        return self._store.list(limit=limit, offset=offset)

    def get(self, repository_id: uuid.UUID) -> Repository:
        repository = self._store.get(repository_id)
        if repository is None:
            raise AppError("repository_not_found", "The local repository was not found.", 404)
        return repository
