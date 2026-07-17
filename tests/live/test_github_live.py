from __future__ import annotations

import os

import pytest

from github_data_sync_service.core.config import Settings
from github_data_sync_service.github.client import GitHubClient

pytestmark = pytest.mark.live


@pytest.mark.skipif(os.environ.get("RUN_LIVE_TESTS") != "1", reason="RUN_LIVE_TESTS=1 required")
def test_live_fastapi_repository() -> None:
    settings = Settings()
    client = GitHubClient(
        base_url=settings.github_api_base_url,
        user_agent=settings.github_user_agent,
        api_version=settings.github_api_version,
        token=settings.github_token_value,
        connect_timeout_seconds=settings.github_connect_timeout_seconds,
        read_timeout_seconds=settings.github_read_timeout_seconds,
        max_attempts=settings.github_max_attempts,
    )
    try:
        repo = client.get_repository("fastapi", "fastapi")
    finally:
        client.close()
    assert repo.full_name.lower() == "fastapi/fastapi"


@pytest.mark.skipif(os.environ.get("RUN_LIVE_TESTS") != "1", reason="RUN_LIVE_TESTS=1 required")
def test_live_fastapi_issues_page() -> None:
    settings = Settings()
    client = GitHubClient(
        base_url=settings.github_api_base_url,
        user_agent=settings.github_user_agent,
        api_version=settings.github_api_version,
        token=settings.github_token_value,
        connect_timeout_seconds=settings.github_connect_timeout_seconds,
        read_timeout_seconds=settings.github_read_timeout_seconds,
        max_attempts=settings.github_max_attempts,
    )
    try:
        page = next(
            client.iter_issues_pages(
                "fastapi",
                "fastapi",
                per_page=1,
                max_pages=1,
            )
        )
    finally:
        client.close()
    assert page.fetched_count >= len(page.issues)
    assert page.skipped_pull_request_count >= 0
    if page.issues:
        assert page.issues[0].html_url.startswith("https://github.com/fastapi/fastapi/issues/")
