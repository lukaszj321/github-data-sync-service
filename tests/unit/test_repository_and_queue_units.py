from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from github_data_sync_service.core.errors import AppError
from github_data_sync_service.db.models.resource_sync_state import ResourceSyncState
from github_data_sync_service.db.models.sync_job import SyncJobStatus
from github_data_sync_service.github.errors import GitHubErrorDetails, GitHubRateLimitError
from github_data_sync_service.github.models import (
    GitHubIssue,
    GitHubIssuePage,
    GitHubRateLimit,
    GitHubRepository,
)
from github_data_sync_service.issues.repository import IssuesStore
from github_data_sync_service.issues.service import IssueService
from github_data_sync_service.queue.repository import SyncJobStore
from github_data_sync_service.queue.service import SyncJobService
from github_data_sync_service.repositories.repository import RepositoryStore


def sample_repo() -> GitHubRepository:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    return GitHubRepository(
        github_id=1,
        owner="owner",
        name="repo",
        full_name="owner/repo",
        html_url="https://github.com/owner/repo",
        description=None,
        default_branch="main",
        is_fork=False,
        is_archived=False,
        is_private=False,
        github_created_at=now,
        github_updated_at=now,
        github_request_id="request",
        rate_limit=GitHubRateLimit(None, None, None, None),
    )


class OneRowResult:
    def __init__(self, row: tuple[object, bool]) -> None:
        self.row = row

    def one(self) -> tuple[object, bool]:
        return self.row


class ScalarResult:
    def __init__(self, items: list[object]) -> None:
        self.items = items

    def first(self) -> object | None:
        return self.items[0] if self.items else None

    def __iter__(self):
        return iter(self.items)

    def all(self) -> list[object]:
        return self.items


class ExecuteResult:
    def __init__(self, values: list[object]) -> None:
        self.values = values

    def scalars(self):
        return iter(self.values)


class Begin:
    def __enter__(self) -> None:
        return None

    def __exit__(self, *_: object) -> None:
        return None


class FakeRepositorySession:
    def __init__(self) -> None:
        self.repository = SimpleNamespace(id=uuid.uuid4(), github_id=1)
        self.committed = False

    def execute(self, statement: object) -> OneRowResult:
        return OneRowResult((self.repository, True))

    def commit(self) -> None:
        self.committed = True

    def refresh(self, repository: object) -> None:
        return None

    def scalars(self, statement: object) -> ScalarResult:
        return ScalarResult([self.repository])

    def get(self, model: object, repository_id: uuid.UUID) -> object:
        return self.repository


def test_repository_store_upsert_list_get() -> None:
    session = FakeRepositorySession()
    store = RepositoryStore(session)  # type: ignore[arg-type]
    result = store.upsert_from_github(sample_repo())
    assert result.created is True
    assert result.repository is session.repository
    assert session.committed is True
    assert store.list(limit=10, offset=0) == [session.repository]
    assert store.get(uuid.uuid4()) is session.repository


class FakeQueueSession:
    def __init__(
        self,
        job: object | None,
        *,
        state: object | None = None,
        execute_values: list[object] | None = None,
    ) -> None:
        self.job = job
        self.state = state
        self.execute_values = execute_values or []
        self.added: object | None = None
        self.committed = False
        self.rolled_back = False

    def begin(self) -> Begin:
        return Begin()

    def scalars(self, statement: object) -> ScalarResult:
        if "resource_sync_states" in str(statement):
            return ScalarResult([] if self.state is None else [self.state])
        return ScalarResult([] if self.job is None else [self.job])

    def get(self, model: object, job_id: uuid.UUID) -> object | None:
        return self.job

    def add(self, item: object) -> None:
        self.added = item

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.rolled_back = True

    def refresh(self, item: object) -> None:
        return None

    def execute(self, statement: object) -> ExecuteResult:
        return ExecuteResult(self.execute_values)


def test_queue_claim_empty() -> None:
    assert SyncJobStore(FakeQueueSession(None)).claim_available_job(worker_id="worker") is None  # type: ignore[arg-type]


def test_queue_claim_and_fail() -> None:
    job = SimpleNamespace(
        id=uuid.uuid4(),
        status=SyncJobStatus.pending.value,
        locked_by=None,
        locked_at=None,
        heartbeat_at=None,
        started_at=None,
        sync_mode="full",
        cursor_before=None,
        since_at=None,
        cursor_after=None,
        sync_window_started_at=None,
        attempt_count=0,
        finished_at=None,
        current_page=7,
        fetched_count=10,
        skipped_count=1,
        created_count=2,
        updated_count=3,
        unchanged_count=4,
        last_error=None,
        error_count=0,
    )
    store = SyncJobStore(FakeQueueSession(job))  # type: ignore[arg-type]
    claimed = store.claim_available_job(worker_id="worker")
    assert claimed is job
    assert job.status == SyncJobStatus.running.value
    assert job.locked_by == "worker"
    assert job.sync_window_started_at is not None
    assert job.current_page == 0
    assert job.fetched_count == 0
    assert job.skipped_count == 0
    assert job.unchanged_count == 0
    store.fail_job(job.id, "Unsupported resource_type")
    assert job.status == SyncJobStatus.failed.value
    assert job.error_count == 1
    assert job.locked_by is None


