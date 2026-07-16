from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

from github_data_sync_service.db.models.sync_job import SyncJobStatus
from github_data_sync_service.github.models import GitHubRateLimit, GitHubRepository
from github_data_sync_service.queue.repository import SyncJobStore
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
    def __init__(self, job: object | None) -> None:
        self.job = job

    def begin(self) -> Begin:
        return Begin()

    def scalars(self, statement: object) -> ScalarResult:
        return ScalarResult([] if self.job is None else [self.job])

    def get(self, model: object, job_id: uuid.UUID) -> object | None:
        return self.job


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
        attempt_count=0,
        finished_at=None,
        last_error=None,
        error_count=0,
    )
    store = SyncJobStore(FakeQueueSession(job))  # type: ignore[arg-type]
    claimed = store.claim_available_job(worker_id="worker")
    assert claimed is job
    assert job.status == SyncJobStatus.running.value
    assert job.locked_by == "worker"
    store.fail_job(job.id, "Unsupported resource_type")
    assert job.status == SyncJobStatus.failed.value
    assert job.error_count == 1
