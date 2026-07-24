# Project core
- AI-assisted organization-data reconciliation system.
- `backend/`: FastAPI services, SQLAlchemy models, Alembic migrations, Agent runtime, pytest. Read `mem:backend/core` for backend boundaries.
- `frontend/`: React reconciliation workbench and tests. Read `mem:frontend/core` for frontend commands/stack.
- `infra/`: local PostgreSQL/pgvector services.
- `openspec/changes/`: implementation contracts; active Agent redesign is under `new-agent-architecture`.
- Security invariants: never commit secrets or real organization data; backend obtains operator identity from trusted context, never client-supplied tenant IDs.
- Build/test entry points: `mem:suggested_commands`; completion gates: `mem:task_completion`; code conventions: `mem:conventions`.