def test_queue_claim_rate_limited_retry_preserves_cursor_context() -> None:
    window_started_at = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    cursor_before = datetime(2025, 12, 31, 23, 59, tzinfo=UTC)
    since_at = datetime(2025, 12, 31, 23, 58, tzinfo=UTC)
    job = SimpleNamespace(
        id=uuid.uuid4(),
        status=SyncJobStatus.rate_limited.value,
        available_at=datetime(2026, 1, 1, 0, 1, tzinfo=UTC),
        locked_by=None,
        locked_at=None,
        heartbeat_at=None,
        started_at=None,
        sync_mode="incremental",
        cursor_before=cursor_before,
        since_at=since_at,
        cursor_after=None,
        sync_window_started_at=window_started_at,
        attempt_count=1,
        finished_at=None,
        current_page=3,
        fetched_count=10,
        skipped_count=1,
        created_count=2,
        updated_count=3,
        unchanged_count=4,
        last_error="rate limited",
        error_count=1,
    )
    claimed = SyncJobStore(FakeQueueSession(job)).claim_available_job(  # type: ignore[arg-type]
        worker_id="worker",
        now=datetime(2026, 1, 1, 0, 2, tzinfo=UTC),
    )
    assert claimed is job
    assert job.sync_mode == "incremental"
    assert job.cursor_before == cursor_before
    assert job.since_at == since_at
    assert job.sync_window_started_at == window_started_at
    assert job.attempt_count == 2
    assert job.current_page == 0
    assert job.fetched_count == 0
    assert job.skipped_count == 0
    assert job.created_count == 0
    assert job.updated_count == 0
    assert job.unchanged_count == 0


def test_queue_create_modes_and_overlap() -> None:
    repository_id = uuid.uuid4()
    now = datetime(2026, 1, 1, tzinfo=UTC)

    bootstrap_session = FakeQueueSession(None)
    SyncJobStore(bootstrap_session).create_or_get_active_job(  # type: ignore[arg-type]
        repository_id=repository_id,
        resource_type="issues",
        requested_mode="incremental",
        overlap_seconds=60,
        now=now,
    )
    bootstrap_job = bootstrap_session.added
    assert bootstrap_job is not None
    assert bootstrap_job.sync_mode == "full"
    assert bootstrap_job.cursor_before is None
    assert bootstrap_job.since_at is None

    cursor_at = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    state = SimpleNamespace(cursor_at=cursor_at)
    incremental_session = FakeQueueSession(None, state=state)
    SyncJobStore(incremental_session).create_or_get_active_job(  # type: ignore[arg-type]
        repository_id=repository_id,
        resource_type="issues",
        requested_mode="incremental",
        overlap_seconds=60,
        now=now,
    )
    incremental_job = incremental_session.added
    assert incremental_job is not None
    assert incremental_job.sync_mode == "incremental"
    assert incremental_job.cursor_before == cursor_at
    assert incremental_job.since_at == cursor_at - timedelta(seconds=60)

    overlap_zero_session = FakeQueueSession(None, state=state)
    SyncJobStore(overlap_zero_session).create_or_get_active_job(  # type: ignore[arg-type]
        repository_id=repository_id,
        resource_type="issues",
        requested_mode="incremental",
        overlap_seconds=0,
        now=now,
    )
    overlap_zero_job = overlap_zero_session.added
    assert overlap_zero_job is not None
    assert overlap_zero_job.since_at == cursor_at

    full_session = FakeQueueSession(None, state=state)
    SyncJobStore(full_session).create_or_get_active_job(  # type: ignore[arg-type]
        repository_id=repository_id,
        resource_type="issues",
        requested_mode="full",
        overlap_seconds=60,
        now=now,
    )
    full_job = full_session.added
    assert full_job is not None
    assert full_job.sync_mode == "full"
    assert full_job.cursor_before == cursor_at
    assert full_job.since_at is None


