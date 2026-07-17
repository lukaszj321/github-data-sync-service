from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class IssueResponse(BaseModel):
    id: uuid.UUID
    repository_id: uuid.UUID
    github_id: int
    number: int
    title: str
    body: str | None
    state: str
    state_reason: str | None
    html_url: str
    author_login: str | None
    comments_count: int
    is_locked: bool
    github_created_at: datetime
    github_updated_at: datetime
    github_closed_at: datetime | None
    last_synced_at: datetime
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class IssueListResponse(BaseModel):
    items: list[IssueResponse]
    limit: int
    offset: int
