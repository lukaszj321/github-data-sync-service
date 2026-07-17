from __future__ import annotations

import argparse
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import ClassVar

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from github_data_sync_service.api import dependencies
from github_data_sync_service.api.routes.health import ready
from github_data_sync_service.core.config import Settings
from github_data_sync_service.core.errors import AppError
from github_data_sync_service.core.logging import JsonFormatter, configure_logging
from github_data_sync_service.db.session import (
    create_db_engine,
    create_session_factory,
    session_scope,
)
from github_data_sync_service.github.client import GitHubClient
from github_data_sync_service.worker.main import Worker, build_parser


def test_get_github_client_closes() -> None:
    settings = Settings(GITHUB_TOKEN="ghp_secret")
    iterator = dependencies.get_github_client(settings)
    client = next(iterator)
    assert isinstance(client, GitHubClient)
    with pytest.raises(StopIteration):
        next(iterator)


def test_get_db_session_closes() -> None:
    closed = False

    class FakeSession:
        def close(self) -> None:
            nonlocal closed
            closed = True

    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(session_factory=lambda: FakeSession()))
    )
    iterator = dependencies.get_db_session(request)
    assert next(iterator).__class__.__name__ == "FakeSession"
    with pytest.raises(StopIteration):
        next(iterator)
    assert closed is True


def test_repository_service_dependency() -> None:
    session = object()
    client = object()
    service = dependencies.get_repository_service(session, client)  # type: ignore[arg-type]
    assert service is not None


def test_init_app_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dependencies, "create_db_engine", lambda settings: "engine")
    monkeypatch.setattr(dependencies, "create_session_factory", lambda engine: "factory")
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))
    dependencies.init_app_state(request)
    assert request.app.state.engine == "engine"
    assert request.app.state.session_factory == "factory"


class FakeConnection:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail

    def __enter__(self) -> FakeConnection:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def execute(self, statement: object) -> None:
        if self.fail:
            raise RuntimeError("db down")


class FakeEngine:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail

    def connect(self) -> FakeConnection:
        return FakeConnection(fail=self.fail)


def test_ready_success() -> None:
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(engine=FakeEngine())))
    assert ready(request) == {"status": "ready"}


def test_ready_failure() -> None:
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(engine=FakeEngine(fail=True)))
    )
    with pytest.raises(AppError) as exc_info:
        ready(request)
    assert exc_info.value.error.status_code == 503


def test_json_formatter_formats_extra_and_exception() -> None:
    formatter = JsonFormatter()
    record = __import__("logging").LogRecord(
        "test",
        20,
        __file__,
        1,
        "hello Authorization: Bearer ghp_secret",
        (),
        None,
    )
    record.request_id = "request-1"
    rendered = formatter.format(record)
    assert "request-1" in rendered
    assert "ghp_secret" not in rendered


def test_configure_logging() -> None:
    configure_logging("INFO")


def test_session_scope_commit_and_rollback() -> None:
    engine = create_db_engine(Settings(DATABASE_URL="sqlite+pysqlite:///:memory:"))
    factory = create_session_factory(engine)
    with session_scope(factory) as session:
        assert session.execute(text("SELECT 1")).scalar_one() == 1
    with pytest.raises(RuntimeError), session_scope(factory):
        raise RuntimeError("boom")
    engine.dispose()


def test_worker_parser_version() -> None:
    parser = build_parser()
    assert isinstance(parser, argparse.ArgumentParser)
    with pytest.raises(SystemExit):
        parser.parse_args(["--version"])


def test_worker_stop() -> None:
    worker = Worker()
    worker.stop()


class FakeSessionForWorker:
    def __init__(self) -> None:
        self.closed = False
        self.rollback_count = 0

    def close(self) -> None:
        self.closed = True

    def rollback(self) -> None:
        self.rollback_count += 1


class FakeWorkerStore:
    jobs: ClassVar[list[object | None]] = []
    failed: ClassVar[list[tuple[uuid.UUID, str]]] = []
    rollback_count = 0
    fail_raises = False

    def __init__(self, session: FakeSessionForWorker) -> None:
        self.session = session

    def rollback(self) -> None:
        type(self).rollback_count += 1
        self.session.rollback()

    def claim_available_job(self, *, worker_id: str, now: datetime) -> object | None:
        return self.jobs.pop(0) if self.jobs else None

    def fail_job(self, job_id: uuid.UUID, message: str) -> None:
        if self.fail_raises:
            raise RuntimeError("database down password=secret")
        self.failed.append((job_id, message))


class ExplodingProcessor:
    def __init__(self, **kwargs: object) -> None:
        return None

    def process(self, job: object) -> None:
        raise RuntimeError("raw secret ghp_should_not_be_saved")


class CompletingProcessor:
    processed: ClassVar[list[uuid.UUID]] = []

    def __init__(self, **kwargs: object) -> None:
        return None

    def process(self, job: object) -> None:
        self.processed.append(job.id)


class IntegrityExplodingProcessor:
    def __init__(self, **kwargs: object) -> None:
        return None

    def process(self, job: object) -> None:
        raise IntegrityError("insert issues", {"token": "ghp_should_not_be_saved"}, RuntimeError())


class CompleteJobExplodingProcessor:
    def __init__(self, **kwargs: object) -> None:
        return None

    def process(self, job: object) -> None:
        raise RuntimeError("complete_job failed ghp_should_not_be_saved")


