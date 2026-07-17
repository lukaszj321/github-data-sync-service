from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends

from github_data_sync_service.api.dependencies import get_sync_job_service
from github_data_sync_service.api.schemas.sync_jobs import SyncJobListResponse, SyncJobResponse
from github_data_sync_service.queue.service import SyncJobService

router = APIRouter(prefix="/sync-jobs", tags=["sync-jobs"])
SyncJobServiceDep = Annotated[SyncJobService, Depends(get_sync_job_service)]


@router.get("", response_model=SyncJobListResponse)
def list_sync_jobs(
    service: SyncJobServiceDep,
    limit: int = 50,
    offset: int = 0,
    repository_id: uuid.UUID | None = None,
    status: str | None = None,
    resource_type: str | None = None,
) -> SyncJobListResponse:
    bounded_limit = min(max(limit, 1), 100)
    bounded_offset = max(offset, 0)
    items = [
        SyncJobResponse.model_validate(item)
        for item in service.list(
            limit=bounded_limit,
            offset=bounded_offset,
            repository_id=repository_id,
            status=status,
            resource_type=resource_type,
        )
    ]
    return SyncJobListResponse(items=items, limit=bounded_limit, offset=bounded_offset)


@router.get("/{job_id}", response_model=SyncJobResponse)
def get_sync_job(job_id: uuid.UUID, service: SyncJobServiceDep) -> object:
    return service.get(job_id)
