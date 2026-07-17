from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta, timezone

import httpx
import pytest
import respx

from github_data_sync_service.github.client import GitHubClient, format_github_since
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


def issue_payload(number: int, **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": 1000 + number,
        "number": number,
        "title": f"Issue {number}",
        "body": f"Body {number}",
        "state": "open",
        "state_reason": None,
        "html_url": f"https://github.com/owner/repo/issues/{number}",
        "user": {"login": "alice"},
        "comments": 2,
        "locked": False,
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-02T00:00:00Z",
        "closed_at": None,
    }
    payload.update(overrides)
    return payload


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


@respx.mock
def test_issues_first_request_uses_required_params() -> None:
    route = respx.get("https://api.github.test/repos/owner/repo/issues").mock(
        return_value=httpx.Response(200, json=[issue_payload(1)])
    )
    page = next(make_client().iter_issues_pages("owner", "repo", per_page=200, max_pages=10))
    params = route.calls[0].request.url.params
    assert page.fetched_count == 1
    assert params["state"] == "all"
    assert params["per_page"] == "100"
    assert params["sort"] == "updated"
    assert params["direction"] == "asc"
    assert "since" not in params


@respx.mock
def test_issues_incremental_request_uses_utc_since_without_microseconds() -> None:
    route = respx.get("https://api.github.test/repos/owner/repo/issues").mock(
        return_value=httpx.Response(200, json=[])
    )
    since = datetime(2026, 7, 17, 14, 0, 0, 123456, tzinfo=timezone(timedelta(hours=2)))
    list(make_client().iter_issues_pages("owner", "repo", per_page=50, max_pages=10, since=since))
    params = route.calls[0].request.url.params
    assert params["since"] == "2026-07-17T12:00:00Z"


def test_format_github_since_rejects_naive_datetime() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        format_github_since(datetime(2026, 7, 17, 12, 0, 0))


@respx.mock
def test_issues_single_page_without_link() -> None:
    respx.get("https://api.github.test/repos/owner/repo/issues").mock(
        return_value=httpx.Response(200, json=[issue_payload(1)])
    )
    pages = list(make_client().iter_issues_pages("owner", "repo", per_page=50, max_pages=10))
    assert len(pages) == 1
    assert pages[0].issues[0].number == 1
    assert pages[0].next_url is None


@respx.mock
def test_issues_two_pages_follow_next_link_only() -> None:
    first = respx.get(
        "https://api.github.test/repos/owner/repo/issues",
        params={"state": "all", "per_page": "50", "sort": "updated", "direction": "asc"},
    ).mock(
        return_value=httpx.Response(
            200,
            json=[issue_payload(1)],
            headers={
                "Link": (
                    '<https://api.github.test/repos/owner/repo/issues?page=1>; rel="first", '
                    '<https://api.github.test/repos/owner/repo/issues?page=2>; rel="next", '
                    '<https://api.github.test/repos/owner/repo/issues?page=9>; rel="last"'
                )
            },
        )
    )
    second = respx.get("https://api.github.test/repos/owner/repo/issues?page=2").mock(
        return_value=httpx.Response(200, json=[issue_payload(2)])
    )
    pages = list(make_client().iter_issues_pages("owner", "repo", per_page=50, max_pages=10))
    assert first.call_count == 1
    assert second.call_count == 1
    assert [page.issues[0].number for page in pages] == [1, 2]


@respx.mock
def test_issues_next_link_does_not_duplicate_since() -> None:
    since = datetime(2026, 7, 17, 12, 0, tzinfo=UTC)
    first = respx.get(
        "https://api.github.test/repos/owner/repo/issues",
        params={
            "state": "all",
            "per_page": "50",
            "sort": "updated",
            "direction": "asc",
            "since": "2026-07-17T12:00:00Z",
        },
    ).mock(
        return_value=httpx.Response(
            200,
            json=[issue_payload(1)],
            headers={
                "Link": (
                    "<https://api.github.test/repos/owner/repo/issues?page=2&since=from-link>; "
                    'rel="next"'
                )
            },
        )
    )
    second = respx.get(
        "https://api.github.test/repos/owner/repo/issues?page=2&since=from-link"
    ).mock(return_value=httpx.Response(200, json=[issue_payload(2)]))
    list(make_client().iter_issues_pages("owner", "repo", per_page=50, max_pages=10, since=since))
    assert first.calls[0].request.url.params["since"] == "2026-07-17T12:00:00Z"
    assert second.calls[0].request.url.params["since"] == "from-link"


@respx.mock
def test_issues_304_is_unexpected_without_conditional_headers() -> None:
    respx.get("https://api.github.test/repos/owner/repo/issues").mock(
        return_value=httpx.Response(304)
    )
    with pytest.raises(GitHubBadResponseError):
        list(make_client().iter_issues_pages("owner", "repo", per_page=50, max_pages=10))


@respx.mock
def test_issues_rejects_next_url_for_other_host() -> None:
    respx.get("https://api.github.test/repos/owner/repo/issues").mock(
        return_value=httpx.Response(
            200,
            json=[issue_payload(1)],
            headers={"Link": '<https://evil.test/repos/owner/repo/issues?page=2>; rel="next"'},
        )
    )
    with pytest.raises(GitHubBadResponseError):
        list(make_client().iter_issues_pages("owner", "repo", per_page=50, max_pages=10))


