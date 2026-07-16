from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class GitHubErrorDetails:
    github_request_id: str | None = None
    status_code: int | None = None
    rate_limit_limit: int | None = None
    retry_after_seconds: int | None = None
    rate_limit_remaining: int | None = None
    rate_limit_reset: int | None = None


class GitHubClientError(Exception):
    code = "github_client_error"
    retryable = False

    def __init__(self, message: str, details: GitHubErrorDetails | None = None) -> None:
        super().__init__(message)
        self.details = details or GitHubErrorDetails()


class GitHubTimeoutError(GitHubClientError):
    code = "github_timeout"
    retryable = True


class GitHubTransportError(GitHubClientError):
    code = "github_transport_error"
    retryable = True


class GitHubUnauthorizedError(GitHubClientError):
    code = "github_unauthorized"


class GitHubForbiddenError(GitHubClientError):
    code = "github_forbidden"


class GitHubRateLimitError(GitHubClientError):
    code = "github_rate_limited"
    retryable = True


class GitHubNotFoundError(GitHubClientError):
    code = "github_repository_not_found"


class GitHubValidationError(GitHubClientError):
    code = "github_validation_error"


class GitHubTemporaryError(GitHubClientError):
    code = "github_temporary_error"
    retryable = True


class GitHubBadResponseError(GitHubClientError):
    code = "github_bad_response"
