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
from github_data_sync_service.db.models.resource_sync_state import ResourceSyncState
from github_data_sync_service.db.models.sync_job import SyncJob, SyncJobStatus, SyncMode
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
        sync_mode=SyncMode.full.value,
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
        ISSUES_SYNC_OVERLAP_SECONDS=60,
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
        self.calls: list[dict[str, object]] = []

    def iter_issues_pages(
        self,
        owner: str,
        repo: str,
        *,
        per_page: int,
        max_pages: int,
        since: datetime | None = None,
    ) -> Iterator[GitHubIssuePage]:
        self.calls.append(
            {
                "owner": owner,
                "repo": repo,
                "per_page": per_page,
                "max_pages": max_pages,
                "since": since,
            }
        )
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

    first_started = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    first_job = store.create_or_get_active_job(
        repository_id=repo.id,
        resource_type="issues",
        now=first_started,
    ).job
    first_client = FakeIssuesClient(
        [
            github_page(
                (github_issue(1), github_issue(3)),
                fetched_count=3,
                skipped=1,
                request_id="page-1",
            ),
            github_page((github_issue(4),), request_id="page-2"),
        ]
    )
    first = run_claimed_job(
        db_session,
        first_client,
        now=first_started,
    )
    assert first.id == first_job.id
    assert first.sync_mode == SyncMode.full.value
    assert first.cursor_before is None
    assert first.since_at is None
    assert first.cursor_after == first_started
    assert first_client.calls[0]["since"] is None
    assert first.status == SyncJobStatus.completed.value
    assert first.current_page == 2
    assert first.fetched_count == 4
    assert first.skipped_count == 1
    assert first.created_count == 3
    assert first.updated_count == 0
    assert first.unchanged_count == 0
    assert [issue.number for issue in db_session.query(Issue).order_by(Issue.number)] == [1, 3, 4]
    state = db_session.query(ResourceSyncState).filter_by(repository_id=repo.id).one()
    assert state.resource_type == "issues"
    assert state.cursor_at == first.sync_window_started_at
    assert state.last_successful_job_id == first.id
    assert state.last_sync_mode == SyncMode.full.value

    second_started = datetime(2026, 1, 1, 0, 5, tzinfo=UTC)
    second_job = store.create_or_get_active_job(
        repository_id=repo.id,
        resource_type="issues",
        now=second_started,
    ).job
    second_client = FakeIssuesClient(
        [
            github_page((github_issue(1), github_issue(3)), fetched_count=3, skipped=1),
            github_page((github_issue(4),)),
        ]
    )
    second = run_claimed_job(
        db_session,
        second_client,
        now=second_started,
    )
    assert second.id == second_job.id
    assert second.sync_mode == SyncMode.incremental.value
    assert second.cursor_before == first.sync_window_started_at
    assert second.since_at == first.sync_window_started_at - timedelta(seconds=60)
    assert second_client.calls[0]["since"] == second.since_at
    assert second.created_count == 0
    assert second.updated_count == 0
    assert second.unchanged_count == 3
    assert db_session.query(Issue).count() == 3

    third_started = datetime(2026, 1, 1, 0, 10, tzinfo=UTC)
    third_job = store.create_or_get_active_job(
        repository_id=repo.id,
        resource_type="issues",
        now=third_started,
    ).job
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
        now=third_started,
    )
    assert third.id == third_job.id
    assert third.created_count == 0
    assert third.updated_count == 1
    assert third.unchanged_count == 2
    assert db_session.query(Issue).count() == 3
    db_session.refresh(state)
    assert state.cursor_at == third.sync_window_started_at
    assert state.last_successful_job_id == third.id
    assert state.last_sync_mode == SyncMode.incremental.value


def test_explicit_full_keeps_cursor_before_without_since(db_session: Session) -> None:
    repo = RepositoryStore(db_session).upsert_from_github(github_repo(github_id=36)).repository
    store = SyncJobStore(db_session)
    first_started = datetime(2026, 1, 1, 1, 0, tzinfo=UTC)
    first_job = store.create_or_get_active_job(
        repository_id=repo.id,
        resource_type="issues",
        now=first_started,
    ).job
    first = run_claimed_job(
        db_session,
        FakeIssuesClient([github_page(())]),
        now=first_started,
    )
    assert first.id == first_job.id

    full_job = store.create_or_get_active_job(
        repository_id=repo.id,
        resource_type="issues",
        requested_mode=SyncMode.full.value,
    ).job
    assert full_job.sync_mode == SyncMode.full.value
    assert full_job.cursor_before == first.sync_window_started_at
    assert full_job.since_at is None


