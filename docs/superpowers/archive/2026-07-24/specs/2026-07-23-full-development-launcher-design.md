# Full development launcher design

## Goal

Provide one root-level `dev.py` command that starts the complete CSV Agent demo from a
normal checkout without requiring operators to coordinate four terminals.

## Scope

The launcher starts PostgreSQL through `infra/docker-compose.yml`, applies Alembic migrations,
then supervises FastAPI, the durable `app.agent_runtime` worker, and Vite. The legacy
`app.ai.worker` is not part of the new Agent execution path and is therefore not started.
API/database connectors remain subject to their existing rollout boundary.

The launcher uses `backend/.env` for local secrets. Because `.env` is intentionally ignored by
Git and is not copied into linked worktrees, a missing file produces an actionable error. The
launcher enables the CSV Agent rollout for its child processes with server-owned environment
overrides:

```text
RECONCILIATION_NEW_AGENT_ENABLED=true
RECONCILIATION_NEW_AGENT_ANALYSIS_ONLY=false
RECONCILIATION_NEW_AGENT_CSV_EXECUTION_ENABLED=true
```

It never creates, copies, logs, or prints secrets.

## Startup and shutdown

Startup is strictly ordered:

1. Validate Docker, Compose, backend virtual environment, frontend dependencies, and `.env`.
2. Reject occupied API or frontend ports before changing process state.
3. Run `docker compose up -d --wait`.
4. Run `alembic upgrade head`.
5. Start FastAPI and wait for `/health/ready`.
6. Start the Agent worker.
7. Start Vite and wait for its TCP port.

FastAPI, Agent worker, and Vite inherit the terminal output. If any supervised process exits,
the launcher terminates the others and exits unsuccessfully. `Ctrl+C` terminates all supervised
processes while leaving PostgreSQL running so local data is preserved.

## Error handling

Preflight errors name the missing prerequisite and the exact corrective action. Docker or
migration failure stops before application processes start. Readiness timeout and unexpected
child exit trigger coordinated shutdown. A `--dry-run` option prints only commands and
non-sensitive rollout settings for diagnostics.

## Verification

Unit tests exercise the deterministic launch plan, missing `.env`, occupied ports, command
failure, process supervision, and redaction. Repository quality gates remain the backend pytest,
Ruff, mypy, frontend unit/lint/typecheck/build, Playwright, clean PostgreSQL migration, and
OpenSpec validation commands.
