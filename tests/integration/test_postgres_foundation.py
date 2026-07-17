from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from alembic import command
from github_data_sync_service.core.config import Settings
from github_data_sync_service.db.models.issue import Issue
from github_data_sync_service.db.models.repository import Repository
from github_data_sync_service.db.models.sync_job import SyncJob, SyncJobStatus
from github_data_sync_service.github.errors import (
    GitHubBadResponseError,
    GitHubErrorDetails,
    GitHubRateLimitError,
)
from github_data_sync_service.github.models import (
    GitHubIssue,
    GitHubIssuePage,
    GitHubRateLimit,
    GitHubRepository,
)
from github_data_sync_service.queue.repository import SyncJobStore
from github_data_sync_service.repositories.repository import RepositoryStore
from github_data_sync_service.worker.processor import IssueSyncProcessor

pytestmark = pytest.mark.integration


def github_repo(
    *,
    github_id: int = 1,
    owner: str = "fastapi",
    name: str = "fastapi",
) -> GitHubRepository:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    return GitHubRepository(
        github_id=github_id,
        owner=owner,
        name=name,
        full_name=f"{owner}/{name}",
        html_url=f"https://github.com/{owner}/{name}",
        description="description",
        default_branch="master",
        is_fork=False,
        is_archived=False,
        is_private=False,
        github_created_at=now,
        github_updated_at=now,
        github_request_id="request-id",
        rate_limit=GitHubRateLimit(limit=60, remaining=59, reset=123, retry_after_seconds=None),
    )


def test_migrations_upgrade_downgrade(test_database_url: str) -> None:
    cfg = Config("alembic.ini")
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")


def test_insert_and_idempotent_update(db_session: Session) -> None:
    store = RepositoryStore(db_session)
    first = store.upsert_from_github(github_repo(github_id=10))
    second = store.upsert_from_github(github_repo(github_id=10))
    assert first.created is True
    assert second.created is False
    assert first.repository.id == second.repository.id
    assert db_session.query(Repository).count() == 1


def test_rename_for_same_github_id_updates_metadata(db_session: Session) -> None:
    store = RepositoryStore(db_session)
    first = store.upsert_from_github(github_repo(github_id=11, owner="old", name="repo"))
    second = store.upsert_from_github(github_repo(github_id=11, owner="new", name="repo"))
    assert first.repository.id == second.repository.id
    assert second.repository.owner == "new"
    assert second.repository.full_name == "new/repo"


def test_case_insensitive_owner_name_unique_constraint(db_session: Session) -> None:
    store = RepositoryStore(db_session)
    store.upsert_from_github(github_repo(github_id=12, owner="FastAPI", name="FastAPI"))
    with pytest.raises(IntegrityError):
        store.upsert_from_github(github_repo(github_id=13, owner="fastapi", name="fastapi"))


def test_list_and_get_repositories(db_session: Session) -> None:
    store = RepositoryStore(db_session)
    created = store.upsert_from_github(github_repo(github_id=14)).repository
    items = store.list(limit=10, offset=0)
    assert [item.id for item in items] == [created.id]
    assert store.get(created.id).github_id == 14  # type: ignore[union-attr]
    assert store.get(uuid.uuid4()) is None


def test_ready_query(migrated_engine: Engine) -> None:
    with migrated_engine.connect() as conn:
        assert conn.execute(text("SELECT 1")).scalar_one() == 1


def add_repository_and_job(session: Session, *, resource_type: str = "issues") -> SyncJob:
    repo = RepositoryStore(session).upsert_from_github(github_repo(github_id=20)).repository
    job = SyncJob(
        repository_id=repo.id,
        resource_type=resource_type,
        status=SyncJobStatus.pending.value,
        attempt_count=0,
        current_page=1,
        fetched_count=0,
        created_count=0,
        updated_count=0,
        error_count=0,
    )
    session.add(job)
    session.commit()
    return job


def worker_settings() -> Settings:
    return Settings(
        GITHUB_ISSUES_PER_PAGE=100,
        GITHUB_MAX_PAGES_PER_SYNC=10,
        WORKER_RATE_LIMIT_FALLBACK_SECONDS=60,
        WORKER_STALE_JOB_TIMEOUT_SECONDS=300,
        WORKER_ID="worker-integration",
    )


def github_issue(number: int, *, title: str | None = None, updated_day: int = 2) -> GitHubIssue:
    return GitHubIssue(
        github_id=1000 + number,
        number=number,
        title=title or f"Issue {number}",
        body=None,
        state="open",
        state_reason=None,
        html_url=f"https://github.com/fastapi/fastapi/issues/{number}",
        author_login="alice",
        comments_count=0,
        is_locked=False,
        github_created_at=datetime(2026, 1, 1, tzinfo=UTC),
        github_updated_at=datetime(2026, 1, updated_day, tzinfo=UTC),
        github_closed_at=None,
    )


