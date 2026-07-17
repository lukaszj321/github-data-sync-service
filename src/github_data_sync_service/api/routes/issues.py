from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends

from github_data_sync_service.api.dependencies import get_issue_service
from github_data_sync_service.api.schemas.issues import IssueListResponse, IssueResponse
from github_data_sync_service.issues.service import IssueService

router = APIRouter(prefix="/repositories/{repository_id}/issues", tags=["issues"])
IssueServiceDep = Annotated[IssueService, Depends(get_issue_service)]


@router.get("", response_model=IssueListResponse)
def list_repository_issues(
    repository_id: uuid.UUID,
    service: IssueServiceDep,
    limit: int = 50,
    offset: int = 0,
    state: str | None = None,
) -> IssueListResponse:
    bounded_limit = min(max(limit, 1), 100)
    bounded_offset = max(offset, 0)
    items = [
        IssueResponse.model_validate(item)
        for item in service.list(
            repository_id=repository_id,
            limit=bounded_limit,
            offset=bounded_offset,
            state=state,
        )
    ]
    return IssueListResponse(items=items, limit=bounded_limit, offset=bounded_offset)
