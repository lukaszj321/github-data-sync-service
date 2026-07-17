from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from typing import Protocol

from github_data_sync_service.core.config import Settings
from github_data_sync_service.db.models.sync_job import SyncJob
from github_data_sync_service.github.errors import GitHubClientError, GitHubRateLimitError
from github_data_sync_service.github.models import GitHubIssuePage
from github_data_sync_service.queue.repository import SyncJobStore

logger = logging.getLogger(__name__)
NowFunc = Callable[[], datetime]


class IssuesPageClient(Protocol):
    def iter_issues_pages(
        self,
        owner: str,
        repo: str,
        *,
        per_page: int,
        max_pages: int,
        since: datetime | None = None,
    ) -> Iterator[GitHubIssuePage]: ...


class IssueSyncProcessor:
    def __init__(
        self,
        *,
        store: SyncJobStore,
        github_client: IssuesPageClient,
        settings: Settings,
        now: NowFunc | None = None,
    ) -> None:
        self._store = store
        self._github_client = github_client
        self._settings = settings
        self._now = now or (lambda: datetime.now(UTC))

    def process(self, job: SyncJob) -> None:
        if job.resource_type != "issues":
            self._store.fail_job(job.id, f"Unsupported resource_type: {job.resource_type}")
            logger.info(
                "Unsupported job failed",
                extra=self._log_context(job, page=job.current_page),
            )
            return

        logger.info("Starting issues sync job", extra=self._log_context(job, page=0))
        pages = self._github_client.iter_issues_pages(
            job.repository.owner,
            job.repository.name,
            per_page=self._settings.github_issues_per_page,
            max_pages=self._settings.github_max_pages_per_sync,
            since=job.since_at if job.sync_mode == "incremental" else None,
        )
        page_number = 0
        while True:
            started = time.perf_counter()
            try:
                page = next(pages)
            except StopIteration:
                self._store.complete_job(job.id, now=self._now())
                logger.info(
                    "Issues sync job completed",
                    extra=self._log_context(job, page=page_number),
                )
                return
            except GitHubRateLimitError as exc:
                available_at = self._rate_limited_until(exc)
                self._store.rate_limit_job(job.id, exc, available_at=available_at)
                logger.info(
                    "Issues sync job rate limited",
                    extra={
                        **self._log_context(job, page=page_number + 1),
                        "github_request_id": exc.details.github_request_id,
                    },
                )
                return
            except GitHubClientError as exc:
                self._store.fail_job(job.id, f"{exc.code}: {exc}")
                logger.info(
                    "Issues sync job failed",
                    extra={
                        **self._log_context(job, page=page_number + 1),
                        "status_code": exc.details.status_code,
                        "github_request_id": exc.details.github_request_id,
                    },
                )
                return

            duration_ms = int((time.perf_counter() - started) * 1000)
            page_number += 1
            counts = self._store.record_issue_page(
                job_id=job.id,
                repository_id=job.repository_id,
                page_number=page_number,
                page=page,
                now=self._now(),
            )
            logger.info(
                "Issues page stored",
                extra={
                    **self._log_context(job, page=page_number),
                    "duration_ms": duration_ms,
                    "github_request_id": page.github_request_id,
                    "created_count": counts.created,
                    "updated_count": counts.updated,
                    "unchanged_count": counts.unchanged,
                },
            )

    def _rate_limited_until(self, exc: GitHubRateLimitError) -> datetime:
        current_time = self._now()
        if exc.details.retry_after_seconds is not None:
            return current_time + timedelta(seconds=exc.details.retry_after_seconds)
        if exc.details.rate_limit_remaining == 0 and exc.details.rate_limit_reset is not None:
            return datetime.fromtimestamp(exc.details.rate_limit_reset, tz=UTC)
        return current_time + timedelta(seconds=self._settings.worker_rate_limit_fallback_seconds)

    def _log_context(self, job: SyncJob, *, page: int) -> dict[str, object]:
        return {
            "job_id": str(job.id),
            "repository_id": str(job.repository_id),
            "resource_type": job.resource_type,
            "sync_mode": job.sync_mode,
            "page": page,
            "attempt": job.attempt_count,
            "worker_id": self._settings.worker_id,
        }


def recover_stale_jobs(
    *,
    store: SyncJobStore,
    settings: Settings,
    now: datetime,
) -> int:
    recovered = store.recover_stale_running_jobs(
        now=now,
        timeout_seconds=settings.worker_stale_job_timeout_seconds,
    )
    if recovered:
        logger.info(
            "Recovered stale sync jobs",
            extra={"worker_id": settings.worker_id, "recovered_count": recovered},
        )
    return recovered
