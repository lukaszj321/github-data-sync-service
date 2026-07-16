from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from alembic import command


@pytest.fixture
def sample_github_payload() -> dict[str, object]:
    return {
        "id": 160919119,
        "owner": {"login": "fastapi"},
        "name": "fastapi",
        "full_name": "fastapi/fastapi",
        "html_url": "https://github.com/fastapi/fastapi",
        "description": "FastAPI framework",
        "default_branch": "master",
        "fork": False,
        "archived": False,
        "private": False,
        "created_at": "2018-12-08T15:00:42Z",
        "updated_at": "2026-01-01T00:00:00Z",
    }


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(scope="session")
def test_database_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is required for integration tests")
    return url


@pytest.fixture(scope="session")
def migrated_engine(test_database_url: str) -> Iterator[Engine]:
    os.environ["DATABASE_URL"] = test_database_url
    cfg = Config("alembic.ini")
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    engine = create_engine(test_database_url, pool_pre_ping=True)
    try:
        yield engine
    finally:
        engine.dispose()
        command.downgrade(cfg, "base")


@pytest.fixture
def db_session(migrated_engine: Engine) -> Iterator[Session]:
    with migrated_engine.begin() as conn:
        conn.execute(text("TRUNCATE sync_jobs, repositories RESTART IDENTITY CASCADE"))
    factory = sessionmaker(bind=migrated_engine, autoflush=False, expire_on_commit=False)
    session = factory()
    try:
        yield session
    finally:
        session.close()
