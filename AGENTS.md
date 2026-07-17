# Repository Guidelines

## Project Structure & Module Organization

This repository implements an AI-assisted organization-data reconciliation system. `backend/` contains FastAPI domain services, SQLAlchemy models, Alembic migrations, and pytest suites. `frontend/` contains the React reconciliation workbench and Playwright tests. `infra/` contains local services, and `openspec/changes/` contains implementation contracts.

## Build, Test, and Development Commands

Use Python 3.12. From the repository root:

```bash
cd backend
python3.12 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
docker compose -f ../infra/docker-compose.yml up -d
.venv/bin/alembic upgrade head
.venv/bin/uvicorn app.main:app --reload
.venv/bin/pytest
.venv/bin/ruff check .
.venv/bin/mypy app
cd ../frontend
npm install
npm run dev
npm test
npm run lint
npm run typecheck
npm run build
npm run test:e2e
cd ..
openspec validate ai-new-ui
```

The API is served at `http://127.0.0.1:8000`; interactive OpenAPI documentation is at `/docs`.

Document the real install, development, lint, and test commands here when a technology stack is introduced.

## Coding Style & Naming Conventions

Keep Markdown concise, use sentence-case headings, and wrap commands and paths in backticks. Use lowercase kebab-case for OpenSpec change names, such as `add-reconciliation-workbench`. For future code, follow the selected language's standard formatter and keep modules focused on one responsibility. Use descriptive domain names such as `reconciliation_task`, `difference_item`, and `execution_record` rather than generic names like `data` or `handler`.

## Testing Guidelines

Every implemented capability should include focused automated tests. Mirror source boundaries under `tests/`, name tests after observable behavior, and include cases for API failure, partial batch execution, idempotent retries, audit recording, and rollback conflicts. Use synthetic organization data only; never copy real teacher or student records into fixtures.

## Commit & Pull Request Guidelines

No Git history is available, so no repository-specific convention can be inferred. Use Conventional Commits (`feat:`, `fix:`, `docs:`, `test:`) with imperative summaries. Pull requests should reference the relevant OpenSpec change, explain behavior and risk, list verification commands, and include screenshots for user-interface changes. Call out API contract changes, migrations, and rollback limitations explicitly.

## Security & Configuration

Never commit credentials, tokens, exported organization data, or unredacted logs. Keep secrets in ignored environment files, mask sensitive fields in screenshots, and obtain operator identity from authenticated backend context rather than client-supplied IDs.
