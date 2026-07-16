from __future__ import annotations

import argparse
from types import SimpleNamespace

import pytest
from sqlalchemy import text

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
