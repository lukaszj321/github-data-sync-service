from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class GitHubRateLimit:
    limit: int | None
    remaining: int | None
    reset: int | None
    retry_after_seconds: int | None


@dataclass(frozen=True, slots=True)
class GitHubRepository:
    github_id: int
    owner: str
    name: str
    full_name: str
    html_url: str
    description: str | None
    default_branch: str
    is_fork: bool
    is_archived: bool
    is_private: bool
    github_created_at: datetime
    github_updated_at: datetime
    github_request_id: str | None
    rate_limit: GitHubRateLimit


@dataclass(frozen=True, slots=True)
class GitHubIssue:
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


@dataclass(frozen=True, slots=True)
class GitHubIssuePage:
    issues: tuple[GitHubIssue, ...]
    fetched_count: int
    skipped_pull_request_count: int
    next_url: str | None
    github_request_id: str | None
    rate_limit: GitHubRateLimit
