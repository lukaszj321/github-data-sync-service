# GitHub Data Sync Service 0.1.0

GitHub Data Sync Service 0.1.0 introduces the backend foundation for reliable synchronization of public GitHub engineering data.

This milestone focuses on repository registration and validation rather than full resource synchronization.

## Highlights

- Validates public repositories through the live GitHub REST API.
- Stores repository metadata idempotently in PostgreSQL.
- Uses the stable GitHub repository ID to support repository renames and transfers.
- Provides FastAPI endpoints for registering, listing, and reading repositories.
- Includes a PostgreSQL-backed job queue foundation using `FOR UPDATE SKIP LOCKED`.
- Runs the API, database migrations, worker, and PostgreSQL as separate Docker Compose services.
- Protects optional GitHub tokens from logs and user-facing errors.
- Includes deterministic unit tests, PostgreSQL integration tests, and an opt-in live GitHub API test.

## API

Implemented endpoints:

- `POST /repositories`
- `GET /repositories`
- `GET /repositories/{repository_id}`
- `GET /health`
- `GET /ready`

## Quality

The release is validated with:

- Ruff linting and formatting checks.
- Strict mypy checks.
- Unit tests with branch coverage above 85%.
- PostgreSQL integration tests.
- Alembic migration checks.
- Wheel and source distribution builds.
- Installation from the built wheel.
- Docker image build and non-root runtime verification.
- GitHub Actions.

## Current Scope

Version 0.1.0 does not yet synchronize issues, pull requests, commits, releases, or workflow runs.

The worker and `sync_jobs` table provide the execution foundation for the next milestone, but resource synchronization is deliberately outside this release.

## Limitations

- Public GitHub repositories only.
- No issues synchronization yet.
- No pagination or ETag support yet.
- No OAuth.
- No frontend.
- No Redis, Celery, Kafka, Kubernetes, or cloud deployment.
- Not intended as a production GitHub analytics platform.
