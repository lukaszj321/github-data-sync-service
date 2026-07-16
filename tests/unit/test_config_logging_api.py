from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest

from github_data_sync_service.api.app import create_app
from github_data_sync_service.api.dependencies import get_repository_service
from github_data_sync_service.api.schemas.repositories import RepositoryCreateRequest
from github_data_sync_service.core.config import Settings
from github_data_sync_service.core.errors import AppError
from github_data_sync_service.core.logging import redact_secrets


def test_empty_github_token_becomes_none() -> None:
    settings = Settings(GITHUB_TOKEN="")
    assert settings.github_token_value is None
    assert "secret" not in repr(settings)


def test_secret_redaction() -> None:
    redacted = redact_secrets(
        "Authorization: Bearer ghp_secret postgresql://user:password@example/db password=secret"
    )
    assert "ghp_secret" not in redacted
    assert "password@example" not in redacted
    assert "password=secret" not in redacted


def test_repository_request_schema() -> None:
    payload = RepositoryCreateRequest(owner="fastapi", name="fastapi")
    assert payload.owner == "fastapi"


def test_repository_request_schema_rejects_invalid_value() -> None:
    try:
        RepositoryCreateRequest(owner="bad owner", name="repo")
    except Exception as exc:
        assert "owner" in str(exc)
    else:
        raise AssertionError("validation should fail")


class FakeService:
    def __init__(self, *, created: bool = True) -> None:
        now = datetime(2026, 1, 1, tzinfo=UTC)
        self.repository = SimpleNamespace(
            id=uuid.uuid4(),
            github_id=1,
            owner="fastapi",
            name="fastapi",
            full_name="fastapi/fastapi",
            html_url="https://github.com/fastapi/fastapi",
            description=None,
            default_branch="master",
            is_fork=False,
            is_archived=False,
            github_created_at=now,
            github_updated_at=now,
            last_validated_at=now,
            created_at=now,
            updated_at=now,
        )
        self.created = created

    def register(self, owner: str, name: str) -> SimpleNamespace:
        return SimpleNamespace(repository=self.repository, created=self.created)

    def list(self, *, limit: int, offset: int) -> list[object]:
        return [self.repository]

    def get(self, repository_id: uuid.UUID) -> object:
        if repository_id != self.repository.id:
            raise AppError("repository_not_found", "The local repository was not found.", 404)
        return self.repository


def make_app(service: FakeService):
    app = create_app()
    app.dependency_overrides[get_repository_service] = lambda: service
    return app


@pytest.mark.anyio
async def test_post_repositories_created() -> None:
    app = make_app(FakeService(created=True))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/repositories", json={"owner": "fastapi", "name": "fastapi"})
    assert response.status_code == 201
    assert response.json()["full_name"] == "fastapi/fastapi"


@pytest.mark.anyio
async def test_post_repositories_existing() -> None:
    app = make_app(FakeService(created=False))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/repositories", json={"owner": "fastapi", "name": "fastapi"})
    assert response.status_code == 200


@pytest.mark.anyio
async def test_list_repositories_bounds_limit() -> None:
    app = make_app(FakeService())
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/repositories?limit=500&offset=-1")
    assert response.status_code == 200
    assert response.json()["limit"] == 100
    assert response.json()["offset"] == 0


@pytest.mark.anyio
async def test_get_repository_not_found_error_shape() -> None:
    service = FakeService()
    app = make_app(service)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(f"/repositories/{uuid.uuid4()}")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "repository_not_found"


@pytest.mark.anyio
async def test_health_does_not_need_database() -> None:
    app = make_app(FakeService())
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")
    assert response.status_code == 200


def test_settings_can_read_environment(monkeypatch: object) -> None:
    monkeypatch.setenv("WORKER_ID", "worker-test")  # type: ignore[attr-defined]
    assert Settings().worker_id == "worker-test"