def github_page(
    issues: tuple[GitHubIssue, ...],
    *,
    fetched_count: int | None = None,
    skipped: int = 0,
    request_id: str = "request-page",
) -> GitHubIssuePage:
    return GitHubIssuePage(
        issues=issues,
        fetched_count=fetched_count if fetched_count is not None else len(issues),
        skipped_pull_request_count=skipped,
        next_url=None,
        github_request_id=request_id,
        rate_limit=GitHubRateLimit(limit=60, remaining=59, reset=None, retry_after_seconds=None),
    )


class FakeIssuesClient:
    def __init__(self, sequence: list[GitHubIssuePage | Exception]) -> None:
        self.sequence = sequence

    def iter_issues_pages(
        self,
        owner: str,
        repo: str,
        *,
        per_page: int,
        max_pages: int,
    ) -> Iterator[GitHubIssuePage]:
        for item in self.sequence:
            if isinstance(item, Exception):
                raise item
            yield item


def run_claimed_job(
    session: Session,
    client: FakeIssuesClient,
    *,
    now: datetime | None = None,
) -> SyncJob:
    store = SyncJobStore(session)
    claimed = store.claim_available_job(worker_id="worker-integration", now=now)
    assert claimed is not None
    IssueSyncProcessor(
        store=store,
        github_client=client,
        settings=worker_settings(),
        now=lambda: now or datetime(2026, 1, 1, tzinfo=UTC),
    ).process(claimed)
    refreshed = session.get(SyncJob, claimed.id)
    assert refreshed is not None
    return refreshed


def test_claim_available_job_uses_skip_locked(db_session: Session) -> None:
    job = add_repository_and_job(db_session)
    claimed = SyncJobStore(db_session).claim_available_job(worker_id="worker-1")
    assert claimed is not None
    assert claimed.id == job.id
    assert claimed.status == SyncJobStatus.running.value
    assert claimed.locked_by == "worker-1"


def test_two_claimants_do_not_get_same_job(migrated_engine: Engine) -> None:
    factory = sessionmaker(bind=migrated_engine, autoflush=False, expire_on_commit=False)
    setup_session = factory()
    try:
        with migrated_engine.begin() as conn:
            conn.execute(text("TRUNCATE sync_jobs, repositories RESTART IDENTITY CASCADE"))
        add_repository_and_job(setup_session)
    finally:
        setup_session.close()

    first_session = factory()
    second_session = factory()
    try:
        first = SyncJobStore(first_session).claim_available_job(worker_id="first")
        second = SyncJobStore(second_session).claim_available_job(worker_id="second")
        assert first is not None
        assert second is None
    finally:
        first_session.close()
        second_session.close()


def test_claim_lock_is_released_after_short_transaction(migrated_engine: Engine) -> None:
    factory = sessionmaker(bind=migrated_engine, autoflush=False, expire_on_commit=False)
    session = factory()
    try:
        with migrated_engine.begin() as conn:
            conn.execute(text("TRUNCATE sync_jobs, repositories RESTART IDENTITY CASCADE"))
        add_repository_and_job(session)
        claimed = SyncJobStore(session).claim_available_job(worker_id="worker")
        assert claimed is not None
        with migrated_engine.connect() as conn:
            status = conn.execute(
                text("SELECT status FROM sync_jobs WHERE id = :id"), {"id": str(claimed.id)}
            ).scalar_one()
        assert status == SyncJobStatus.running.value
    finally:
        session.close()


def test_fail_job_marks_unsupported_resource(db_session: Session) -> None:
    job = add_repository_and_job(db_session, resource_type="unknown")
    store = SyncJobStore(db_session)
    claimed = store.claim_available_job(worker_id="worker")
    assert claimed is not None
    store.fail_job(claimed.id, "Unsupported resource_type: unknown")
    failed = db_session.get(SyncJob, job.id)
    assert failed.status == SyncJobStatus.failed.value  # type: ignore[union-attr]
    assert "Unsupported" in failed.last_error  # type: ignore[union-attr]


def test_issue_sync_first_rerun_update_and_no_duplicates(db_session: Session) -> None:
    repo = RepositoryStore(db_session).upsert_from_github(github_repo(github_id=30)).repository
    store = SyncJobStore(db_session)

    first_job = store.create_or_get_active_job(repository_id=repo.id, resource_type="issues").job
    first = run_claimed_job(
        db_session,
        FakeIssuesClient(
            [
                github_page(
                    (github_issue(1), github_issue(3)),
                    fetched_count=3,
                    skipped=1,
                    request_id="page-1",
                ),
                github_page((github_issue(4),), request_id="page-2"),
            ]
        ),
    )
    assert first.id == first_job.id
    assert first.status == SyncJobStatus.completed.value
    assert first.current_page == 2
    assert first.fetched_count == 4
    assert first.skipped_count == 1
    assert first.created_count == 3
    assert first.updated_count == 0
    assert first.unchanged_count == 0
    assert [issue.number for issue in db_session.query(Issue).order_by(Issue.number)] == [1, 3, 4]

    second_job = store.create_or_get_active_job(repository_id=repo.id, resource_type="issues").job
    second = run_claimed_job(
        db_session,
        FakeIssuesClient(
            [
                github_page((github_issue(1), github_issue(3)), fetched_count=3, skipped=1),
                github_page((github_issue(4),)),
            ]
        ),
    )
    assert second.id == second_job.id
    assert second.created_count == 0
    assert second.updated_count == 0
    assert second.unchanged_count == 3
    assert db_session.query(Issue).count() == 3

    third_job = store.create_or_get_active_job(repository_id=repo.id, resource_type="issues").job
    third = run_claimed_job(
        db_session,
        FakeIssuesClient(
            [
                github_page(
                    (
                        github_issue(1),
                        github_issue(3, title="Updated issue 3", updated_day=3),
                    ),
                    fetched_count=3,
                    skipped=1,
                ),
                github_page((github_issue(4),)),
            ]
        ),
    )
    assert third.id == third_job.id
    assert third.created_count == 0
    assert third.updated_count == 1
    assert third.unchanged_count == 2
    assert db_session.query(Issue).count() == 3