def test_active_job_is_returned_even_when_requested_mode_differs(db_session: Session) -> None:
    repo = RepositoryStore(db_session).upsert_from_github(github_repo(github_id=37)).repository
    store = SyncJobStore(db_session)
    first = store.create_or_get_active_job(
        repository_id=repo.id,
        resource_type="issues",
        requested_mode=SyncMode.incremental.value,
    )
    second = store.create_or_get_active_job(
        repository_id=repo.id,
        resource_type="issues",
        requested_mode=SyncMode.full.value,
    )
    assert first.created is True
    assert second.created is False
    assert second.job.id == first.job.id
    assert second.job.sync_mode == first.job.sync_mode


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
    assert result.cursor_after is None
    assert result.locked_by is None
    assert db_session.query(Issue).count() == 1
    assert db_session.query(ResourceSyncState).count() == 0


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
    assert result.cursor_after is None
    assert result.last_error is not None
    assert "bad third page" in result.last_error
    assert db_session.query(Issue).count() == 1
    assert db_session.query(ResourceSyncState).count() == 0


def test_completion_never_moves_cursor_backwards(db_session: Session) -> None:
    repo = RepositoryStore(db_session).upsert_from_github(github_repo(github_id=38)).repository
    old_window = datetime(2026, 1, 1, 2, 0, tzinfo=UTC)
    new_window = datetime(2026, 1, 1, 2, 5, tzinfo=UTC)
    old_job = SyncJob(
        repository_id=repo.id,
        resource_type="issues",
        sync_mode=SyncMode.full.value,
        status=SyncJobStatus.running.value,
        sync_window_started_at=old_window,
        attempt_count=1,
        current_page=0,
        fetched_count=0,
        skipped_count=0,
        created_count=0,
        updated_count=0,
        unchanged_count=0,
        error_count=0,
    )
    new_job = SyncJob(
        repository_id=repo.id,
        resource_type="issues",
        sync_mode=SyncMode.incremental.value,
        status=SyncJobStatus.completed.value,
        sync_window_started_at=new_window,
        cursor_after=new_window,
        finished_at=datetime(2026, 1, 1, 2, 6, tzinfo=UTC),
        attempt_count=1,
        current_page=0,
        fetched_count=0,
        skipped_count=0,
        created_count=0,
        updated_count=0,
        unchanged_count=0,
        error_count=0,
    )
    db_session.add_all([old_job, new_job])
    db_session.commit()
    state = ResourceSyncState(
        repository_id=repo.id,
        resource_type="issues",
        cursor_at=new_window,
        last_successful_job_id=new_job.id,
        last_sync_mode=SyncMode.incremental.value,
        last_started_at=new_window,
        last_completed_at=datetime(2026, 1, 1, 2, 6, tzinfo=UTC),
    )
    db_session.add(state)
    db_session.commit()

    store = SyncJobStore(db_session)
    store.complete_job(old_job.id, now=datetime(2026, 1, 1, 2, 7, tzinfo=UTC))

    db_session.refresh(state)
    assert state.cursor_at == new_window
    assert state.last_successful_job_id == new_job.id
    assert state.last_sync_mode == SyncMode.incremental.value
    db_session.refresh(old_job)
    assert old_job.status == SyncJobStatus.completed.value
    assert old_job.cursor_after == old_window


def test_issue_page_commit_failure_rolls_back_whole_page(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = RepositoryStore(db_session).upsert_from_github(github_repo(github_id=35)).repository
    store = SyncJobStore(db_session)
    job = store.create_or_get_active_job(repository_id=repo.id, resource_type="issues").job
    claimed = store.claim_available_job(worker_id="worker-integration")
    assert claimed is not None

    first_page = github_page((github_issue(1),), request_id="page-1")
    store.record_issue_page(
        job_id=job.id,
        repository_id=repo.id,
        page_number=1,
        page=first_page,
    )
    db_session.refresh(job)
    assert db_session.query(Issue).count() == 1
    assert job.current_page == 1
    assert job.fetched_count == 1
    assert job.created_count == 1

    original_commit = db_session.commit

    def fail_commit_once() -> None:
        monkeypatch.setattr(db_session, "commit", original_commit)
        raise RuntimeError("commit failed after upsert")

    monkeypatch.setattr(db_session, "commit", fail_commit_once)
    with pytest.raises(RuntimeError, match="commit failed after upsert"):
        store.record_issue_page(
            job_id=job.id,
            repository_id=repo.id,
            page_number=2,
            page=github_page((github_issue(2),), request_id="page-2"),
        )

    db_session.expire_all()
    failed_job = db_session.get(SyncJob, job.id)
    assert failed_job is not None
    assert db_session.query(Issue).count() == 1
    assert [issue.number for issue in db_session.query(Issue).order_by(Issue.number)] == [1]
    assert failed_job.current_page == 1
    assert failed_job.fetched_count == 1
    assert failed_job.created_count == 1

    store.fail_job(job.id, "internal_error: RuntimeError")
    db_session.refresh(failed_job)
    assert failed_job.status == SyncJobStatus.failed.value
    assert failed_job.locked_by is None


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
        sync_mode=SyncMode.incremental.value,
        status=SyncJobStatus.running.value,
        cursor_before=old - timedelta(minutes=1),
        since_at=old - timedelta(minutes=2),
        sync_window_started_at=old,
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
        sync_mode=SyncMode.full.value,
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
    assert stale_job.cursor_before == old - timedelta(minutes=1)
    assert stale_job.since_at == old - timedelta(minutes=2)
    assert stale_job.sync_window_started_at == old
    assert fresh_job.status == SyncJobStatus.running.value