def fake_job() -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        repository_id=uuid.uuid4(),
        resource_type="issues",
        attempt_count=1,
        locked_at=datetime(2026, 1, 1, tzinfo=UTC),
        locked_by="worker-test",
        heartbeat_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def worker_settings() -> Settings:
    return Settings(WORKER_ID="worker-test", WORKER_POLL_INTERVAL_SECONDS=0.25)


def test_worker_run_once_marks_unexpected_error_failed_and_continues(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_job = fake_job()
    second_job = fake_job()
    sessions = [FakeSessionForWorker(), FakeSessionForWorker()]
    sleeps: list[float] = []
    FakeWorkerStore.jobs = [first_job, second_job]
    FakeWorkerStore.failed = []
    FakeWorkerStore.rollback_count = 0
    FakeWorkerStore.fail_raises = False
    CompletingProcessor.processed = []
    processor_classes = [ExplodingProcessor, CompletingProcessor]

    monkeypatch.setattr("github_data_sync_service.worker.main.SyncJobStore", FakeWorkerStore)
    monkeypatch.setattr(
        "github_data_sync_service.worker.main.recover_stale_jobs",
        lambda **kwargs: 0,
    )
    monkeypatch.setattr(
        "github_data_sync_service.worker.main.IssueSyncProcessor",
        lambda **kwargs: processor_classes.pop(0)(**kwargs),
    )

    worker = Worker(sleep=sleeps.append)
    settings = worker_settings()
    now = datetime(2026, 1, 1, tzinfo=UTC)
    worker.run_once(
        session_factory=lambda: sessions.pop(0),  # type: ignore[arg-type]
        github_client=object(),  # type: ignore[arg-type]
        settings=settings,
        now=lambda: now,
    )
    worker.run_once(
        session_factory=lambda: sessions.pop(0),  # type: ignore[arg-type]
        github_client=object(),  # type: ignore[arg-type]
        settings=settings,
        now=lambda: now,
    )

    assert FakeWorkerStore.failed == [(first_job.id, "internal_error: RuntimeError")]
    assert "ghp_should_not_be_saved" not in FakeWorkerStore.failed[0][1]
    assert FakeWorkerStore.rollback_count == 1
    assert CompletingProcessor.processed == [second_job.id]
    assert sleeps == []


@pytest.mark.parametrize(
    ("processor_class", "expected_error"),
    [
        (IntegrityExplodingProcessor, "internal_error: IntegrityError"),
        (CompleteJobExplodingProcessor, "internal_error: RuntimeError"),
    ],
)
def test_worker_run_once_marks_database_or_finalization_error_failed(
    monkeypatch: pytest.MonkeyPatch,
    processor_class: type[object],
    expected_error: str,
) -> None:
    sync_job = fake_job()
    session = FakeSessionForWorker()
    sleeps: list[float] = []
    FakeWorkerStore.jobs = [sync_job]
    FakeWorkerStore.failed = []
    FakeWorkerStore.rollback_count = 0
    FakeWorkerStore.fail_raises = False

    monkeypatch.setattr("github_data_sync_service.worker.main.SyncJobStore", FakeWorkerStore)
    monkeypatch.setattr(
        "github_data_sync_service.worker.main.recover_stale_jobs",
        lambda **kwargs: 0,
    )
    monkeypatch.setattr(
        "github_data_sync_service.worker.main.IssueSyncProcessor",
        processor_class,
    )

    Worker(sleep=sleeps.append).run_once(
        session_factory=lambda: session,  # type: ignore[arg-type]
        github_client=object(),  # type: ignore[arg-type]
        settings=worker_settings(),
        now=lambda: datetime(2026, 1, 1, tzinfo=UTC),
    )

    assert FakeWorkerStore.failed == [(sync_job.id, expected_error)]
    assert "ghp_should_not_be_saved" not in FakeWorkerStore.failed[0][1]
    assert FakeWorkerStore.rollback_count == 1
    assert session.rollback_count == 1
    assert session.closed is True
    assert sleeps == []


def test_worker_run_once_backs_off_when_fail_job_also_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sync_job = fake_job()
    session = FakeSessionForWorker()
    sleeps: list[float] = []
    FakeWorkerStore.jobs = [sync_job]
    FakeWorkerStore.failed = []
    FakeWorkerStore.rollback_count = 0
    FakeWorkerStore.fail_raises = True

    monkeypatch.setattr("github_data_sync_service.worker.main.SyncJobStore", FakeWorkerStore)
    monkeypatch.setattr(
        "github_data_sync_service.worker.main.recover_stale_jobs",
        lambda **kwargs: 0,
    )
    monkeypatch.setattr(
        "github_data_sync_service.worker.main.IssueSyncProcessor",
        ExplodingProcessor,
    )

    worker = Worker(sleep=sleeps.append)
    worker.run_once(
        session_factory=lambda: session,  # type: ignore[arg-type]
        github_client=object(),  # type: ignore[arg-type]
        settings=worker_settings(),
        now=lambda: datetime(2026, 1, 1, tzinfo=UTC),
    )

    assert FakeWorkerStore.failed == []
    assert FakeWorkerStore.rollback_count == 2
    assert session.closed is True
    assert sleeps == [0.25]
