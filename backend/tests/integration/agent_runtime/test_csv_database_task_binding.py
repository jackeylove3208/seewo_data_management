from dataclasses import replace
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.agent_graph.contracts import AllowedActionV1
from app.agent_graph.production_executor import ProductionGraphActionExecutor
from app.agent_graph.repository import AgentGraphRepository
from app.agent_graph.worker import GraphWorkContext
from app.agent_runtime.repository import AgentRuntimeRepository
from app.agent_runtime.state_machine import AgentPhase
from app.agent_runtime.task_service import AgentTaskService
from app.core.security import OperatorContext
from app.models.agent_analysis import AgentInputRecord
from app.models.api_connectors import AgentSourceBindingRecord
from app.repositories.files import FileRepository
from app.schemas.agent_api import AgentTaskIntent
from app.schemas.ingestion import SourceRole
from tests.settings import build_test_settings


class ModelMustNotRun:
    async def complete_json_once(self, _request):
        raise AssertionError("standard CSV headers must not call a model")


def _settings():
    return build_test_settings(
        new_agent_enabled=True,
        agent_graph_enabled=True,
        source_ingestion_v2_enabled=True,
        source_ingestion_v3_enabled=True,
        agent_graph_sql_execution_enabled=True,
        new_agent_analysis_only=False,
        database_connector_configurations={
            "seewo-data-mysql": {
                "credential_reference": "secret://connectors/seewo-data-mysql",
                "dialect": "mysql",
                "table_name": "data",
                "primary_key": "row_id",
                "version_column": "version",
                "source_role": "target",
                "mapping": {"mode": "llm"},
                "capabilities": {
                    "read": True,
                    "paginated": True,
                    "create": True,
                    "update": True,
                    "delete": True,
                    "optimistic_version": True,
                    "read_after_write": True,
                },
            }
        },
        database_connector_credentials={
            "secret://connectors/seewo-data-mysql": "mysql+asyncmy://hidden"
        },
    )


@pytest.mark.asyncio
async def test_uploaded_csv_authority_binds_to_mysql_target_with_ingestion_v3(
    database,
    tmp_path: Path,
) -> None:
    csv_path = tmp_path / "students.csv"
    csv_path.write_text(
        "category,name,number,class_name,phone,email\n"
        "学生,张三,S001,一班,,\n",
        encoding="utf-8",
    )
    async with database.session_factory() as session:
        async with session.begin():
            source = await FileRepository(session).create(
                source_role=SourceRole.AUTHORITATIVE,
                original_name=csv_path.name,
                storage_name=csv_path.name,
                storage_path=csv_path,
                sha256="a" * 64,
                size_bytes=csv_path.stat().st_size,
                detected_encoding="utf-8",
            )
            await session.flush()
            task, run = await AgentTaskService(
                session,
                operator=OperatorContext(
                    operator_id="operator-1",
                    tenant_id="school-1",
                ),
                settings=_settings(),
            ).create(
                AgentTaskIntent.model_validate(
                    {
                        "title": "CSV 学生同步到希沃数据库",
                        "entity_types": ["student"],
                        "source": {"kind": "csv", "upload_id": str(source.id)},
                        "target": {
                            "kind": "database",
                            "configuration_id": "seewo-data-mysql",
                        },
                    }
                ),
                idempotency_key="csv-database-task",
                conversation_id=None,
            )
            bindings = tuple(
                await session.scalars(
                    select(AgentSourceBindingRecord)
                    .where(AgentSourceBindingRecord.task_id == task.id)
                    .order_by(AgentSourceBindingRecord.role)
                )
            )
            graph = await AgentGraphRepository(session).get_run_state_for_agent_run(
                run.id
            )

    assert run.ingestion_contract_version == "source-ingestion-v3"
    assert [(item.role, item.connector_kind) for item in bindings] == [
        ("authoritative", "csv"),
        ("target", "database"),
    ]
    assert graph is not None
    context = GraphWorkContext(
        worker_id="csv-database-worker",
        run_id=run.id,
        task_id=task.id,
        tenant_id=task.tenant_id,
        graph_run_id=graph.id,
        graph_version=graph.graph_version,
        current_node="inspect_sources",
        graph_cursor=graph.cursor,
        attempt_count=run.attempt_count,
        lease_token=task.id,
        ingestion_contract_version=run.ingestion_contract_version,
        execution_contract_version=run.execution_contract_version,
    )
    action = AllowedActionV1(
        action_id="inspect_authority:source",
        graph_action_kind="inspect_authority",
        kind="run_deterministic",
        resource_ids=("source:authoritative:full",),
        required_evidence=("source:authoritative:inspection",),
        risk="low",
        requires_human=False,
        successor_node="inspect_sources",
    )
    await ProductionGraphActionExecutor(
        database.session_factory,
        provider=ModelMustNotRun(),
        tokenization_secret="test-secret-at-least-16",
        settings=_settings(),
    )(context, action)
    async with database.session_factory() as session:
        inspection = await AgentRuntimeRepository(session).get_checkpoint(
            run.id,
            phase=AgentPhase.INGEST_AND_NORMALIZE,
            checkpoint_key="graph-source-inspection:authoritative",
        )
    assert inspection is not None
    assert inspection.payload["recognized"] is True

    normalize_context = replace(context, current_node="normalize_input_batches")
    mapping_action = AllowedActionV1(
        action_id="resolve_csv_authoritative_mapping",
        graph_action_kind="normalize_next_batch",
        kind="run_deterministic",
        resource_ids=("source:authoritative:mapping",),
        required_evidence=("mapping:csv:authoritative:v3",),
        risk="low",
        requires_human=False,
        successor_node="normalize_input_batches",
    )
    executor = ProductionGraphActionExecutor(
        database.session_factory,
        provider=ModelMustNotRun(),
        tokenization_secret="test-secret-at-least-16",
        settings=_settings(),
    )
    await executor(normalize_context, mapping_action)
    normalize_action = AllowedActionV1(
        action_id="normalize_authoritative_full",
        graph_action_kind="normalize_next_batch",
        kind="run_deterministic",
        resource_ids=("source:authoritative:full",),
        required_evidence=("normalized:authoritative:full",),
        risk="low",
        requires_human=False,
        successor_node="normalize_input_batches",
    )
    await executor(normalize_context, normalize_action)
    async with database.session_factory() as session:
        mapping = await AgentRuntimeRepository(session).get_checkpoint(
            run.id,
            phase=AgentPhase.INGEST_AND_NORMALIZE,
            checkpoint_key="graph-csv-field-mapping-v3:authoritative",
        )
        input_count = await session.scalar(
            select(func.count(AgentInputRecord.id)).where(
                AgentInputRecord.run_id == run.id,
                AgentInputRecord.source_role == "authoritative",
            )
        )
    assert mapping is not None
    assert mapping.payload["resolved"] is True
    assert input_count == 1
