from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from github_data_sync_service.github.errors import (
    GitHubBadResponseError,
    GitHubClientError,
    GitHubErrorDetails,
    GitHubForbiddenError,
    GitHubNotFoundError,
    GitHubRateLimitError,
    GitHubTemporaryError,
    GitHubTimeoutError,
    GitHubTransportError,
    GitHubUnauthorizedError,
    GitHubValidationError,
)
from github_data_sync_service.github.models import GitHubRateLimit, GitHubRepository

logger = logging.getLogger(__name__)
SleepFunc = Callable[[float], None]
JitterFunc = Callable[[], float]


class GitHubClient:
    def __init__(
        self,
        *,
        base_url: str,
        user_agent: str,
        api_version: str,
        token: str | None,
        connect_timeout_seconds: float,
        read_timeout_seconds: float,
        max_attempts: int,
        sleep: SleepFunc = time.sleep,
        jitter: JitterFunc = random.random,
        client: httpx.Client | None = None,
    ) -> None:
        self._base_headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": user_agent,
            "X-GitHub-Api-Version": api_version,
        }
        self._authorization_header = f"Bearer {token}" if token else None
        self._max_attempts = max_attempts
        self._sleep = sleep
        self._jitter = jitter
        timeout = httpx.Timeout(
            connect=connect_timeout_seconds,
            read=read_timeout_seconds,
            write=read_timeout_seconds,
            pool=connect_timeout_seconds,
        )
        self._client = client or httpx.Client(base_url=base_url, timeout=timeout)

    def __repr__(self) -> str:
        return "GitHubClient(token=***hidden***)"

    def close(self) -> None:
        self._client.close()

    def get_repository(self, owner: str, repo: str) -> GitHubRepository:
        path = f"/repos/{owner}/{repo}"
        last_error: GitHubClientError | None = None
        for attempt in range(1, self._max_attempts + 1):
            try:
                response = self._client.get(path, headers=self._headers())
                return self._handle_response(response)
            except GitHubClientError as exc:
                last_error = exc
                if not self._should_retry(exc, attempt):
                    raise
                delay = self._retry_delay(exc, attempt)
                logger.info(
                    "Retrying GitHub request",
                    extra={
                        "attempt": attempt,
                        "status_code": exc.details.status_code,
                        "github_request_id": exc.details.github_request_id,
                    },
                )
                self._sleep(delay)
            except httpx.TimeoutException as exc:
                last_error = GitHubTimeoutError("GitHub request timed out")
                if not self._should_retry(last_error, attempt):
                    raise last_error from exc
                self._sleep(self._retry_delay(last_error, attempt))
            except httpx.TransportError as exc:
                last_error = GitHubTransportError("GitHub transport error")
                if not self._should_retry(last_error, attempt):
                    raise last_error from exc
                self._sleep(self._retry_delay(last_error, attempt))
        if last_error is not None:
            raise last_error
        raise GitHubTransportError("GitHub request failed")

    def _headers(self) -> dict[str, str]:
        headers = dict(self._base_headers)
        if self._authorization_header:
            headers["Authorization"] = self._authorization_header
        return headers

    def _handle_response(self, response: httpx.Response) -> GitHubRepository:
        details = self._details(response)
        if response.status_code == 200:
            try:
                data = response.json()
            except ValueError as exc:
                raise GitHubBadResponseError("GitHub returned invalid JSON", details) from exc
            return self._parse_repository(data, details)
        if response.status_code == 401:
            raise GitHubUnauthorizedError("GitHub authentication failed", details)
        if response.status_code == 403:
            if self._is_rate_limited(response):
                raise GitHubRateLimitError("GitHub rate limit exceeded", details)
            raise GitHubForbiddenError("GitHub repository is private or inaccessible", details)
        if response.status_code == 404:
            raise GitHubNotFoundError("GitHub repository was not found", details)
        if response.status_code == 422:
            raise GitHubValidationError("GitHub rejected repository identifier", details)
        if response.status_code in {408, 429}:
            raise GitHubRateLimitError("GitHub request should be retried later", details)
        if response.status_code in {500, 502, 503, 504}:
            raise GitHubTemporaryError("GitHub temporary error", details)
        raise GitHubBadResponseError("GitHub returned an unexpected status", details)

    def _parse_repository(self, data: Any, details: GitHubErrorDetails) -> GitHubRepository:
        if not isinstance(data, dict):
            raise GitHubBadResponseError("GitHub returned an unexpected JSON structure", details)
        try:
            owner_data = data["owner"]
            if not isinstance(owner_data, dict):
                raise KeyError("owner")
            return GitHubRepository(
                github_id=int(data["id"]),
                owner=str(owner_data["login"]),
                name=str(data["name"]),
                full_name=str(data["full_name"]),
                html_url=str(data["html_url"]),
                description=data.get("description"),
                default_branch=str(data["default_branch"]),
                is_fork=bool(data["fork"]),
                is_archived=bool(data["archived"]),
                is_private=bool(data["private"]),
                github_created_at=_parse_datetime(str(data["created_at"])),
                github_updated_at=_parse_datetime(str(data["updated_at"])),
                github_request_id=details.github_request_id,
                rate_limit=GitHubRateLimit(
                    limit=details.rate_limit_limit,
                    remaining=details.rate_limit_remaining,
                    reset=details.rate_limit_reset,
                    retry_after_seconds=details.retry_after_seconds,
                ),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise GitHubBadResponseError(
                "GitHub repository response is missing required fields", details
            ) from exc

    def _details(self, response: httpx.Response) -> GitHubErrorDetails:
        return GitHubErrorDetails(
            github_request_id=response.headers.get("X-GitHub-Request-Id"),
            status_code=response.status_code,
            rate_limit_limit=_parse_optional_int(response.headers.get("X-RateLimit-Limit")),
            retry_after_seconds=_parse_retry_after(response.headers.get("Retry-After")),
            rate_limit_remaining=_parse_optional_int(response.headers.get("X-RateLimit-Remaining")),
            rate_limit_reset=_parse_optional_int(response.headers.get("X-RateLimit-Reset")),
        )

    def _is_rate_limited(self, response: httpx.Response) -> bool:
        remaining = response.headers.get("X-RateLimit-Remaining")
        return remaining == "0" or "Retry-After" in response.headers

    def _should_retry(self, error: GitHubClientError, attempt: int) -> bool:
        return error.retryable and attempt < self._max_attempts

    def _retry_delay(self, error: GitHubClientError, attempt: int) -> float:
        if error.details.retry_after_seconds is not None:
            return float(error.details.retry_after_seconds)
        jitter_value: float = float(self._jitter())
        delay: float = min(30.0, (2 ** (attempt - 1)) + jitter_value)
        return delay


def _parse_datetime(value: str) -> datetime:
    normalized = value.replace("Z", "+00:00")
    return datetime.fromisoformat(normalized)


def _parse_optional_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _parse_retry_after(value: str | None) -> int | None:
    if value is None:
        return None
    seconds = _parse_optional_int(value)
    if seconds is not None:
        return seconds
    try:
        retry_at = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    delay = retry_at.timestamp() - time.time()
    return max(0, int(delay))
