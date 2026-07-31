from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api_connectors.policy import TASK_EPHEMERAL_CREDENTIAL_TTL
from app.api_connectors.secrets import revoke_all_expired_ephemeral_connections


class ApiConnectorCredentialMaintenanceWorker:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        scan_interval: timedelta = timedelta(minutes=5),
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._scan_interval = scan_interval
        self._now = now or (lambda: datetime.now(UTC))
        self._next_scan_at: datetime | None = None

    async def run_once(self) -> bool:
        current = self._now()
        if self._next_scan_at is not None and current < self._next_scan_at:
            return False
        self._next_scan_at = current + self._scan_interval
        async with self._session_factory() as session:
            async with session.begin():
                revoked = await revoke_all_expired_ephemeral_connections(
                    session,
                    expires_before=current - TASK_EPHEMERAL_CREDENTIAL_TTL,
                )
        return revoked > 0
