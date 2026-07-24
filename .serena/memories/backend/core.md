# Backend core
- FastAPI application under `backend/app`; SQLAlchemy async persistence and Alembic migrations.
- New Agent runtime spans `app/agent_runtime`, model-facing code under `app/ai`, governance/report services, and runtime Skills under `app/ai/skills`.
- Runtime Skills are parsed by `app/ai/skills/registry.py`; preserve its YAML schema and phase/tool/schema validation.
- API default: `http://127.0.0.1:8000`; docs at `/docs`.
- Durable AI/Agent jobs require a separate worker process.
- Configuration/secrets stay in ignored `.env`; do not print credentials.
- Trusted tenancy uses backend OperatorContext; clients cannot select or override tenant identity.