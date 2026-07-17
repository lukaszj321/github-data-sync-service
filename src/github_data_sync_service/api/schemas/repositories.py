from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

REPO_PART_PATTERN = r"^[A-Za-z0-9_.-]+$"


class RepositoryCreateRequest(BaseModel):
    owner: str = Field(min_length=1, max_length=255, pattern=REPO_PART_PATTERN)
    name: str = Field(min_length=1, max_length=255, pattern=REPO_PART_PATTERN)


class RepositoryResponse(BaseModel):
    id: uuid.UUID
    github_id: int
    owner: str
    name: str
    full_name: str
    html_url: str
    description: str | None
    default_branch: str
    is_fork: bool
    is_archived: bool
    github_created_at: datetime
    github_updated_at: datetime
    last_validated_at: datetime
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class RepositoryListResponse(BaseModel):
    items: list[RepositoryResponse]
    limit: int
    offset: int


class RepositorySyncStateResponse(BaseModel):
    repository_id: uuid.UUID
    resource_type: str
    initialized: bool
    cursor_at: datetime | None
    last_successful_job_id: uuid.UUID | None
    last_sync_mode: str | None
    last_started_at: datetime | None
    last_completed_at: datetime | None

    model_config = ConfigDict(from_attributes=True)
