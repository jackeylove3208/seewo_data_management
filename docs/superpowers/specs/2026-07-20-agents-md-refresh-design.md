# AGENTS.md refresh design

## Purpose

Update the repository-level `AGENTS.md` so a development agent can quickly understand the product goal, current implementation stage, active contracts, real development commands, quality gates, and security boundaries.

The document will combine stable repository guidance with a dated project-status snapshot. The snapshot is informative rather than authoritative: code, tests, Git state, and OpenSpec artifacts remain the sources of truth.

## Document structure

The refreshed document will contain these sections:

1. Project mission and current status.
2. Repository structure and module ownership.
3. Current technology stack.
4. Local setup and the preferred development startup path.
5. Backend, frontend, database, and Docker commands.
6. OpenSpec workflow and the status of each current change.
7. Testing and quality gates.
8. Agent working rules.
9. Git and pull-request conventions.
10. Security, credentials, and synthetic-data rules.

## Current status snapshot

The snapshot will be dated `2026-07-20` and will record the output-derived OpenSpec task counts:

- `ai-new-ui`: complete, 73 of 73 tasks.
- `leftworkarea`: complete, 34 of 34 tasks.
- `demo`: in progress, 44 of 101 tasks.

It will explain that completed planning tasks do not by themselves prove production readiness. Agents must inspect the relevant implementation and run proportionate verification before claiming a capability is complete.

## Development guidance

The preferred local workflow will reflect the repository's current scripts:

- Python 3.12 and an editable backend installation in `backend/.venv`.
- `npm run dev` from `frontend/` as the normal full-stack development command. It starts FastAPI and Vite together and defaults to a local SQLite development database.
- Docker Compose as the PostgreSQL 16 plus pgvector option when PostgreSQL-specific behavior or migrations need verification.
- Separate backend and frontend lint, type-check, test, build, and end-to-end commands.
- The real enterprise-model smoke test remains opt-in and requires non-production credentials in the ignored `backend/.env` file.

## Agent workflow

Before changing behavior, an agent should:

1. Read `AGENTS.md` and inspect `git status` without discarding unrelated work.
2. Run `openspec list --json` and identify the relevant change.
3. Read that change's `proposal.md`, `design.md`, `tasks.md`, and affected specs.
4. Follow existing backend and frontend boundaries.
5. Add focused tests and run verification proportional to the change.
6. Update `AGENTS.md` only when commands, structure, security constraints, or the status snapshot materially changes.

## Accuracy and maintenance rules

- Commands must match checked-in scripts and configuration rather than historical planning documents.
- The status section must include an explicit date and commands for refreshing it.
- Stable guidance and volatile status information must remain visibly separate.
- Credentials, real organization exports, and unredacted personal information must never appear in the document or fixtures.
- The final update is documentation-only and does not change application behavior.

## Verification

After editing `AGENTS.md`:

- Re-read the full file for contradictions and obsolete commands.
- Confirm every referenced path exists.
- Confirm package scripts and Python commands match `frontend/package.json` and `backend/pyproject.toml`.
- Run `openspec list --json` and the relevant OpenSpec validation commands.
- Use lightweight command help or dry-run checks where available; do not start external services solely to validate documentation.
