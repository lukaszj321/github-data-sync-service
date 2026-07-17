from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class SyncJobCreateRequest(BaseModel):
    resource_type: Literal["issues"]


class SyncJobResponse(BaseModel):
    id: uuid.UUID
    repository_id: uuid.UUID
    resource_type: str
    status: str
    attempt_count: int
    available_at: datetime
    locked_at: datetime | None
    locked_by: str | None
    heartbeat_at: datetime | None
    started_at: datetime | None
    finished_at: datetime | None
    current_page: int
    fetched_count: int
    skipped_count: int
    created_count: int
    updated_count: int
    unchanged_count: int
    error_count: int
    last_error: str | None
    github_request_id: str | None
    rate_limit_remaining: int | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class SyncJobListResponse(BaseModel):
    items: list[SyncJobResponse]
    limit: int
    offset: int
