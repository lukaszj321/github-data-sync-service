from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Response, status

from github_data_sync_service.api.dependencies import get_repository_service
from github_data_sync_service.api.schemas.repositories import (
    RepositoryCreateRequest,
    RepositoryListResponse,
    RepositoryResponse,
)
from github_data_sync_service.repositories.service import RepositoryService

router = APIRouter(prefix="/repositories", tags=["repositories"])
RepositoryServiceDep = Annotated[RepositoryService, Depends(get_repository_service)]


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
