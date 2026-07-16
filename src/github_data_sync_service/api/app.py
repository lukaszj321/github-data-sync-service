from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from github_data_sync_service.api.error_handlers import install_error_handlers
from github_data_sync_service.api.routes.health import router as health_router
from github_data_sync_service.api.routes.repositories import router as repositories_router
from github_data_sync_service.core.config import get_settings
from github_data_sync_service.core.logging import configure_logging
from github_data_sync_service.db.session import create_db_engine, create_session_factory


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level)
    engine = create_db_engine(settings)
    app.state.engine = engine
    app.state.session_factory = create_session_factory(engine)
    try:
        yield
    finally:
        engine.dispose()


def create_app() -> FastAPI:
    app = FastAPI(title="github-data-sync-service", version="0.1.0", lifespan=lifespan)
    install_error_handlers(app)
    app.include_router(health_router)
    app.include_router(repositories_router)
    return app


app = create_app()
