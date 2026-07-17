from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from github_data_sync_service.core.errors import AppError
from github_data_sync_service.db.models.resource_sync_state import ResourceSyncState
from github_data_sync_service.db.models.sync_job import SyncJob
from github_data_sync_service.queue.repository import CreateSyncJobResult, SyncJobStore
from github_data_sync_service.repositories.repository import RepositoryStore


@dataclass(frozen=True, slots=True)
class CreateJobResult:
    job: SyncJob
    created: bool


@dataclass(frozen=True, slots=True)
class SyncStateView:
    repository_id: uuid.UUID
    resource_type: str
    initialized: bool
    cursor_at: datetime | None
    last_successful_job_id: uuid.UUID | None
    last_sync_mode: str | None
    last_started_at: datetime | None
    last_completed_at: datetime | None


class SyncJobService:
    def __init__(
        self,
        job_store: SyncJobStore,
        repository_store: RepositoryStore,
        *,
        overlap_seconds: int = 60,
    ) -> None:
        self._job_store = job_store
        self._repository_store = repository_store
        self._overlap_seconds = overlap_seconds

    def create_repository_sync(
        self,
        *,
        repository_id: uuid.UUID,
        resource_type: str,
        mode: str = "incremental",
    ) -> CreateJobResult:
        if resource_type != "issues":
            raise AppError(
                "unsupported_resource_type",
                "Only issues synchronization is supported in this milestone.",
                422,
            )
        if mode not in {"full", "incremental"}:
            raise AppError("invalid_sync_mode", "mode must be full or incremental.", 422)
        if self._repository_store.get(repository_id) is None:
            raise AppError("repository_not_found", "The local repository was not found.", 404)
        result: CreateSyncJobResult = self._job_store.create_or_get_active_job(
            repository_id=repository_id,
            resource_type=resource_type,
            requested_mode=mode,
            overlap_seconds=self._overlap_seconds,
        )
        return CreateJobResult(job=result.job, created=result.created)

    def list(
        self,
        *,
        limit: int,
        offset: int,
        repository_id: uuid.UUID | None,
        status: str | None,
        resource_type: str | None,
        mode: str | None = None,
    ) -> list[SyncJob]:
        return self._job_store.list(
            limit=limit,
            offset=offset,
            repository_id=repository_id,
            status=status,
            resource_type=resource_type,
            mode=mode,
        )

    def get(self, job_id: uuid.UUID) -> SyncJob:
        job = self._job_store.get(job_id)
        if job is None:
            raise AppError("sync_job_not_found", "The synchronization job was not found.", 404)
        return job

    def get_repository_sync_state(self, repository_id: uuid.UUID) -> SyncStateView:
        if self._repository_store.get(repository_id) is None:
            raise AppError("repository_not_found", "The local repository was not found.", 404)
        state: ResourceSyncState | None = self._job_store.get_resource_state(
            repository_id=repository_id,
            resource_type="issues",
        )
        if state is None or state.cursor_at is None:
            return SyncStateView(
                repository_id=repository_id,
                resource_type="issues",
                initialized=False,
                cursor_at=None,
                last_successful_job_id=None,
                last_sync_mode=None,
                last_started_at=None,
                last_completed_at=None,
            )
        return SyncStateView(
            repository_id=repository_id,
            resource_type=state.resource_type,
            initialized=True,
            cursor_at=state.cursor_at,
            last_successful_job_id=state.last_successful_job_id,
            last_sync_mode=state.last_sync_mode,
            last_started_at=state.last_started_at,
            last_completed_at=state.last_completed_at,
        )
