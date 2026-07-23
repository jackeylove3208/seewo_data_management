"""Executable durable worker for ``new-agent-v1`` tasks."""

import asyncio
import logging
import signal
from uuid import uuid4

from app.agent_runtime.csv_analysis_worker import CsvAnalysisHandlerFactory
from app.agent_runtime.worker import AgentWorker
from app.ai.worker import run_worker_loop
from app.core.config import get_settings
from app.core.database import Database

logger = logging.getLogger(__name__)


async def run() -> None:
    settings = get_settings()
    try:
        settings.validate_agent_worker_configuration()
    except ValueError as error:
        raise RuntimeError(str(error)) from error
    assert settings.tokenization_secret is not None
    database = Database(settings.database_url)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signal_name in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signal_name, stop.set)
    worker_id = f"agent-worker-{uuid4()}"
    factory = CsvAnalysisHandlerFactory(
        database.session_factory,
        tokenization_secret=settings.tokenization_secret.get_secret_value(),
        lease_seconds=settings.analysis_worker_lease_seconds,
    )
    worker = AgentWorker(
        database.session_factory,
        worker_id=worker_id,
        lease_seconds=settings.analysis_worker_lease_seconds,
        handlers=factory.handlers(),
    )
    logger.info(
        "Agent worker started id=%s analysis_only=%s csv_execution=%s",
        worker_id,
        settings.new_agent_analysis_only,
        settings.new_agent_csv_execution_enabled,
    )
    try:
        await run_worker_loop(
            worker,
            stop,
            poll_seconds=settings.analysis_worker_poll_seconds,
        )
    finally:
        await database.dispose()
        logger.info("Agent worker stopped id=%s", worker_id)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run())