def test_queue_create_list_get_complete_rate_limit_and_recover() -> None:
    job = SimpleNamespace(
        id=uuid.uuid4(),
        repository_id=uuid.uuid4(),
        resource_type="issues",
        status=SyncJobStatus.pending.value,
        available_at=datetime(2026, 1, 1, tzinfo=UTC),
        locked_by="worker",
        locked_at=datetime(2026, 1, 1, tzinfo=UTC),
        heartbeat_at=datetime(2026, 1, 1, tzinfo=UTC),
        started_at=None,
        finished_at=None,
        sync_mode="full",
        cursor_before=None,
        since_at=None,
        cursor_after=None,
        sync_window_started_at=None,
        current_page=0,
        fetched_count=0,
        skipped_count=0,
        created_count=0,
        updated_count=0,
        unchanged_count=0,
        error_count=0,
        last_error="old",
        github_request_id=None,
        rate_limit_remaining=None,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    session = FakeQueueSession(job, execute_values=[True, False])
    store = SyncJobStore(session)  # type: ignore[arg-type]
    created = store.create_or_get_active_job(
        repository_id=job.repository_id,
        resource_type="issues",
    )
    assert created.created is True
    assert session.added is not None
    assert store.list(limit=10, offset=0) == [job]
    assert store.get(job.id) is job
    assert store.get_active_job(repository_id=job.repository_id, resource_type="issues") is job

    page = GitHubIssuePage(
        issues=(sample_issue(1), sample_issue(2), sample_issue(3)),
        fetched_count=4,
        skipped_pull_request_count=1,
        next_url=None,
        github_request_id="request",
        rate_limit=GitHubRateLimit(limit=60, remaining=58, reset=None, retry_after_seconds=None),
    )
    counts = store.record_issue_page(
        job_id=job.id,
        repository_id=job.repository_id,
        page_number=1,
        page=page,
    )
    assert counts.created == 1
    assert counts.updated == 1
    assert counts.unchanged == 1
    assert job.fetched_count == 4
    assert job.skipped_count == 1

    job.status = SyncJobStatus.running.value
    job.sync_window_started_at = datetime(2026, 1, 1, 0, 2, tzinfo=UTC)
    store.complete_job(job.id)
    assert job.status == SyncJobStatus.completed.value
    assert isinstance(session.added, ResourceSyncState)
    assert session.added.cursor_at == job.sync_window_started_at
    assert session.added.last_successful_job_id == job.id
    assert session.added.last_sync_mode == "full"
    assert job.cursor_after == job.sync_window_started_at
    assert job.locked_by is None
    assert job.last_error is None

    error = GitHubRateLimitError(
        "limited",
        GitHubErrorDetails(github_request_id="rl", rate_limit_remaining=0),
    )
    available_at = datetime(2026, 1, 1, 0, 1, tzinfo=UTC)
    store.rate_limit_job(job.id, error, available_at=available_at)
    assert job.status == SyncJobStatus.rate_limited.value
    assert job.available_at == available_at
    assert job.cursor_after is None
    assert job.error_count == 1

    job.status = SyncJobStatus.running.value
    recovered = store.recover_stale_running_jobs(
        now=datetime(2026, 1, 1, 0, 10, tzinfo=UTC),
        timeout_seconds=300,
    )
    assert recovered == 1


def sample_issue(number: int) -> GitHubIssue:
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


def test_issues_store_empty_upsert_and_list() -> None:
    session = FakeQueueSession(SimpleNamespace(id=uuid.uuid4()))
    store = IssuesStore(session)  # type: ignore[arg-type]
    assert store.upsert_page(repository_id=uuid.uuid4(), issues=()).created == 0
    assert store.list(repository_id=uuid.uuid4(), limit=10, offset=0) == [session.job]


def test_issue_and_sync_job_services() -> None:
    repository = SimpleNamespace(id=uuid.uuid4())
    repository_store = SimpleNamespace(get=lambda repository_id: repository)
    issue_store = SimpleNamespace(list=lambda **kwargs: ["issue"])
    issue_service = IssueService(issue_store, repository_store)  # type: ignore[arg-type]
    assert issue_service.list(repository_id=repository.id, limit=10, offset=0, state="open") == [
        "issue"
    ]
    try:
        issue_service.list(repository_id=repository.id, limit=10, offset=0, state="bad")
    except AppError as exc:
        assert exc.error.code == "invalid_issue_state"
    else:
        raise AssertionError("invalid state should fail")

    job = SimpleNamespace(id=uuid.uuid4())
    job_store = SimpleNamespace(
        create_or_get_active_job=lambda **kwargs: SimpleNamespace(job=job, created=True),
        list=lambda **kwargs: [job],
        get=lambda job_id: job,
    )
    sync_service = SyncJobService(job_store, repository_store)  # type: ignore[arg-type]
    result = sync_service.create_repository_sync(
        repository_id=repository.id,
        resource_type="issues",
    )
    assert result.created is True
    assert sync_service.list(
        limit=10,
        offset=0,
        repository_id=None,
        status=None,
        resource_type=None,
    ) == [job]
    assert sync_service.get(job.id) is job
    try:
        sync_service.create_repository_sync(repository_id=repository.id, resource_type="commits")
    except AppError as exc:
        assert exc.error.code == "unsupported_resource_type"
    else:
        raise AssertionError("unsupported resource type should fail")
