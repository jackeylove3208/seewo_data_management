## Context

The repository has independent backend and frontend commands, a backend-only GitHub Actions workflow, and a Docker Compose PostgreSQL 16 image with pgvector. Alembic migrations are covered by SQLite and offline PostgreSQL SQL tests, but the full migration history has not been applied to an empty live PostgreSQL instance as a required delivery gate.

This change closes the remaining foundation tasks without changing reconciliation business behavior. It must remain usable by a developer who has Docker, Python 3.12, and Node installed, and by GitHub Actions on a clean runner.

## Goals / Non-Goals

**Goals:**

- Define one documented verification sequence for backend, frontend, and database migration readiness.
- Apply `alembic upgrade head` to an isolated clean PostgreSQL database with the required pgvector extension, then assert the expected Alembic head and essential schema objects.
- Run backend and frontend quality checks in CI with dependency installation and caching appropriate to each ecosystem.
- Make migration failures actionable and ensure local verification does not use a developer's persistent database volume.

**Non-Goals:**

- Add Redis, Celery, new background workers, new application endpoints, or product behavior.
- Run paid or real enterprise-model smoke tests in CI.
- Require full browser E2E coverage in this focused change; Playwright remains a separate final-verification task.
- Replace Docker Compose, Alembic, pytest, Vitest, or GitHub Actions with another tool.

## Decisions

### 1. Use a dedicated ephemeral PostgreSQL database for migration checks

The migration test SHALL create or target a database isolated from the normal local `reconcile` volume, enable `vector`, apply migrations, and inspect the resulting database through SQLAlchemy/Alembic. CI will use a disposable PostgreSQL service container; local developers will use a documented Compose command and a uniquely named temporary database.

This is preferred to SQLite-only testing because JSONB, trigger DDL, UUID behavior, pgvector, and Alembic branch-head handling are PostgreSQL-specific. It is preferred to testing the persistent compose volume because a prior schema could hide a missing migration.

### 2. Keep migration validation in the backend test suite

The PostgreSQL clean-upgrade check will be a pytest integration test gated by an explicit database URL environment variable. It will skip locally when the variable is absent, while CI sets it and treats failure as blocking. The test will verify the Alembic head, `vector` extension, and schema objects required by the current migration history.

This keeps assertions close to existing migration tests and avoids duplicating schema knowledge in shell scripts. A shell-only `alembic upgrade head` check would detect failure but provide weaker regression coverage.

### 3. Split CI by runtime boundary

GitHub Actions will have separate backend and frontend jobs, plus a PostgreSQL migration job. Backend runs pytest, Ruff, and mypy. Frontend runs Vitest, ESLint, TypeScript checking, and the production Vite build. The migration job runs the backend migration integration test against PostgreSQL with pgvector.

Separate jobs make failures attributable and allow caching Python and npm dependencies independently. The migration job remains isolated so no test database state leaks into ordinary backend tests.

### 4. Make documentation the canonical command map

`AGENTS.md`, backend documentation, and a concise top-level developer guide will list install, Docker startup, migration, backend checks, frontend checks, and the PostgreSQL migration smoke command. The commands will be copied from CI rather than described abstractly.

This prevents local and CI verification from diverging. The guide will also state which checks require Docker and which do not.

## Risks / Trade-offs

- [Docker unavailable or daemon stopped] -> The local PostgreSQL test skips with an explicit prerequisite; CI remains mandatory and documentation provides the compose startup command.
- [Persistent database masks a migration defect] -> Use a dedicated clean database name and drop it before/after the migration test.
- [Migration test has destructive SQL] -> Restrict the test URL to a named disposable database and reject the default development database name.
- [CI duration increases] -> Run only migration-specific assertions in the PostgreSQL job and cache Python/npm dependencies in their respective jobs.
- [Future migration changes alter expected head] -> Determine the expected head through Alembic's script directory instead of hard-coding a revision identifier where possible.

## Migration Plan

1. Add the environment-gated clean PostgreSQL migration test and run it against the local Docker service.
2. Add the CI backend, frontend, and migration gates.
3. Document the matching local commands and troubleshooting steps.
4. Verify all local checks and CI configuration syntax.

Rollback consists of reverting the CI/documentation changes and the migration test. No production schema migration or application data is changed by this feature.

## Open Questions

None. The existing PostgreSQL 16 pgvector Compose image and GitHub Actions platform are the chosen validation targets.
