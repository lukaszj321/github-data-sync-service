from __future__ import annotations

import pytest

from github_data_sync_service.core.errors import AppError
from github_data_sync_service.github.errors import (
    GitHubBadResponseError,
    GitHubForbiddenError,
    GitHubNotFoundError,
    GitHubRateLimitError,
    GitHubTemporaryError,
    GitHubTimeoutError,
)
from github_data_sync_service.github.models import GitHubRepository
from github_data_sync_service.repositories.service import RepositoryService


class RaisingGitHub:
    def __init__(self, exc: Exception) -> None:
        self.exc = exc

    def get_repository(self, owner: str, name: str) -> object:
        raise self.exc


class Store:
    def upsert_from_github(self, repo: GitHubRepository) -> object:
        return type("Result", (), {"repository": object(), "created": True})()


@pytest.mark.parametrize(
    ("exc", "status_code"),
    [
        (GitHubNotFoundError("not found"), 404),
        (GitHubForbiddenError("forbidden"), 404),
        (GitHubRateLimitError("limited"), 429),
        (GitHubTimeoutError("timeout"), 504),
        (GitHubTemporaryError("temporary"), 503),
        (GitHubBadResponseError("bad"), 502),
    ],
)
def test_github_errors_map_to_app_errors(exc: Exception, status_code: int) -> None:
    service = RepositoryService(Store(), RaisingGitHub(exc))  # type: ignore[arg-type]
    with pytest.raises(AppError) as error:
        service.register("owner", "repo")
    assert error.value.error.status_code == status_code
