from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from alembic import command
from github_data_sync_service.db.models.repository import Repository
from github_data_sync_service.db.models.sync_job import SyncJob, SyncJobStatus
from github_data_sync_service.github.models import GitHubRateLimit, GitHubRepository
from github_data_sync_service.queue.repository import SyncJobStore
from github_data_sync_service.repositories.repository import RepositoryStore

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
