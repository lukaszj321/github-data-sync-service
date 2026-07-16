from __future__ import annotations

from fastapi import APIRouter, Request
from sqlalchemy import text

from github_data_sync_service.core.errors import AppError

router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
def ready(request: Request) -> dict[str, str]:
    try:
        with request.app.state.engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception as exc:
        raise AppError("postgres_unavailable", "PostgreSQL is not available.", 503) from exc
    return {"status": "ready"}
