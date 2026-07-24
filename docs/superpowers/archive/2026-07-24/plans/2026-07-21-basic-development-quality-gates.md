# Basic Development Quality Gates Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make clean pgvector PostgreSQL migration validation and backend/frontend quality checks reproducible locally and mandatory in CI.

**Architecture:** A pytest integration test owns clean-database lifecycle behind an explicit environment variable and rejects unsafe database names. GitHub Actions keeps backend, frontend, and migration concerns in separate jobs, while the documentation lists the exact local equivalents.

**Tech Stack:** Python 3.12, pytest, Alembic, SQLAlchemy, asyncpg, PostgreSQL 16 with pgvector, GitHub Actions, Node.js, Vitest, ESLint, TypeScript, Vite.

## Global Constraints

- The disposable URL is `RECONCILIATION_MIGRATION_TEST_DATABASE_URL`; it must target PostgreSQL and database `reconcile_migration_test`, never the normal `reconcile` database.
- The test creates and removes only the dedicated database, enables the existing pgvector extension, and makes no product API or AI gateway calls.
- CI runs Python 3.12 and Node dependency installation with `pip` and `npm ci`; it receives no model credentials or production data.
- Documentation commands must match CI command categories and use synthetic data only.

---

### Task 1: PostgreSQL clean-migration test

**Files:**
- Modify: `backend/tests/integration/test_migrations.py`
- Test: `backend/tests/integration/test_migrations.py`

**Interfaces:**
- Consumes: `RECONCILIATION_MIGRATION_TEST_DATABASE_URL`, an `asyncpg` PostgreSQL URL to `reconcile_migration_test`.
- Produces: an env-gated test that skips if the URL is absent and otherwise recreates the disposable database before `alembic upgrade head`.

- [ ] **Step 1: Write failing safety and clean-upgrade tests**

Add helpers that parse the supplied URL, reject non-PostgreSQL schemes or a database other than `reconcile_migration_test`, then test their error messages without a live server.

- [ ] **Step 2: Run the focused tests and verify the expected failure**

Run: `../backend/.venv/bin/pytest tests/integration/test_migrations.py -q`

Expected: FAIL because the migration-test helpers do not exist.

- [ ] **Step 3: Implement the database lifecycle and schema assertions**

Connect to the URL's maintenance database using the synchronous `postgresql` driver form, terminate existing test-database sessions, issue `DROP DATABASE IF EXISTS reconcile_migration_test`, recreate it, then run Alembic against the supplied URL. Assert the script-directory heads equal `alembic_version`, the `vector` extension exists, and execution/reporting/restore tables exist. Drop the test database in `finally`.

- [ ] **Step 4: Run ordinary and live focused migration tests**

Run: `../backend/.venv/bin/pytest tests/integration/test_migrations.py -q`

Expected: SQLite/offline tests pass and exactly the live PostgreSQL test skips when the environment variable is absent.

Run: `RECONCILIATION_MIGRATION_TEST_DATABASE_URL=postgresql+asyncpg://reconcile:reconcile@localhost:5432/reconcile_migration_test ../backend/.venv/bin/pytest tests/integration/test_migrations.py -q`

Expected: all migration tests pass against Docker PostgreSQL.

### Task 2: CI quality gates

**Files:**
- Modify: `.github/workflows/backend.yml`
- Create: `.github/workflows/frontend.yml`
- Test: workflow YAML parsing and the commands defined in each job

**Interfaces:**
- Consumes: backend extras from `backend/pyproject.toml`, frontend lockfile and scripts from `frontend/package.json`, and the PostgreSQL smoke-test environment variable from Task 1.
- Produces: independent backend, frontend, and pgvector migration jobs on pushes and pull requests.

- [ ] **Step 1: Add the expected workflow job definitions**

Define a backend job using `python -m pip install -e './backend[dev]'`, pytest, Ruff, and mypy. Define a frontend workflow using `npm ci`, `npm test -- --run`, lint, typecheck, and build. Define a PostgreSQL service job using `pgvector/pgvector:0.8.1-pg16` and the dedicated disposable database URL.

- [ ] **Step 2: Validate the workflow YAML and inspect all commands**

Run: `ruby -e "require 'yaml'; ARGV.each { |path| YAML.load_file(path) }" .github/workflows/backend.yml .github/workflows/frontend.yml`

Expected: exit 0; every quality command uses repository dependencies and no model credential or production-data environment variable appears.

### Task 3: Documentation and OpenSpec completion

**Files:**
- Modify: `AGENTS.md`
- Modify: `backend/README.md`
- Modify: `openspec/changes/basic-development/tasks.md`
- Modify: `openspec/changes/demo/tasks.md`

**Interfaces:**
- Consumes: completed commands from Tasks 1 and 2.
- Produces: a local command map and checked OpenSpec tasks only after commands have passed.

- [ ] **Step 1: Document local setup and verification commands**

Document Docker startup from the repository root, the dedicated migration-test environment variable, backend checks, frontend `npm ci` checks, and the fact that neither normal quality checks nor the migration smoke test need model credentials.

- [ ] **Step 2: Run the full verification sequence**

Run backend tests, Ruff, and mypy; frontend tests in non-watch mode, lint, typecheck, and build; the live PostgreSQL migration test; OpenSpec validation for `basic-development` and `demo`.

Expected: all commands exit 0.

- [ ] **Step 3: Mark contracts complete and commit**

Check every completed item in `basic-development/tasks.md`, then check only demo items `1.7` and `2.4`. Commit the implementation with `feat: add delivery quality gates`.
