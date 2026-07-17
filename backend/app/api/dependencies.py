from collections.abc import AsyncIterator

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import OperatorContext


def get_operator_context(request: Request) -> OperatorContext:
    settings = request.app.state.settings
    return OperatorContext(
        operator_id=settings.demo_operator_id,
        tenant_id=settings.demo_tenant_id,
    )


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.database.session_factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        else:
            await session.commit()
