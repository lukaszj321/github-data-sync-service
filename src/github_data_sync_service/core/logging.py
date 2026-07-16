from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from logging.config import dictConfig
from typing import Any, ClassVar

SECRET_PATTERNS = [
    re.compile(r"(Authorization:\s*Bearer\s+)[^\s,;]+", re.IGNORECASE),
    re.compile(r"(password=)[^@\s]+", re.IGNORECASE),
    re.compile(r"(postgres(?:ql)?(?:\+psycopg)?://[^:\s]+:)[^@\s]+(@)", re.IGNORECASE),
    re.compile(r"(github_pat_|ghp_|gho_|ghu_|ghs_|ghr_)[A-Za-z0-9_]+"),
]


def redact_secrets(value: object) -> str:
    text = str(value)
    for pattern in SECRET_PATTERNS:
        text = pattern.sub(r"\1***\2", text) if pattern.groups >= 2 else pattern.sub("***", text)
    return text


class JsonFormatter(logging.Formatter):
    reserved: ClassVar[set[str]] = {
        "name",
        "msg",
        "args",
        "levelname",
        "levelno",
        "pathname",
        "filename",
        "module",
        "exc_info",
        "exc_text",
        "stack_info",
        "lineno",
        "funcName",
        "created",
        "msecs",
        "relativeCreated",
        "thread",
        "threadName",
        "processName",
        "process",
        "message",
    }

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": redact_secrets(record.getMessage()),
        }
        for key, value in record.__dict__.items():
            if key not in self.reserved and not key.startswith("_"):
                payload[key] = redact_secrets(value) if isinstance(value, str) else value
        if record.exc_info:
            payload["exception"] = redact_secrets(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(level: str) -> None:
    dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {"json": {"()": JsonFormatter}},
            "handlers": {
                "default": {
                    "class": "logging.StreamHandler",
                    "formatter": "json",
                }
            },
            "root": {"handlers": ["default"], "level": level.upper()},
        }
    )
