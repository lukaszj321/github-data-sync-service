# Changelog

## [0.1.0] - 2026-07-17

### Added

- FastAPI endpoints for registering, listing, and reading public GitHub repositories.
- GitHub REST API client with optional token authentication, timeout handling, selective retries, rate-limit classification, and secret-safe diagnostics.
- Idempotent PostgreSQL repository upserts based on the stable GitHub repository identifier.
- Repository rename and transfer handling.
- PostgreSQL-backed `sync_jobs` queue foundation using `FOR UPDATE SKIP LOCKED`.
- Separate API, migration, worker, and PostgreSQL services in Docker Compose.
- Alembic migrations for repositories, sync jobs, indexes, constraints, and foreign keys.
- Structured JSON logging with request and worker context.
- Health and PostgreSQL readiness endpoints.
- Unit, PostgreSQL integration, and optional live GitHub API tests.
- Ruff, mypy, branch coverage, package build, wheel installation, and Docker checks in GitHub Actions.

### Limitations

- Milestone 1 does not synchronize issues or other repository resources.
- No pagination or ETag support yet.
- The worker currently provides the PostgreSQL queue foundation but does not execute resource synchronization.
- Public repositories only.
- No OAuth, frontend, Redis, Celery, Kafka, Kubernetes, or cloud deployment.
