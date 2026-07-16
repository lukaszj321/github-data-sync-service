from __future__ import annotations

import logging

import httpx
import pytest
import respx

from github_data_sync_service.github.client import GitHubClient
from github_data_sync_service.github.errors import (
    GitHubBadResponseError,
    GitHubForbiddenError,
    GitHubNotFoundError,
    GitHubRateLimitError,
    GitHubTemporaryError,
    GitHubUnauthorizedError,
    GitHubValidationError,
)


def make_client(*, token: str | None = None, sleeps: list[float] | None = None) -> GitHubClient:
    return GitHubClient(
        base_url="https://api.github.test",
        user_agent="github-data-sync-service-tests",
        api_version="2022-11-28",
        token=token,
        connect_timeout_seconds=1,
        read_timeout_seconds=1,
        max_attempts=3,
        sleep=lambda seconds: sleeps.append(seconds) if sleeps is not None else None,
        jitter=lambda: 0,
    )


@respx.mock
def test_get_repository_success(sample_github_payload: dict[str, object]) -> None:
    route = respx.get("https://api.github.test/repos/fastapi/fastapi").mock(
        return_value=httpx.Response(
            200,
            json=sample_github_payload,
            headers={
                "X-GitHub-Request-Id": "request-1",
                "X-RateLimit-Limit": "60",
                "X-RateLimit-Remaining": "59",
                "X-RateLimit-Reset": "123",
            },
        )
    )
    repo = make_client().get_repository("fastapi", "fastapi")
    assert route.called
    assert repo.github_id == 160919119
    assert repo.github_request_id == "request-1"
    assert repo.rate_limit.limit == 60
    assert repo.rate_limit.remaining == 59
    assert repo.rate_limit.reset == 123


@respx.mock
def test_request_without_token(sample_github_payload: dict[str, object]) -> None:
    route = respx.get("https://api.github.test/repos/fastapi/fastapi").mock(
        return_value=httpx.Response(200, json=sample_github_payload)
    )
    make_client().get_repository("fastapi", "fastapi")
    assert "Authorization" not in route.calls[0].request.headers


@respx.mock
def test_request_with_token(sample_github_payload: dict[str, object]) -> None:
    route = respx.get("https://api.github.test/repos/fastapi/fastapi").mock(
        return_value=httpx.Response(200, json=sample_github_payload)
    )
    make_client(token="ghp_secret").get_repository("fastapi", "fastapi")
    assert route.calls[0].request.headers["Authorization"] == "Bearer ghp_secret"


@respx.mock
def test_token_is_not_in_logs_or_exception(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    token = "ghp_supersecret"
    respx.get("https://api.github.test/repos/fastapi/fastapi").mock(
        return_value=httpx.Response(404)
    )
    with pytest.raises(GitHubNotFoundError) as exc_info:
        make_client(token=token).get_repository("fastapi", "fastapi")
    assert token not in str(exc_info.value)
    assert token not in caplog.text


@pytest.mark.parametrize(
    ("status_code", "error_type"),
    [
        (404, GitHubNotFoundError),
        (401, GitHubUnauthorizedError),
        (403, GitHubForbiddenError),
    ],
)
@respx.mock
def test_non_retryable_statuses(status_code: int, error_type: type[Exception]) -> None:
    route = respx.get("https://api.github.test/repos/fastapi/fastapi").mock(
        return_value=httpx.Response(status_code)
    )
    with pytest.raises(error_type):
        make_client(sleeps=[]).get_repository("fastapi", "fastapi")
    assert route.call_count == 1


@respx.mock
def test_rate_limited_403_is_classified() -> None:
    respx.get("https://api.github.test/repos/fastapi/fastapi").mock(
        return_value=httpx.Response(403, headers={"X-RateLimit-Remaining": "0"})
    )
    with pytest.raises(GitHubRateLimitError):
        make_client().get_repository("fastapi", "fastapi")


@respx.mock
def test_429_respects_retry_after(sample_github_payload: dict[str, object]) -> None:
    sleeps: list[float] = []
    route = respx.get("https://api.github.test/repos/fastapi/fastapi").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "7"}),
            httpx.Response(200, json=sample_github_payload),
        ]
    )
    repo = make_client(sleeps=sleeps).get_repository("fastapi", "fastapi")
    assert repo.github_id == 160919119
    assert sleeps == [7.0]
    assert route.call_count == 2


@respx.mock
def test_503_retries(sample_github_payload: dict[str, object]) -> None:
    route = respx.get("https://api.github.test/repos/fastapi/fastapi").mock(
        side_effect=[httpx.Response(503), httpx.Response(200, json=sample_github_payload)]
    )
    make_client(sleeps=[]).get_repository("fastapi", "fastapi")
    assert route.call_count == 2


@respx.mock
def test_timeout_retries(sample_github_payload: dict[str, object]) -> None:
    route = respx.get("https://api.github.test/repos/fastapi/fastapi").mock(
        side_effect=[httpx.ReadTimeout("timeout"), httpx.Response(200, json=sample_github_payload)]
    )
    make_client(sleeps=[]).get_repository("fastapi", "fastapi")
    assert route.call_count == 2


@respx.mock
def test_max_attempts_exhausted() -> None:
    route = respx.get("https://api.github.test/repos/fastapi/fastapi").mock(
        return_value=httpx.Response(503)
    )
    with pytest.raises(GitHubTemporaryError):
        make_client(sleeps=[]).get_repository("fastapi", "fastapi")
    assert route.call_count == 3


@respx.mock
def test_invalid_json_does_not_retry() -> None:
    route = respx.get("https://api.github.test/repos/fastapi/fastapi").mock(
        return_value=httpx.Response(200, content=b"{")
    )
    with pytest.raises(GitHubBadResponseError):
        make_client(sleeps=[]).get_repository("fastapi", "fastapi")
    assert route.call_count == 1


@respx.mock
def test_missing_required_field_does_not_retry(sample_github_payload: dict[str, object]) -> None:
    payload = dict(sample_github_payload)
    del payload["full_name"]
    route = respx.get("https://api.github.test/repos/fastapi/fastapi").mock(
        return_value=httpx.Response(200, json=payload)
    )
    with pytest.raises(GitHubBadResponseError):
        make_client(sleeps=[]).get_repository("fastapi", "fastapi")
    assert route.call_count == 1


@respx.mock
def test_422_without_retry() -> None:
    route = respx.get("https://api.github.test/repos/bad/repo").mock(
        return_value=httpx.Response(422)
    )
    with pytest.raises(GitHubValidationError):
        make_client(sleeps=[]).get_repository("bad", "repo")
    assert route.call_count == 1
