from __future__ import annotations

import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from github_data_sync_service.core.errors import AppError


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        request_id = request.headers.get("X-Request-Id", str(uuid.uuid4()))
        body = {
            "error": {
                "code": exc.error.code,
                "message": exc.error.message,
                "request_id": request_id,
            }
        }
        if exc.error.details:
            safe_details = {k: v for k, v in exc.error.details.items() if v is not None}
            if safe_details:
                body["error"].update(safe_details)
        return JSONResponse(status_code=exc.error.status_code, content=body)
