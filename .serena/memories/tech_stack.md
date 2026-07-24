# Tech stack
- Darwin development host; zsh.
- Python 3.12 only (`>=3.12,<3.13`).
- Backend: FastAPI, SQLAlchemy async, Alembic, asyncpg/aiosqlite, Pydantic v2/settings, Uvicorn, pytest/pytest-asyncio, Ruff, strict mypy.
- Data/model integration: Polars, pgvector, RapidFuzz, HTTPX, MCP, Tenacity.
- Frontend: React 19, TypeScript 5.8, Vite 7, React Router 7, TanStack Query, Ant Design, Vitest, Testing Library, Playwright, ESLint.
- Infra: Docker Compose PostgreSQL with pgvector.
- Backend package data includes normalization rules, runtime Agent `SKILL.md` files, and report templates.