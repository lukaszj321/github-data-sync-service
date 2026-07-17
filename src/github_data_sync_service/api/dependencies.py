from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from github_data_sync_service.core.config import Settings, get_settings
from github_data_sync_service.db.session import create_db_engine, create_session_factory
from github_data_sync_service.github.client import GitHubClient
from github_data_sync_service.issues.repository import IssuesStore
from github_data_sync_service.issues.service import IssueService
from github_data_sync_service.queue.repository import SyncJobStore
from github_data_sync_service.queue.service import SyncJobService
from github_data_sync_service.repositories.repository import RepositoryStore
from github_data_sync_service.repositories.service import RepositoryService

SettingsDep = Annotated[Settings, Depends(get_settings)]


def get_db_session(request: Request) -> Iterator[Session]:
    factory = request.app.state.session_factory
    session = factory()
    try:
        yield session
    finally:
        session.close()


SessionDep = Annotated[Session, Depends(get_db_session)]


def get_github_client(settings: SettingsDep) -> Iterator[GitHubClient]:
    client = GitHubClient(
        base_url=settings.github_api_base_url,
        user_agent=settings.github_user_agent,
        api_version=settings.github_api_version,
        token=settings.github_token_value,
        connect_timeout_seconds=settings.github_connect_timeout_seconds,
        read_timeout_seconds=settings.github_read_timeout_seconds,
        max_attempts=settings.github_max_attempts,
    )
    try:
        yield client
    finally:
        client.close()


GitHubClientDep = Annotated[GitHubClient, Depends(get_github_client)]


def get_repository_service(
    session: SessionDep,
    github_client: GitHubClientDep,
) -> RepositoryService:
    return RepositoryService(RepositoryStore(session), github_client)


def get_sync_job_service(session: SessionDep) -> SyncJobService:
    settings = get_settings()
    return SyncJobService(
        SyncJobStore(session),
        RepositoryStore(session),
        overlap_seconds=settings.issues_sync_overlap_seconds,
    )


def get_issue_service(session: SessionDep) -> IssueService:
    return IssueService(IssuesStore(session), RepositoryStore(session))


def init_app_state(request: Request) -> None:
    settings = get_settings()
    engine = create_db_engine(settings)
    request.app.state.engine = engine
    request.app.state.session_factory = create_session_factory(engine)
