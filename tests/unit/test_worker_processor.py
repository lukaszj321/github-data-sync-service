from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from github_data_sync_service.core.config import Settings
from github_data_sync_service.github.errors import GitHubErrorDetails, GitHubRateLimitError
from github_data_sync_service.github.models import GitHubIssue, GitHubIssuePage, GitHubRateLimit
from github_data_sync_service.issues.repository import IssueUpsertCounts
from github_data_sync_service.worker.processor import IssueSyncProcessor


def settings() -> Settings:
    return Settings(
        GITHUB_ISSUES_PER_PAGE=100,
        GITHUB_MAX_PAGES_PER_SYNC=10,
        WORKER_RATE_LIMIT_FALLBACK_SECONDS=60,
        WORKER_STALE_JOB_TIMEOUT_SECONDS=300,
        WORKER_ID="worker-test",
    )


def issue(number: int) -> GitHubIssue:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    return GitHubIssue(
        github_id=number,
        number=number,
        title=f"Issue {number}",
        body=None,
        state="open",
        state_reason=None,
        html_url=f"https://github.com/owner/repo/issues/{number}",
        author_login=None,
        comments_count=0,
        is_locked=False,
        github_created_at=now,
        github_updated_at=now,
        github_closed_at=None,
    )


def page(number: int) -> GitHubIssuePage:
    return GitHubIssuePage(
        issues=(issue(number),),
        fetched_count=1,
        skipped_pull_request_count=0,
        next_url=None,
        github_request_id=f"request-{number}",
        rate_limit=GitHubRateLimit(limit=60, remaining=59, reset=None, retry_after_seconds=None),
    )


def job(resource_type: str = "issues") -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        repository_id=uuid.uuid4(),
        repository=SimpleNamespace(owner="owner", name="repo"),
        resource_type=resource_type,
        current_page=0,
        attempt_count=1,
    )


class FakeClient:
    def __init__(self, pages: list[GitHubIssuePage] | None = None, error: Exception | None = None):
        self.pages = pages or []
        self.error = error

    def iter_issues_pages(
        self,
        owner: str,
        repo: str,
        *,
        per_page: int,
        max_pages: int,
    ) -> Iterator[GitHubIssuePage]:
        if self.error is not None:
            raise self.error
        yield from self.pages


class FakeStore:
    def __init__(self) -> None:
        self.recorded_pages: list[int] = []
        self.completed: uuid.UUID | None = None
        self.failed: tuple[uuid.UUID, str] | None = None
        self.rate_limited: datetime | None = None

    def record_issue_page(
        self,
        *,
        job_id: uuid.UUID,
        repository_id: uuid.UUID,
        page_number: int,
        page: GitHubIssuePage,
        now: datetime | None = None,
    ) -> IssueUpsertCounts:
        self.recorded_pages.append(page_number)
        return IssueUpsertCounts(created=len(page.issues), updated=0, unchanged=0)

    def complete_job(self, job_id: uuid.UUID, *, now: datetime | None = None) -> None:
        self.completed = job_id

    def fail_job(self, job_id: uuid.UUID, message: str) -> None:
        self.failed = (job_id, message)

    def rate_limit_job(
        self,
        job_id: uuid.UUID,
        error: GitHubRateLimitError,
        *,
        available_at: datetime,
    ) -> None:
        self.rate_limited = available_at


def test_processor_handles_issues_job() -> None:
    store = FakeStore()
    current_time = datetime(2026, 1, 1, tzinfo=UTC)
    sync_job = job()
    IssueSyncProcessor(
        store=store,  # type: ignore[arg-type]
        github_client=FakeClient([page(1), page(2)]),
        settings=settings(),
        now=lambda: current_time,
    ).process(sync_job)  # type: ignore[arg-type]
    assert store.recorded_pages == [1, 2]
    assert store.completed == sync_job.id
    assert store.failed is None


def test_processor_fails_unsupported_resource_type() -> None:
    store = FakeStore()
    sync_job = job("commits")
    IssueSyncProcessor(
        store=store,  # type: ignore[arg-type]
        github_client=FakeClient(),
        settings=settings(),
    ).process(sync_job)  # type: ignore[arg-type]
    assert store.failed is not None
    assert "Unsupported resource_type" in store.failed[1]


def test_processor_rate_limit_uses_retry_after() -> None:
    store = FakeStore()
    now = datetime(2026, 1, 1, tzinfo=UTC)
    error = GitHubRateLimitError(
        "GitHub rate limit exceeded",
        GitHubErrorDetails(retry_after_seconds=9, github_request_id="request"),
    )
    IssueSyncProcessor(
        store=store,  # type: ignore[arg-type]
        github_client=FakeClient(error=error),
        settings=settings(),
        now=lambda: now,
    ).process(job())  # type: ignore[arg-type]
    assert store.rate_limited == now + timedelta(seconds=9)


def test_processor_rate_limit_uses_fallback_seconds() -> None:
    store = FakeStore()
    now = datetime(2026, 1, 1, tzinfo=UTC)
    error = GitHubRateLimitError("GitHub rate limit exceeded", GitHubErrorDetails())
    IssueSyncProcessor(
        store=store,  # type: ignore[arg-type]
        github_client=FakeClient(error=error),
        settings=settings(),
        now=lambda: now,
    ).process(job())  # type: ignore[arg-type]
    assert store.rate_limited == now + timedelta(seconds=60)
