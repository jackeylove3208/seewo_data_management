"""Executable durable workers for fixed and controlled-graph Agent tasks."""

import asyncio
import logging
import signal
from uuid import uuid4

from app.agent_graph.production_executor import ProductionGraphActionExecutor
from app.agent_graph.runtime import ProductionGraphCandidateProvider
from app.agent_graph.worker import AgentGraphWorker
from app.agent_runtime.csv_analysis_worker import CsvAnalysisHandlerFactory
from app.agent_runtime.worker import AgentWorker
from app.ai.graph_supervisor import GraphSupervisorAgent
from app.ai.providers.llm import HttpLLMProvider
from app.ai.worker import WorkerRunner, run_worker_loop
from app.connectors.database_runtime import ConfiguredDatabaseConnectorRuntime
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
    provider = HttpLLMProvider(settings=settings)
    database_connector_runtime = (
        ConfiguredDatabaseConnectorRuntime(settings)
        if settings.agent_graph_sql_execution_enabled
        else None
    )
    factory = CsvAnalysisHandlerFactory(
        database.session_factory,
        tokenization_secret=settings.tokenization_secret.get_secret_value(),
        provider=provider,
        lease_seconds=settings.analysis_worker_lease_seconds,
    )
    fixed_worker = AgentWorker(
        database.session_factory,
        worker_id=worker_id,
        lease_seconds=settings.analysis_worker_lease_seconds,
        handlers=factory.handlers(),
    )
    workers: list[WorkerRunner] = [fixed_worker]
    if settings.agent_graph_enabled:
        graph_worker_id = f"agent-graph-worker-{uuid4()}"
        workers.append(
            AgentGraphWorker(
                database.session_factory,
                worker_id=graph_worker_id,
                lease_seconds=settings.analysis_worker_lease_seconds,
                supervisor=GraphSupervisorAgent(
                    provider,
                    max_retries=settings.model_retry_attempts,
                ),
                candidate_provider=ProductionGraphCandidateProvider(
                    database.session_factory,
                    database_connectors=database_connector_runtime,
                ),
                executor=ProductionGraphActionExecutor(
                    database.session_factory,
                    provider=provider,
                    tokenization_secret=settings.tokenization_secret.get_secret_value(),
                    max_retries=settings.model_retry_attempts,
                    output_root=settings.export_root / "agent-targets",
                    csv_execution_enabled=settings.agent_graph_csv_execution_enabled,
                    settings=settings,
                    database_connectors=database_connector_runtime,
                ),
            )
        )
    logger.info(
        "Agent workers started fixed_id=%s graph_enabled=%s analysis_only=%s csv_execution=%s",
        worker_id,
        settings.agent_graph_enabled,
        settings.new_agent_analysis_only,
        settings.new_agent_csv_execution_enabled,
    )
    try:
        await asyncio.gather(
            *(
                run_worker_loop(
                    worker,
                    stop,
                    poll_seconds=settings.analysis_worker_poll_seconds,
                )
                for worker in workers
            )
        )
    finally:
        if database_connector_runtime is not None:
            await database_connector_runtime.close()
        await database.dispose()
        logger.info("Agent worker stopped id=%s", worker_id)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run())
