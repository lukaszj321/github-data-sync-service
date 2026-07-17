from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import Select, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from github_data_sync_service.db.models.resource_sync_state import ResourceSyncState
from github_data_sync_service.db.models.sync_job import SyncJob, SyncJobStatus
from github_data_sync_service.github.errors import GitHubRateLimitError
from github_data_sync_service.github.models import GitHubIssuePage
from github_data_sync_service.issues.repository import IssuesStore, IssueUpsertCounts

ACTIVE_STATUSES = (
    SyncJobStatus.pending.value,
    SyncJobStatus.running.value,
    SyncJobStatus.rate_limited.value,
)
MAX_ERROR_LENGTH = 2000


@dataclass(frozen=True, slots=True)
class CreateSyncJobResult:
    job: SyncJob
    created: bool


class SyncJobStore:
    def __init__(self, session: Session) -> None:
        self._session = session

    def rollback(self) -> None:
        self._session.rollback()

    def create_or_get_active_job(
        self,
        *,
        repository_id: uuid.UUID,
        resource_type: str,
        requested_mode: str = "incremental",
        overlap_seconds: int = 60,
        now: datetime | None = None,
    ) -> CreateSyncJobResult:
        current_time = now or datetime.now(UTC)
        state = self.get_resource_state(repository_id=repository_id, resource_type=resource_type)
        cursor_before = state.cursor_at if state is not None else None
        sync_mode = (
            "incremental"
            if requested_mode == "incremental" and cursor_before is not None
            else "full"
        )
        since_at = (
            cursor_before - timedelta(seconds=overlap_seconds)
            if sync_mode == "incremental" and cursor_before is not None
            else None
        )
        job = SyncJob(
            repository_id=repository_id,
            resource_type=resource_type,
            sync_mode=sync_mode,
            cursor_before=cursor_before,
            since_at=since_at,
            cursor_after=None,
            sync_window_started_at=None,
            status=SyncJobStatus.pending.value,
            attempt_count=0,
            available_at=current_time,
            current_page=0,
            fetched_count=0,
            skipped_count=0,
            created_count=0,
            updated_count=0,
            unchanged_count=0,
            error_count=0,
        )
        self._session.add(job)
        try:
            self._session.commit()
        except IntegrityError:
            self._session.rollback()
            active_job = self.get_active_job(
                repository_id=repository_id,
                resource_type=resource_type,
            )
            if active_job is None:
                raise
            return CreateSyncJobResult(job=active_job, created=False)
        self._session.refresh(job)
        return CreateSyncJobResult(job=job, created=True)

    def get_resource_state(
        self, *, repository_id: uuid.UUID, resource_type: str
    ) -> ResourceSyncState | None:
        return self._session.scalars(
            select(ResourceSyncState)
            .where(
                ResourceSyncState.repository_id == repository_id,
                ResourceSyncState.resource_type == resource_type,
            )
            .limit(1)
        ).first()

    def get_active_job(self, *, repository_id: uuid.UUID, resource_type: str) -> SyncJob | None:
        return self._session.scalars(
            select(SyncJob)
            .where(
                SyncJob.repository_id == repository_id,
                SyncJob.resource_type == resource_type,
                SyncJob.status.in_(ACTIVE_STATUSES),
            )
            .order_by(SyncJob.created_at.desc(), SyncJob.id.desc())
            .limit(1)
        ).first()

    def list(
        self,
        *,
        limit: int,
        offset: int,
        repository_id: uuid.UUID | None = None,
        status: str | None = None,
        resource_type: str | None = None,
        mode: str | None = None,
    ) -> list[SyncJob]:
        stmt: Select[tuple[SyncJob]] = (
            select(SyncJob)
            .order_by(SyncJob.created_at.desc(), SyncJob.id.desc())
            .limit(limit)
            .offset(offset)
        )
        if repository_id is not None:
            stmt = stmt.where(SyncJob.repository_id == repository_id)
        if status is not None:
            stmt = stmt.where(SyncJob.status == status)
        if resource_type is not None:
            stmt = stmt.where(SyncJob.resource_type == resource_type)
        if mode is not None:
            stmt = stmt.where(SyncJob.sync_mode == mode)
        return list(self._session.scalars(stmt))

    def get(self, job_id: uuid.UUID) -> SyncJob | None:
        return self._session.get(SyncJob, job_id)

    def claim_available_job(
        self,
        *,
        worker_id: str,
        now: datetime | None = None,
    ) -> SyncJob | None:
        current_time = now or datetime.now(UTC)
        job = self._session.scalars(
            select(SyncJob)
            .options(selectinload(SyncJob.repository))
            .where(
                SyncJob.status.in_((SyncJobStatus.pending.value, SyncJobStatus.rate_limited.value)),
                SyncJob.available_at <= current_time,
            )
            .order_by(SyncJob.available_at, SyncJob.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        ).first()
        if job is None:
            self._session.rollback()
            return None
        job.status = SyncJobStatus.running.value
        job.locked_by = worker_id
        job.locked_at = current_time
        job.heartbeat_at = current_time
        job.started_at = current_time
        job.finished_at = None
        if job.sync_window_started_at is None:
            job.sync_window_started_at = current_time
        job.cursor_after = None
        job.attempt_count += 1
        job.current_page = 0
        job.fetched_count = 0
        job.skipped_count = 0
        job.created_count = 0
        job.updated_count = 0
        job.unchanged_count = 0
        self._session.commit()
        return job

    def record_issue_page(
        self,
        *,
        job_id: uuid.UUID,
        repository_id: uuid.UUID,
        page_number: int,
        page: GitHubIssuePage,
        now: datetime | None = None,
    ) -> IssueUpsertCounts:
        try:
            current_time = now or datetime.now(UTC)
            counts = IssuesStore(self._session).upsert_page(
                repository_id=repository_id,
                issues=page.issues,
                synced_at=current_time,
            )
            job = self._session.get(SyncJob, job_id)
            if job is None:
                raise ValueError("Sync job no longer exists")
            job.current_page = page_number
            job.fetched_count += page.fetched_count
            job.skipped_count += page.skipped_pull_request_count
            job.created_count += counts.created
            job.updated_count += counts.updated
            job.unchanged_count += counts.unchanged
            job.github_request_id = page.github_request_id
            job.rate_limit_remaining = page.rate_limit.remaining
            job.heartbeat_at = current_time
            self._session.commit()
            return counts
        except Exception:
            self._session.rollback()
            raise

    def complete_job(self, job_id: uuid.UUID, *, now: datetime | None = None) -> None:
        current_time = now or datetime.now(UTC)
        try:
            job = self._session.scalars(
                select(SyncJob).where(SyncJob.id == job_id).with_for_update()
            ).first()
            if job is None:
                self._session.rollback()
                return
            if job.status != SyncJobStatus.running.value:
                self._session.rollback()
                return
            if job.sync_window_started_at is None:
                raise ValueError("Sync job is missing sync_window_started_at")
            state = self._session.scalars(
                select(ResourceSyncState)
                .where(
                    ResourceSyncState.repository_id == job.repository_id,
                    ResourceSyncState.resource_type == job.resource_type,
                )
                .with_for_update()
            ).first()
            if state is None:
                state = ResourceSyncState(
                    repository_id=job.repository_id,
                    resource_type=job.resource_type,
                )
                self._session.add(state)
            cursor_candidate = job.sync_window_started_at
            if state.cursor_at is None or cursor_candidate >= state.cursor_at:
                state.cursor_at = cursor_candidate
                state.last_successful_job_id = job.id
                state.last_sync_mode = job.sync_mode
                state.last_started_at = job.sync_window_started_at
                state.last_completed_at = current_time
            # If an older recovered job completes after a newer job, keep the newer
            # high-watermark so the durable cursor never moves backwards.
            job.cursor_after = cursor_candidate
            job.status = SyncJobStatus.completed.value
            job.finished_at = current_time
            job.last_error = None
            job.locked_by = None
            job.locked_at = None
            job.heartbeat_at = None
            self._session.commit()
        except Exception:
            self._session.rollback()
            raise

    def fail_job(self, job_id: uuid.UUID, message: str) -> None:
        current_time = datetime.now(UTC)
        job = self._session.get(SyncJob, job_id)
        if job is None:
            self._session.rollback()
            return
        job.status = SyncJobStatus.failed.value
        job.finished_at = current_time
        job.cursor_after = None
        job.last_error = _safe_error(message)
        job.error_count += 1
        job.locked_by = None
        job.locked_at = None
        job.heartbeat_at = None
        self._session.commit()

    def rate_limit_job(
        self,
        job_id: uuid.UUID,
        error: GitHubRateLimitError,
        *,
        available_at: datetime,
    ) -> None:
        job = self._session.get(SyncJob, job_id)
        if job is None:
            self._session.rollback()
            return
        job.status = SyncJobStatus.rate_limited.value
        job.available_at = available_at
        job.cursor_after = None
        job.last_error = _safe_error(str(error))
        job.error_count += 1
        job.github_request_id = error.details.github_request_id
        job.rate_limit_remaining = error.details.rate_limit_remaining
        job.locked_by = None
        job.locked_at = None
        job.heartbeat_at = None
        job.finished_at = None
        self._session.commit()

    def recover_stale_running_jobs(
        self,
        *,
        now: datetime,
        timeout_seconds: int,
    ) -> int:
        threshold = now - timedelta(seconds=timeout_seconds)
        jobs = list(
            self._session.scalars(
                select(SyncJob)
                .where(
                    SyncJob.status == SyncJobStatus.running.value,
                    SyncJob.heartbeat_at < threshold,
                )
                .with_for_update(skip_locked=True)
            )
        )
        for job in jobs:
            job.status = SyncJobStatus.pending.value
            job.available_at = now
            job.locked_by = None
            job.locked_at = None
            job.heartbeat_at = None
            job.last_error = "Recovered stale worker lock."
            job.error_count += 1
        self._session.commit()
        return len(jobs)


def _safe_error(message: str) -> str:
    return message.replace("Authorization", "[redacted]")[:MAX_ERROR_LENGTH]