def test_issue_sync_rate_limit_after_first_page_preserves_data(db_session: Session) -> None:
    repo = RepositoryStore(db_session).upsert_from_github(github_repo(github_id=31)).repository
    store = SyncJobStore(db_session)
    now = datetime(2026, 1, 1, tzinfo=UTC)
    store.create_or_get_active_job(repository_id=repo.id, resource_type="issues", now=now)
    rate_limit_error = GitHubRateLimitError(
        "GitHub rate limit exceeded",
        GitHubErrorDetails(retry_after_seconds=30, github_request_id="rate-limit"),
    )
    result = run_claimed_job(
        db_session,
        FakeIssuesClient([github_page((github_issue(1),)), rate_limit_error]),
        now=now,
    )
    assert result.status == SyncJobStatus.rate_limited.value
    assert result.available_at == now + timedelta(seconds=30)
    assert result.finished_at is None
    assert result.locked_by is None
    assert db_session.query(Issue).count() == 1


def test_issue_sync_later_page_failure_does_not_complete(db_session: Session) -> None:
    repo = RepositoryStore(db_session).upsert_from_github(github_repo(github_id=32)).repository
    store = SyncJobStore(db_session)
    store.create_or_get_active_job(repository_id=repo.id, resource_type="issues")
    result = run_claimed_job(
        db_session,
        FakeIssuesClient(
            [
                github_page((github_issue(1),)),
                GitHubBadResponseError("bad third page", GitHubErrorDetails(status_code=200)),
            ]
        ),
    )
    assert result.status == SyncJobStatus.failed.value
    assert result.finished_at is not None
    assert result.last_error is not None
    assert "bad third page" in result.last_error
    assert db_session.query(Issue).count() == 1


def test_active_job_unique_constraint_returns_existing(db_session: Session) -> None:
    repo = RepositoryStore(db_session).upsert_from_github(github_repo(github_id=33)).repository
    store = SyncJobStore(db_session)
    first = store.create_or_get_active_job(repository_id=repo.id, resource_type="issues")
    second = store.create_or_get_active_job(repository_id=repo.id, resource_type="issues")
    assert first.created is True
    assert second.created is False
    assert second.job.id == first.job.id

    store.fail_job(first.job.id, "boom")
    third = store.create_or_get_active_job(repository_id=repo.id, resource_type="issues")
    assert third.created is True
    assert third.job.id != first.job.id


def test_recover_stale_running_job_only(db_session: Session) -> None:
    repo = RepositoryStore(db_session).upsert_from_github(github_repo(github_id=34)).repository
    old = datetime(2026, 1, 1, tzinfo=UTC)
    fresh = datetime(2026, 1, 1, 0, 10, tzinfo=UTC)
    stale_job = SyncJob(
        repository_id=repo.id,
        resource_type="issues",
        status=SyncJobStatus.running.value,
        available_at=old,
        locked_at=old,
        locked_by="old-worker",
        heartbeat_at=old,
        current_page=1,
        fetched_count=1,
        skipped_count=0,
        created_count=1,
        updated_count=0,
        unchanged_count=0,
        error_count=0,
    )
    fresh_job = SyncJob(
        repository_id=repo.id,
        resource_type="commits",
        status=SyncJobStatus.running.value,
        available_at=fresh,
        locked_at=fresh,
        locked_by="fresh-worker",
        heartbeat_at=fresh,
        current_page=1,
        fetched_count=1,
        skipped_count=0,
        created_count=1,
        updated_count=0,
        unchanged_count=0,
        error_count=0,
    )
    db_session.add_all([stale_job, fresh_job])
    db_session.commit()

    recovered = SyncJobStore(db_session).recover_stale_running_jobs(
        now=fresh,
        timeout_seconds=300,
    )
    assert recovered == 1
    db_session.refresh(stale_job)
    db_session.refresh(fresh_job)
    assert stale_job.status == SyncJobStatus.pending.value
    assert stale_job.locked_by is None
    assert stale_job.error_count == 1
    assert fresh_job.status == SyncJobStatus.running.value
