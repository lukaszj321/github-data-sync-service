from __future__ import annotations

import argparse
import logging
import signal
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from github_data_sync_service import __version__
from github_data_sync_service.core.config import Settings, get_settings
from github_data_sync_service.core.logging import configure_logging
from github_data_sync_service.db.session import create_db_engine, create_session_factory
from github_data_sync_service.github.client import GitHubClient
from github_data_sync_service.queue.repository import SyncJobStore
from github_data_sync_service.worker.processor import IssueSyncProcessor, recover_stale_jobs

logger = logging.getLogger(__name__)
SleepFunc = Callable[[float], None]


class Worker:
    def __init__(self, *, sleep: SleepFunc = time.sleep) -> None:
        self._stopped = False
        self._sleep = sleep

    def stop(self, *_: object) -> None:
        self._stopped = True

    def run_once(
        self,
        *,
        session_factory: Callable[[], Session],
        github_client: GitHubClient,
        settings: Settings,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        current_time = now or (lambda: datetime.now(UTC))
        session = session_factory()
        try:
            store = SyncJobStore(session)
            recover_stale_jobs(
                store=store,
                settings=settings,
                now=current_time(),
            )
            job = store.claim_available_job(worker_id=settings.worker_id, now=current_time())
            if job is None:
                self._sleep(settings.worker_poll_interval_seconds)
                return
            logger.info(
                "Claimed sync job",
                extra={
                    "worker_id": settings.worker_id,
                    "job_id": str(job.id),
                    "repository_id": str(job.repository_id),
                    "resource_type": job.resource_type,
                    "attempt": job.attempt_count,
                },
            )
            try:
                processor = IssueSyncProcessor(
                    store=store,
                    github_client=github_client,
                    settings=settings,
                    now=current_time,
                )
                processor.process(job)
            except Exception as exc:
                self._handle_unexpected_job_error(
                    store=store,
                    job_id=job.id,
                    worker_id=settings.worker_id,
                    exc=exc,
                    backoff_seconds=settings.worker_poll_interval_seconds,
                )
        except Exception as exc:
            logger.error(
                "Worker iteration failed before job processing completed",
                extra={
                    "worker_id": settings.worker_id,
                    "error_type": exc.__class__.__name__,
                },
            )
            try:
                session.rollback()
            except Exception as rollback_exc:
                logger.error(
                    "Worker rollback failed after iteration error",
                    extra={
                        "worker_id": settings.worker_id,
                        "error_type": rollback_exc.__class__.__name__,
                    },
                )
            self._sleep(settings.worker_poll_interval_seconds)
        finally:
            session.close()

    def _handle_unexpected_job_error(
        self,
        *,
        store: SyncJobStore,
        job_id: uuid.UUID,
        worker_id: str,
        exc: Exception,
        backoff_seconds: float,
    ) -> None:
        error_code = f"internal_error: {exc.__class__.__name__}"
        logger.error(
            "Unexpected sync job error",
            extra={
                "worker_id": worker_id,
                "job_id": str(job_id),
                "error_code": error_code,
                "error_type": exc.__class__.__name__,
            },
        )
        try:
            store.rollback()
            store.fail_job(job_id, error_code)
        except Exception as fail_exc:
            logger.error(
                "Failed to mark sync job failed after unexpected error",
                extra={
                    "worker_id": worker_id,
                    "job_id": str(job_id),
                    "error_code": error_code,
                    "error_type": fail_exc.__class__.__name__,
                },
            )
            try:
                store.rollback()
            except Exception as rollback_exc:
                logger.error(
                    "Worker rollback failed after fail_job error",
                    extra={
                        "worker_id": worker_id,
                        "job_id": str(job_id),
                        "error_type": rollback_exc.__class__.__name__,
                    },
                )
            self._sleep(backoff_seconds)

    def run(self) -> None:
        settings = get_settings()
        configure_logging(settings.log_level)
        engine = create_db_engine(settings)
        factory = create_session_factory(engine)
        github_client = GitHubClient(
            base_url=settings.github_api_base_url,
            user_agent=settings.github_user_agent,
            api_version=settings.github_api_version,
            token=settings.github_token_value,
            connect_timeout_seconds=settings.github_connect_timeout_seconds,
            read_timeout_seconds=settings.github_read_timeout_seconds,
            max_attempts=settings.github_max_attempts,
        )
        signal.signal(signal.SIGTERM, self.stop)
        signal.signal(signal.SIGINT, self.stop)
        logger.info("Worker started", extra={"worker_id": settings.worker_id})
        try:
            while not self._stopped:
                self.run_once(
                    session_factory=factory,
                    github_client=github_client,
                    settings=settings,
                )
        finally:
            github_client.close()
            engine.dispose()
            logger.info("Worker stopped", extra={"worker_id": settings.worker_id})


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="github-data-sync-worker")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def main() -> None:
    build_parser().parse_args()
    Worker().run()


if __name__ == "__main__":
    main()
