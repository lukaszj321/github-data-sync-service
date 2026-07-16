from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from github_data_sync_service.db.models.sync_job import SyncJob, SyncJobStatus


class SyncJobStore:
    def __init__(self, session: Session) -> None:
        self._session = session

    def claim_available_job(self, *, worker_id: str) -> SyncJob | None:
        now = datetime.now(UTC)
        with self._session.begin():
            job = self._session.scalars(
                select(SyncJob)
                .where(
                    SyncJob.status == SyncJobStatus.pending.value,
                    SyncJob.available_at <= now,
                )
                .order_by(SyncJob.available_at, SyncJob.created_at)
                .with_for_update(skip_locked=True)
                .limit(1)
            ).first()
            if job is None:
                return None
            job.status = SyncJobStatus.running.value
            job.locked_by = worker_id
            job.locked_at = now
            job.heartbeat_at = now
            job.started_at = now
            job.attempt_count += 1
        return job

    def fail_job(self, job_id: uuid.UUID, message: str) -> None:
        now = datetime.now(UTC)
        with self._session.begin():
            job = self._session.get(SyncJob, job_id)
            if job is None:
                return
            job.status = SyncJobStatus.failed.value
            job.finished_at = now
            job.last_error = message
            job.error_count += 1
