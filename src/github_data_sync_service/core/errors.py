from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class ErrorResponse:
    code: str
    message: str
    status_code: int
    details: dict[str, Any] | None = None


class AppError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        status_code: int,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.error = ErrorResponse(code, message, status_code, details)
