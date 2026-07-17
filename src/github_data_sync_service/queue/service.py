from __future__ import annotations

import uuid
from dataclasses import dataclass

from github_data_sync_service.core.errors import AppError
from github_data_sync_service.db.models.sync_job import SyncJob
from github_data_sync_service.queue.repository import CreateSyncJobResult, SyncJobStore
from github_data_sync_service.repositories.repository import RepositoryStore


@dataclass(frozen=True, slots=True)
class CreateJobResult:
    job: SyncJob
    created: bool


class SyncJobService:
    def __init__(self, job_store: SyncJobStore, repository_store: RepositoryStore) -> None:
        self._job_store = job_store
        self._repository_store = repository_store

    def create_repository_sync(
        self,
        *,
        repository_id: uuid.UUID,
        resource_type: str,
    ) -> CreateJobResult:
        if resource_type != "issues":
            raise AppError(
                "unsupported_resource_type",
                "Only issues synchronization is supported in this milestone.",
                422,
            )
        if self._repository_store.get(repository_id) is None:
            raise AppError("repository_not_found", "The local repository was not found.", 404)
        result: CreateSyncJobResult = self._job_store.create_or_get_active_job(
            repository_id=repository_id,
            resource_type=resource_type,
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
    ) -> list[SyncJob]:
        return self._job_store.list(
            limit=limit,
            offset=offset,
            repository_id=repository_id,
            status=status,
            resource_type=resource_type,
        )

    def get(self, job_id: uuid.UUID) -> SyncJob:
        job = self._job_store.get(job_id)
        if job is None:
            raise AppError("sync_job_not_found", "The synchronization job was not found.", 404)
        return job
