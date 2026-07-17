from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Response, status

from github_data_sync_service.api.dependencies import get_repository_service, get_sync_job_service
from github_data_sync_service.api.schemas.repositories import (
    RepositoryCreateRequest,
    RepositoryListResponse,
    RepositoryResponse,
    RepositorySyncStateResponse,
)
from github_data_sync_service.api.schemas.sync_jobs import SyncJobCreateRequest, SyncJobResponse
from github_data_sync_service.queue.service import SyncJobService
from github_data_sync_service.repositories.service import RepositoryService

router = APIRouter(prefix="/repositories", tags=["repositories"])
RepositoryServiceDep = Annotated[RepositoryService, Depends(get_repository_service)]
SyncJobServiceDep = Annotated[SyncJobService, Depends(get_sync_job_service)]


@router.post("", response_model=RepositoryResponse)
def create_repository(
    payload: RepositoryCreateRequest,
    response: Response,
    service: RepositoryServiceDep,
) -> object:
    result = service.register(payload.owner, payload.name)
    response.status_code = status.HTTP_201_CREATED if result.created else status.HTTP_200_OK
    return result.repository


@router.get("", response_model=RepositoryListResponse)
def list_repositories(
    service: RepositoryServiceDep,
    limit: int = 50,
    offset: int = 0,
) -> RepositoryListResponse:
    bounded_limit = min(max(limit, 1), 100)
    bounded_offset = max(offset, 0)
    items = [
        RepositoryResponse.model_validate(item)
        for item in service.list(limit=bounded_limit, offset=bounded_offset)
    ]
    return RepositoryListResponse(items=items, limit=bounded_limit, offset=bounded_offset)


@router.get("/{repository_id}", response_model=RepositoryResponse)
def get_repository(
    repository_id: uuid.UUID,
    service: RepositoryServiceDep,
) -> object:
    return service.get(repository_id)


@router.post("/{repository_id}/sync", response_model=SyncJobResponse)
def create_repository_sync_job(
    repository_id: uuid.UUID,
    payload: SyncJobCreateRequest,
    response: Response,
    service: SyncJobServiceDep,
) -> object:
    result = service.create_repository_sync(
        repository_id=repository_id,
        resource_type=payload.resource_type,
        mode=payload.mode,
    )
    response.status_code = status.HTTP_202_ACCEPTED if result.created else status.HTTP_200_OK
    response.headers["Location"] = f"/sync-jobs/{result.job.id}"
    return result.job


@router.get("/{repository_id}/sync-state", response_model=RepositorySyncStateResponse)
def get_repository_sync_state(
    repository_id: uuid.UUID,
    service: SyncJobServiceDep,
) -> object:
    return service.get_repository_sync_state(repository_id)