@respx.mock
def test_issues_detects_next_url_loop() -> None:
    respx.get("https://api.github.test/repos/owner/repo/issues").mock(
        return_value=httpx.Response(
            200,
            json=[issue_payload(1)],
            headers={
                "Link": '<https://api.github.test/repos/owner/repo/issues?page=2>; rel="next"'
            },
        )
    )
    respx.get("https://api.github.test/repos/owner/repo/issues?page=2").mock(
        return_value=httpx.Response(
            200,
            json=[issue_payload(2)],
            headers={
                "Link": '<https://api.github.test/repos/owner/repo/issues?page=2>; rel="next"'
            },
        )
    )
    with pytest.raises(GitHubBadResponseError):
        list(make_client().iter_issues_pages("owner", "repo", per_page=50, max_pages=10))


@respx.mock
def test_issues_detects_max_pages_exceeded() -> None:
    respx.get("https://api.github.test/repos/owner/repo/issues").mock(
        return_value=httpx.Response(
            200,
            json=[issue_payload(1)],
            headers={
                "Link": '<https://api.github.test/repos/owner/repo/issues?page=2>; rel="next"'
            },
        )
    )
    with pytest.raises(GitHubBadResponseError):
        list(make_client().iter_issues_pages("owner", "repo", per_page=50, max_pages=1))


@respx.mock
def test_issues_filters_pull_requests_and_counts_skipped() -> None:
    payload = [
        issue_payload(1),
        issue_payload(2, pull_request={"url": "https://api.github.test/pulls/2"}),
        issue_payload(3),
    ]
    respx.get("https://api.github.test/repos/owner/repo/issues").mock(
        return_value=httpx.Response(200, json=payload)
    )
    page = next(make_client().iter_issues_pages("owner", "repo", per_page=50, max_pages=10))
    assert page.fetched_count == 3
    assert page.skipped_pull_request_count == 1
    assert [issue.number for issue in page.issues] == [1, 3]


@respx.mock
def test_issues_accepts_nullable_fields_and_rate_headers() -> None:
    respx.get("https://api.github.test/repos/owner/repo/issues").mock(
        return_value=httpx.Response(
            200,
            json=[
                issue_payload(
                    1,
                    body=None,
                    user=None,
                    closed_at=None,
                    state_reason=None,
                )
            ],
            headers={
                "X-GitHub-Request-Id": "request-issues",
                "X-RateLimit-Limit": "60",
                "X-RateLimit-Remaining": "58",
                "X-RateLimit-Reset": "1234",
            },
        )
    )
    page = next(make_client().iter_issues_pages("owner", "repo", per_page=50, max_pages=10))
    assert page.issues[0].body is None
    assert page.issues[0].author_login is None
    assert page.issues[0].github_closed_at is None
    assert page.github_request_id == "request-issues"
    assert page.rate_limit.remaining == 58


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, content=b"{"),
        httpx.Response(200, json={"not": "a-list"}),
        httpx.Response(200, json=[{"id": 1}]),
    ],
)
@respx.mock
def test_issues_rejects_invalid_responses(response: httpx.Response) -> None:
    respx.get("https://api.github.test/repos/owner/repo/issues").mock(return_value=response)
    with pytest.raises(GitHubBadResponseError):
        list(make_client().iter_issues_pages("owner", "repo", per_page=50, max_pages=10))


@respx.mock
def test_issues_503_then_success() -> None:
    route = respx.get("https://api.github.test/repos/owner/repo/issues").mock(
        side_effect=[httpx.Response(503), httpx.Response(200, json=[issue_payload(1)])]
    )
    pages = list(
        make_client(sleeps=[]).iter_issues_pages("owner", "repo", per_page=50, max_pages=10)
    )
    assert route.call_count == 2
    assert pages[0].issues[0].number == 1


@respx.mock
def test_issues_timeout_then_success() -> None:
    route = respx.get("https://api.github.test/repos/owner/repo/issues").mock(
        side_effect=[httpx.ReadTimeout("timeout"), httpx.Response(200, json=[issue_payload(1)])]
    )
    pages = list(
        make_client(sleeps=[]).iter_issues_pages("owner", "repo", per_page=50, max_pages=10)
    )
    assert route.call_count == 2
    assert pages[0].issues[0].number == 1


@respx.mock
def test_issues_retry_exhausted() -> None:
    route = respx.get("https://api.github.test/repos/owner/repo/issues").mock(
        return_value=httpx.Response(503)
    )
    with pytest.raises(GitHubTemporaryError):
        list(make_client(sleeps=[]).iter_issues_pages("owner", "repo", per_page=50, max_pages=10))
    assert route.call_count == 3


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(429, headers={"Retry-After": "9", "X-GitHub-Request-Id": "rl-429"}),
        httpx.Response(
            403,
            headers={"X-RateLimit-Remaining": "0", "X-GitHub-Request-Id": "rl-403"},
        ),
    ],
)
@respx.mock
def test_issues_rate_limit_is_returned_without_sleep(response: httpx.Response) -> None:
    sleeps: list[float] = []
    route = respx.get("https://api.github.test/repos/owner/repo/issues").mock(return_value=response)
    with pytest.raises(GitHubRateLimitError) as exc_info:
        list(
            make_client(sleeps=sleeps).iter_issues_pages(
                "owner",
                "repo",
                per_page=50,
                max_pages=10,
            )
        )
    assert route.call_count == 1
    assert sleeps == []
    assert exc_info.value.details.github_request_id in {"rl-429", "rl-403"}


@respx.mock
def test_issues_token_is_not_in_logs_or_exception(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    token = "ghp_issues_secret"
    respx.get("https://api.github.test/repos/owner/repo/issues").mock(
        return_value=httpx.Response(404)
    )
    with pytest.raises(GitHubNotFoundError) as exc_info:
        list(make_client(token=token).iter_issues_pages("owner", "repo", per_page=50, max_pages=10))
    assert token not in str(exc_info.value)
    assert token not in caplog.text
