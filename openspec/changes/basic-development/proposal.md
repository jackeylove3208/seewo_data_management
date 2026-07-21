## Why

The project has working backend and frontend checks, but they are not enforced as one reproducible delivery gate. SQLite and offline PostgreSQL DDL checks also do not prove that a clean PostgreSQL database can apply the complete Alembic history used in deployment.

## What Changes

- Add a CI quality gate that verifies backend tests, formatting/linting, typing, frontend tests, linting, typing, and production build.
- Run an isolated PostgreSQL plus pgvector migration smoke test from a clean database in CI and document the equivalent local Docker workflow.
- Make the documented development, verification, and troubleshooting commands match the commands executed by CI.
- Preserve the existing synchronous durable analysis worker workflow; do not introduce Redis, Celery, new product APIs, or application behavior changes.

## Capabilities

### New Capabilities
- `delivery-quality-gates`: Reproducible local and CI validation for backend, frontend, and clean PostgreSQL migration readiness.

### Modified Capabilities

None.

## Impact

- Affects GitHub Actions workflows, Docker-based local verification, development documentation, and migration test coverage.
- Uses the existing `pgvector/pgvector` Docker image, Alembic configuration, Python 3.12 backend, and Node frontend tooling.
- Adds no external product API and does not change reconciliation, governance execution, reporting, or restore semantics.
