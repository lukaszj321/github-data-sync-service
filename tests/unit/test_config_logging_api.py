from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest

from github_data_sync_service.api.app import create_app
from github_data_sync_service.api.dependencies import (
    get_issue_service,
    get_repository_service,
    get_sync_job_service,
)
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


def fake_job(repository_id: uuid.UUID) -> SimpleNamespace:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    return SimpleNamespace(
        id=uuid.uuid4(),
        repository_id=repository_id,
        resource_type="issues",
        sync_mode="full",
        cursor_before=None,
        since_at=None,
        cursor_after=None,
        sync_window_started_at=None,
        status="pending",
        attempt_count=0,
        available_at=now,
        locked_at=None,
        locked_by=None,
        heartbeat_at=None,
        started_at=None,
        finished_at=None,
        current_page=0,
        fetched_count=0,
        skipped_count=0,
        created_count=0,
        updated_count=0,
        unchanged_count=0,
        error_count=0,
        last_error=None,
        github_request_id=None,
        rate_limit_remaining=None,
        created_at=now,
        updated_at=now,
    )


def fake_issue(repository_id: uuid.UUID) -> SimpleNamespace:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    return SimpleNamespace(
        id=uuid.uuid4(),
        repository_id=repository_id,
        github_id=1,
        number=1,
        title="Issue 1",
        body=None,
        state="open",
        state_reason=None,
        html_url="https://github.com/fastapi/fastapi/issues/1",
        author_login=None,
        comments_count=0,
        is_locked=False,
        github_created_at=now,
        github_updated_at=now,
        github_closed_at=None,
        last_synced_at=now,
        created_at=now,
        updated_at=now,
    )


class FakeSyncJobService:
    def __init__(self, repository_id: uuid.UUID, *, created: bool = True) -> None:
        self.job = fake_job(repository_id)
        self.created = created

    def create_repository_sync(
        self, *, repository_id: uuid.UUID, resource_type: str, mode: str = "incremental"
    ) -> object:
        return SimpleNamespace(job=self.job, created=self.created)

    def list(
        self,
        *,
        limit: int,
        offset: int,
        repository_id: uuid.UUID | None,
        status: str | None,
        resource_type: str | None,
        mode: str | None = None,
    ) -> list[object]:
        return [self.job]

    def get(self, job_id: uuid.UUID) -> object:
        return self.job

    def get_repository_sync_state(self, repository_id: uuid.UUID) -> object:
        return SimpleNamespace(
            repository_id=repository_id,
            resource_type="issues",
            initialized=False,
            cursor_at=None,
            last_successful_job_id=None,
            last_sync_mode=None,
            last_started_at=None,
            last_completed_at=None,
        )


class FakeIssueService:
    def __init__(self, repository_id: uuid.UUID) -> None:
        self.issue = fake_issue(repository_id)

    def list(
        self,
        *,
        repository_id: uuid.UUID,
        limit: int,
        offset: int,
        state: str | None,
    ) -> list[object]:
        return [self.issue]


def make_app(service: FakeService):
    app = create_app()
    app.dependency_overrides[get_repository_service] = lambda: service
    return app


def make_app_with_sync(
    service: FakeService,
    sync_service: FakeSyncJobService,
    issue_service: FakeIssueService,
):
    app = make_app(service)
    app.dependency_overrides[get_sync_job_service] = lambda: sync_service
    app.dependency_overrides[get_issue_service] = lambda: issue_service
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


@pytest.mark.anyio
async def test_create_repository_sync_job_sets_location() -> None:
    repository_service = FakeService()
    sync_service = FakeSyncJobService(repository_service.repository.id, created=True)
    app = make_app_with_sync(
        repository_service,
        sync_service,
        FakeIssueService(repository_service.repository.id),
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            f"/repositories/{repository_service.repository.id}/sync",
            json={"resource_type": "issues"},
        )
    assert response.status_code == 202
    assert response.headers["Location"] == f"/sync-jobs/{sync_service.job.id}"
    assert response.json()["sync_mode"] == "full"


@pytest.mark.anyio
async def test_create_repository_sync_job_accepts_explicit_mode() -> None:
    repository_service = FakeService()
    sync_service = FakeSyncJobService(repository_service.repository.id, created=True)
    app = make_app_with_sync(
        repository_service,
        sync_service,
        FakeIssueService(repository_service.repository.id),
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            f"/repositories/{repository_service.repository.id}/sync",
            json={"resource_type": "issues", "mode": "full"},
        )
    assert response.status_code == 202


@pytest.mark.anyio
async def test_create_repository_sync_job_rejects_invalid_mode() -> None:
    repository_service = FakeService()
    app = make_app_with_sync(
        repository_service,
        FakeSyncJobService(repository_service.repository.id),
        FakeIssueService(repository_service.repository.id),
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            f"/repositories/{repository_service.repository.id}/sync",
            json={"resource_type": "issues", "mode": "bad"},
        )
    assert response.status_code == 422


@pytest.mark.anyio
async def test_create_repository_sync_job_existing_returns_200() -> None:
    repository_service = FakeService()
    app = make_app_with_sync(
        repository_service,
        FakeSyncJobService(repository_service.repository.id, created=False),
        FakeIssueService(repository_service.repository.id),
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            f"/repositories/{repository_service.repository.id}/sync",
            json={"resource_type": "issues"},
        )
    assert response.status_code == 200


@pytest.mark.anyio
async def test_get_repository_sync_state_uninitialized() -> None:
    repository_service = FakeService()
    app = make_app_with_sync(
        repository_service,
        FakeSyncJobService(repository_service.repository.id),
        FakeIssueService(repository_service.repository.id),
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(f"/repositories/{repository_service.repository.id}/sync-state")
    assert response.status_code == 200
    assert response.json()["initialized"] is False
    assert response.json()["cursor_at"] is None


@pytest.mark.anyio
async def test_list_sync_jobs_and_issues_bound_limits() -> None:
    repository_service = FakeService()
    app = make_app_with_sync(
        repository_service,
        FakeSyncJobService(repository_service.repository.id),
        FakeIssueService(repository_service.repository.id),
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        jobs_response = await client.get("/sync-jobs?limit=500&offset=-1")
        issues_response = await client.get(
            f"/repositories/{repository_service.repository.id}/issues?limit=500&offset=-1"
        )
    assert jobs_response.status_code == 200
    assert jobs_response.json()["limit"] == 100
    assert issues_response.status_code == 200
    assert issues_response.json()["items"][0]["number"] == 1


def test_settings_can_read_environment(monkeypatch: object) -> None:
    monkeypatch.setenv("WORKER_ID", "worker-test")  # type: ignore[attr-defined]
    assert Settings().worker_id == "worker-test"


def test_default_user_agent_tracks_package_version() -> None:
    settings = Settings()
    assert settings.github_user_agent == "github-data-sync-service/0.3.0"


def test_user_agent_override_still_works() -> None:
    settings = Settings(GITHUB_USER_AGENT="custom-github-client")
    assert settings.github_user_agent == "custom-github-client"
