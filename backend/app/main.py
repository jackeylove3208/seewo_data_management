import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes.analyses import router as analysis_router
from app.api.routes.analysis_jobs import router as analysis_job_router
from app.api.routes.differences import router as difference_router
from app.api.routes.health import router as health_router
from app.api.routes.proposals import router as proposal_router
from app.api.routes.reconciliation_tasks import router as task_router
from app.api.routes.rematching_jobs import router as rematching_router
from app.api.routes.uploads import router as upload_router
from app.core.config import Settings, get_settings
from app.core.database import Database
from app.models import Base


def create_app(settings: Settings | None = None) -> FastAPI:
    configured = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configured.ensure_storage_directories()
        app.state.settings = configured
        preview_secret = configured.proposal_preview_secret or configured.tokenization_secret
        app.state.proposal_preview_secret = (
            preview_secret.get_secret_value().encode()
            if preview_secret is not None
            else secrets.token_bytes(32)
        )
        app.state.database = Database(configured.database_url)
        if configured.auto_create_schema:
            async with app.state.database.engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
        try:
            yield
        finally:
            await app.state.database.dispose()

    app = FastAPI(title=configured.app_name, version="0.1.0", lifespan=lifespan)
    app.include_router(health_router, prefix="/health", tags=["health"])
    app.include_router(analysis_router)
    app.include_router(analysis_job_router)
    app.include_router(difference_router)
    app.include_router(upload_router)
    app.include_router(task_router)
    app.include_router(proposal_router)
    app.include_router(rematching_router)
    return app


app = create_app()
