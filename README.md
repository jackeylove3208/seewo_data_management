# Organization reconciliation demo

This repository contains the FastAPI backend, React workbench, PostgreSQL storage and durable
Agent workers for the organization-data reconciliation demo.

## Start the complete demo

Create `backend/.env` from `backend/.env.example`, set the DeepSeek-compatible model endpoint,
API key and a tokenization secret of at least 16 characters, then run:

```bash
python3 dev.py
```

The launcher starts PostgreSQL, applies Alembic migrations, starts FastAPI, starts both durable
Agent worker loops, starts Vite and opens `http://127.0.0.1:5173`. Stop the whole stack with
`Ctrl+C`.

The launcher explicitly enables the controlled graph demo:

```dotenv
RECONCILIATION_NEW_AGENT_ENABLED=true
RECONCILIATION_AGENT_GRAPH_ENABLED=true
RECONCILIATION_AGENT_GRAPH_CSV_EXECUTION_ENABLED=true
RECONCILIATION_NEW_AGENT_ANALYSIS_ONLY=false
RECONCILIATION_NEW_AGENT_CSV_EXECUTION_ENABLED=true
```

These are development overrides. The application settings remain default-off, so a normal
deployment cannot route new tasks to `agent-graph-v1` without an explicit rollout decision.

To inspect commands without starting services or printing secrets:

```bash
python3 dev.py --dry-run
```

## Manual startup

Use four terminals after Docker Desktop is running:

```bash
cd /path/to/PythonProject
docker compose -f infra/docker-compose.yml up -d --wait
```

```bash
cd /path/to/PythonProject/backend
.venv/bin/alembic upgrade head
.venv/bin/uvicorn app.main:app --reload
```

```bash
cd /path/to/PythonProject/backend
.venv/bin/python -m app.agent_runtime
```

```bash
cd /path/to/PythonProject/frontend
npm run dev:web
```

`legacy-v1`, fixed `new-agent-v1` and controlled `agent-graph-v1` are immutable task versions.
The fixed worker claims only `new-agent-v1`; the graph worker claims only `agent-graph-v1`.

## Verification

```bash
cd backend
PYTHONPATH="$PWD" .venv/bin/pytest --import-mode=importlib
.venv/bin/ruff check .
.venv/bin/mypy app
cd ../frontend
npm test -- --run
npm run lint
npm run typecheck
npm run build
npm run test:e2e
```

The automated suites use synthetic records and do not need live model credentials.
