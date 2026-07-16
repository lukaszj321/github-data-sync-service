from __future__ import annotations

import argparse
import logging
import signal
import time

from github_data_sync_service import __version__
from github_data_sync_service.core.config import get_settings
from github_data_sync_service.core.logging import configure_logging
from github_data_sync_service.db.session import create_db_engine, create_session_factory
from github_data_sync_service.queue.repository import SyncJobStore

logger = logging.getLogger(__name__)


class Worker:
    def __init__(self) -> None:
        self._stopped = False

    def stop(self, *_: object) -> None:
        self._stopped = True

    def run(self) -> None:
        settings = get_settings()
        configure_logging(settings.log_level)
        engine = create_db_engine(settings)
        factory = create_session_factory(engine)
        signal.signal(signal.SIGTERM, self.stop)
        signal.signal(signal.SIGINT, self.stop)
        logger.info("Worker started", extra={"worker_id": settings.worker_id})
        try:
            while not self._stopped:
                session = factory()
                try:
                    store = SyncJobStore(session)
                    job = store.claim_available_job(worker_id=settings.worker_id)
                    if job is None:
                        time.sleep(settings.worker_poll_interval_seconds)
                        continue
                    store.fail_job(job.id, f"Unsupported resource_type: {job.resource_type}")
                    logger.info(
                        "Unsupported job failed",
                        extra={"worker_id": settings.worker_id, "job_id": str(job.id)},
                    )
                finally:
                    session.close()
        finally:
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
