## 1. Clean PostgreSQL migration verification

- [x] 1.1 Define a dedicated environment variable and safety checks for a disposable PostgreSQL migration-test database.
- [x] 1.2 Add a pytest integration test that enables pgvector, applies the full Alembic history to the clean database, and validates the Alembic head plus essential schema objects.
- [x] 1.3 Start the local Docker PostgreSQL service and run the clean migration test against it; retain the ordinary local skip path when its URL is absent.

## 2. Continuous integration quality gates

- [x] 2.1 Update the backend GitHub Actions workflow to run backend tests, Ruff, and mypy with deterministic dependency installation.
- [x] 2.2 Add a frontend GitHub Actions workflow or job that runs `npm ci`, unit tests, lint, typecheck, and the production build.
- [x] 2.3 Add an isolated pgvector PostgreSQL migration job that supplies the dedicated migration-test database URL and runs the migration integration test.
- [x] 2.4 Validate workflow YAML and confirm commands do not require model credentials or production data.

## 3. Developer documentation and contract completion

- [x] 3.1 Document the exact local setup and verification commands, including Docker startup and the clean PostgreSQL migration smoke test, in `AGENTS.md` and developer-facing documentation.
- [x] 3.2 Update the demo OpenSpec task checklist for `1.7` and `2.4` only after the documented commands and live PostgreSQL validation pass.
- [x] 3.3 Run the full backend and frontend quality suite, the clean PostgreSQL migration validation, and `openspec validate basic-development`.
