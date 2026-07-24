# Suggested commands
## Backend setup and runtime
```bash
cd backend
python3.12 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
docker compose -f ../infra/docker-compose.yml up -d
.venv/bin/alembic upgrade head
.venv/bin/uvicorn app.main:app --reload
.venv/bin/python -m app.ai.worker
```
Run API and durable worker in separate terminals.

## Backend checks
```bash
.venv/bin/pytest
.venv/bin/ruff check .
.venv/bin/mypy app
```

## Frontend
```bash
cd frontend
npm install
npm run dev
npm test
npm run lint
npm run typecheck
npm run build
npm run test:e2e
```

## OpenSpec
```bash
openspec validate <change-name>
```

## Clean PostgreSQL migration smoke
```bash
cd backend
RECONCILIATION_MIGRATION_TEST_DATABASE_URL=postgresql+asyncpg://reconcile:reconcile@127.0.0.1:5432/reconcile_migration_test \
  .venv/bin/pytest tests/integration/test_migrations.py::test_clean_postgresql_migration_reaches_head -q
```
The smoke test refuses the normal `reconcile` database URL.