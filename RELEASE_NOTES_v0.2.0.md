# GitHub Data Sync Service v0.2.0

`v0.2.0` turns the project from a repository-registration foundation into a working issues synchronization service.

## Highlights

- Synchronizes GitHub issues for registered public repositories.
- Runs synchronization in a separate worker process backed by PostgreSQL jobs.
- Reads GitHub Issues API pages through validated `rel="next"` pagination.
- Filters pull requests returned by the issues endpoint.
- Stores issues idempotently using stable GitHub identifiers.
- Distinguishes created, updated, unchanged, skipped, and fetched records.
- Reschedules rate-limited jobs without holding database locks or sleeping for long periods.
- Recovers stale `running` jobs after an expired worker heartbeat.
- Isolates unexpected failures to a single job so the worker process remains available.
- Preserves previously committed pages when a later page fails.
- Avoids duplicate active jobs for the same repository and resource.

## API

New endpoints in `0.2.0`:

- `POST /repositories/{repository_id}/sync`
- `GET /sync-jobs`
- `GET /sync-jobs/{job_id}`
- `GET /repositories/{repository_id}/issues`

Existing repository and health endpoints remain available.

## Synchronization Flow

1. A client creates an `issues` synchronization job.
2. The worker claims the job from PostgreSQL.
3. The worker requests one GitHub API page without holding a database transaction.
4. Issues and job statistics for that page are committed atomically.
5. Pull requests are counted as skipped and are not stored as issues.
6. The process continues through the validated `rel="next"` URL.
7. The job finishes as `completed`, `failed`, or `rate_limited`.

## Reliability

- PostgreSQL partial unique index prevents duplicate active jobs.
- Each page is stored in a short transaction.
- Retried jobs restart from the first page and rely on idempotent upserts.
- Rate limits use `Retry-After`, `X-RateLimit-Reset`, or a configured fallback.
- Unexpected job failures are rolled back and safely recorded.
- Stale worker locks can be recovered.
- Worker and API containers run as non-root users.

## Quality

The release is validated with:

- Ruff linting and formatting checks.
- Strict mypy checks.
- Unit tests with branch coverage above 85%.
- PostgreSQL integration tests.
- Alembic upgrade and downgrade validation.
- Optional live GitHub API tests.
- A complete Docker end-to-end issue synchronization smoke test.
- Wheel and source distribution builds.
- Installation and CLI version checks from the built wheel.
- Docker non-root runtime verification.
- GitHub Actions for both branch and release tag pushes.

## Limitations

- Public GitHub repositories only.
- No ETag or incremental synchronization.
- No persistent page checkpoint or resume from a page number.
- No automatic pruning of locally stored issues.
- No dedicated synchronization for pull requests, commits, releases, or workflow runs.
- No labels, comments, milestones, or assignee normalization.
- No OAuth.
- No frontend.
- No Redis, Celery, Kafka, Kubernetes, or cloud deployment.
- Not intended as a complete GitHub analytics platform.
