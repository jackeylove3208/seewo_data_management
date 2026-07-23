from uuid import uuid4

import pytest
from pydantic import BaseModel
from sqlalchemy import select

from app.agent_graph.analysis_executors import GraphIngestionAnalysisExecutors
from app.agent_graph.analysis_tools import GraphAnalysisEvidenceTools
from app.agent_graph.evidence import build_evidence_manifest
from app.agent_graph.repository import AgentGraphRepository
from app.agent_graph.tools import GraphPhaseToolGateway, GraphToolContext
from app.agent_runtime.repository import AgentRuntimeRepository
from app.agent_runtime.state_machine import AgentRunKind
from app.ai.graph_subagents import (
    GraphSkillInvocation,
    GraphSkillModelRunner,
    GraphSubAgentFailure,
)
from app.ai.providers.base import (
    LLMRequest,
    LLMResponse,
    ModelProviderError,
    ModelUsage,
)
from app.core.security import OperatorContext
from app.models.agent_graph import (
    AgentSubAgentInvocationRecord,
    AgentToolCallRecord,
)
from app.models.reconciliation import ReconciliationTask
from app.models.snapshots import Snapshot, SourceFile


class ScriptedProvider:
    def __init__(self, outputs: list[dict[str, object] | Exception]) -> None:
        self.outputs = list(outputs)
        self.requests: list[LLMRequest] = []

    async def complete_json_once(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        value = self.outputs.pop(0)
        if isinstance(value, Exception):
            raise value
        return LLMResponse(
            output=value,
            provider="scripted",
            model="scripted-long-context",
            request_id=f"request-{len(self.requests)}",
            usage=ModelUsage(input_tokens=10, output_tokens=5),
        )


async def _graph_invocation_fixture(session, *, node: str, action_id: str):
    task = ReconciliationTask(
        tenant_id="school-real-subagent",
        scope_id="all",
        snapshot_mode="full",
        entity_types=["student"],
        status="running",
        stage="analysis",
        workflow_version="agent-graph-v1",
        idempotency_key=str(uuid4()),
        request_hash=str(uuid4()),
    )
    session.add(task)
    await session.flush()
    run = await AgentRuntimeRepository(session).create_run(
        task_id=task.id,
        tenant_id=task.tenant_id,
        conversation_id=None,
        kind=AgentRunKind.SYNC,
        workflow_version="agent-graph-v1",
    )
    state = await AgentGraphRepository(session).create_run_state(
        run_id=run.id,
        graph_version="agent-sync-graph-v1",
        initial_node=node,
    )
    manifest = build_evidence_manifest(
        tenant_ref=f"tenant-ref:{state.id}",
        task_id=str(task.id),
        run_id=str(run.id),
        graph_node=node,
        action_id=action_id,
        resource_ids=("source:authoritative:page:1",),
        allowed_evidence_refs=("source:authoritative:inspection",),
    )
    manifest_record = await AgentGraphRepository(session).record_manifest(
        graph_run_id=state.id,
        cursor=state.cursor,
        graph_node=node,
        action_id=action_id,
        manifest=manifest.model_dump(mode="json"),
        content_hash=manifest.content_hash,
        record_id=manifest.manifest_id,
    )
    return task, run, state, manifest_record


@pytest.mark.asyncio
async def test_real_skill_invocation_uses_tool_and_records_model_provenance(session) -> None:
    task, run, state, manifest = await _graph_invocation_fixture(
        session,
        node="inspect_sources",
        action_id="inspect_authority:page-1",
    )
    provider = ScriptedProvider(
        [
            {
                "result": {
                    "tool_call": {
                        "name": "inspect_configured_source",
                        "arguments": {
                            "resource_id": "source:authoritative:page:1",
                        },
                    }
                }
            },
            {
                "result": {
                    "schema_version": "agent-contract-v1",
                    "recognized": True,
                    "detected_fields": [
                        "category",
                        "name",
                        "number",
                        "class_name",
                        "phone",
                        "email",
                    ],
                    "entity_kinds": ["student"],
                    "safe_problem_codes": [],
                }
            },
        ]
    )

    async def inspect_source(_context, arguments):
        return {
            "resource_id": arguments["resource_id"],
            "connector_kind": "csv",
            "stable_order": True,
        }

    gateway = GraphPhaseToolGateway(
        session,
        operator=OperatorContext(
            operator_id="demo-operator",
            tenant_id=task.tenant_id,
        ),
        tools={"inspect_configured_source": inspect_source},
    )
    runner = GraphSkillModelRunner(
        session,
        provider=provider,
        tool_gateway=gateway,
        operator=OperatorContext(
            operator_id="demo-operator",
            tenant_id=task.tenant_id,
        ),
    )
    result = await runner.run(
        GraphSkillInvocation(
            task_id=task.id,
            run_id=run.id,
            graph_run_id=state.id,
            graph_node=state.current_node,
            graph_cursor=state.cursor,
            action_id="inspect_authority:page-1",
            evidence_manifest_id=manifest.id,
            skill_name="inspect-external-data-source",
            skill_version="1.0.0",
            input_payload={
                "task_id": str(task.id),
                "run_id": str(run.id),
                "phase": "ingest_and_normalize",
                "evidence_refs": ["source:authoritative:inspection"],
                "connector_kind": "csv",
                "connector_ref": "source:authoritative:page:1",
            },
        )
    )

    assert isinstance(result.output, BaseModel)
    invocation = await session.scalar(
        select(AgentSubAgentInvocationRecord).where(
            AgentSubAgentInvocationRecord.graph_run_id == state.id,
            AgentSubAgentInvocationRecord.status == "completed",
        )
    )
    assert invocation is not None
    assert invocation.execution_mode == "skill_model"
    assert invocation.skill_name == "inspect-external-data-source"
    assert invocation.skill_version == "1.0.0"
    assert invocation.schema_version == "SourceInspectionResult"
    assert invocation.evidence_manifest_id == manifest.id
    assert invocation.model_provenance["request_ids"] == ["request-1", "request-2"]
    assert "inspect-external-data-source@1.0.0" in provider.requests[0].messages[0].content
    tool_call = await session.scalar(select(AgentToolCallRecord))
    assert tool_call is not None
    assert tool_call.authorized is True


@pytest.mark.asyncio
async def test_model_exhaustion_records_four_failures_without_legacy_delegate(session) -> None:
    task, run, state, manifest = await _graph_invocation_fixture(
        session,
        node="inspect_sources",
        action_id="inspect_authority:page-1",
    )
    provider = ScriptedProvider(
        [ModelProviderError("unavailable") for _attempt in range(4)]
    )
    runner = GraphSkillModelRunner(
        session,
        provider=provider,
        tool_gateway=GraphPhaseToolGateway(
            session,
            operator=OperatorContext(
                operator_id="demo-operator",
                tenant_id=task.tenant_id,
            ),
            tools={},
        ),
        operator=OperatorContext(
            operator_id="demo-operator",
            tenant_id=task.tenant_id,
        ),
    )

    with pytest.raises(GraphSubAgentFailure, match="four attempts"):
        await runner.run(
            GraphSkillInvocation(
                task_id=task.id,
                run_id=run.id,
                graph_run_id=state.id,
                graph_node=state.current_node,
                graph_cursor=state.cursor,
                action_id="inspect_authority:page-1",
                evidence_manifest_id=manifest.id,
                skill_name="inspect-external-data-source",
                skill_version="1.0.0",
                input_payload={
                    "task_id": str(task.id),
                    "run_id": str(run.id),
                    "phase": "ingest_and_normalize",
                    "connector_kind": "csv",
                    "connector_ref": "source:authoritative:page:1",
                },
            )
        )

    invocations = tuple(
        await session.scalars(
            select(AgentSubAgentInvocationRecord).order_by(
                AgentSubAgentInvocationRecord.attempt
            )
        )
    )
    assert len(invocations) == 4
    assert {record.execution_mode for record in invocations} == {"skill_model"}
    assert {record.status for record in invocations} == {"failed"}
    assert not any(record.execution_mode == "legacy_delegate" for record in invocations)


@pytest.mark.asyncio
async def test_analysis_action_invokes_reconciliation_and_solution_skills(session) -> None:
    task, run, state, manifest = await _graph_invocation_fixture(
        session,
        node="analyze_actionable_batches",
        action_id="analyze_students:batch-1",
    )
    work_item_id = uuid4()
    finding_id = uuid4()
    provider = ScriptedProvider(
        [
            {
                "result": {
                    "schema_version": "agent-contract-v1",
                    "findings": [
                        {
                            "finding_id": str(finding_id),
                            "work_item_id": str(work_item_id),
                            "disposition": "field_difference",
                            "category_zh": "学生班级不一致",
                            "analysis_zh": "引用了不属于本任务的证据。",
                            "proposed_operation": "update",
                            "evidence_refs": ["paired-record:foreign"],
                        }
                    ],
                }
            },
            {
                "result": {
                    "schema_version": "agent-contract-v1",
                    "findings": [
                        {
                            "finding_id": str(finding_id),
                            "work_item_id": str(work_item_id),
                            "disposition": "field_difference",
                            "category_zh": "学生班级不一致",
                            "analysis_zh": "权威班级与希沃目标班级不一致。",
                            "proposed_operation": "update",
                            "evidence_refs": ["source:authoritative:inspection"],
                        }
                    ],
                }
            },
            {
                "result": {
                    "schema_version": "agent-contract-v1",
                    "solutions": [
                        {
                            "finding_id": str(finding_id),
                            "solution_zh": "按第三方权威班级更新希沃目标记录。",
                            "operation": "update",
                            "risk": "high",
                            "dependency_finding_ids": [],
                        }
                    ],
                }
            },
        ]
    )
    operator = OperatorContext(
        operator_id="demo-operator",
        tenant_id=task.tenant_id,
    )
    runner = GraphSkillModelRunner(
        session,
        provider=provider,
        tool_gateway=GraphPhaseToolGateway(
            session,
            operator=operator,
            tools={},
        ),
        operator=operator,
    )
    executor = GraphIngestionAnalysisExecutors(runner)

    result = await executor.analyze_actionable_batch(
        GraphSkillInvocation(
            task_id=task.id,
            run_id=run.id,
            graph_run_id=state.id,
            graph_node=state.current_node,
            graph_cursor=state.cursor,
            action_id="analyze_students:batch-1",
            evidence_manifest_id=manifest.id,
            skill_name="reconcile-entity-batch",
            skill_version="1.0.0",
            input_payload={
                "task_id": str(task.id),
                "run_id": str(run.id),
                "phase": "analyze_batches",
                "evidence_refs": ["source:authoritative:inspection"],
                "work_items": [
                    {
                        "work_item_id": str(work_item_id),
                        "entity_kind": "student",
                        "target_locator": "csv:2",
                        "candidate_evidence_refs": [
                            "source:authoritative:inspection"
                        ],
                    }
                ],
            },
        ),
        expected_work_item_kinds={work_item_id: "field_difference"},
        allowed_evidence_refs=frozenset({"source:authoritative:inspection"}),
    )

    assert result.payloads[0].solutions[0].recommended is True
    invocations = tuple(
        await session.scalars(
            select(AgentSubAgentInvocationRecord).where(
                AgentSubAgentInvocationRecord.graph_run_id == state.id
            )
        )
    )
    assert {record.skill_name for record in invocations} == {
        "reconcile-entity-batch",
        "generate-governance-solutions",
    }
    assert {record.execution_mode for record in invocations} == {"skill_model"}
    reconciliation_attempts = sorted(
        (
            record.attempt,
            record.status,
        )
        for record in invocations
        if record.skill_name == "reconcile-entity-batch"
    )
    assert reconciliation_attempts == [(1, "failed"), (2, "completed")]


@pytest.mark.asyncio
async def test_connector_page_tokenizes_phone_before_model_boundary(
    session,
    tmp_path,
) -> None:
    task = ReconciliationTask(
        tenant_id="school-phone-boundary",
        scope_id="all",
        snapshot_mode="full",
        entity_types=["student"],
        status="running",
        stage="ingestion",
        workflow_version="agent-graph-v1",
        idempotency_key=str(uuid4()),
        request_hash=str(uuid4()),
    )
    session.add(task)
    await session.flush()
    run = await AgentRuntimeRepository(session).create_run(
        task_id=task.id,
        tenant_id=task.tenant_id,
        conversation_id=None,
        kind=AgentRunKind.SYNC,
        workflow_version="agent-graph-v1",
    )
    csv_path = tmp_path / "authority.csv"
    csv_path.write_text(
        "类别,姓名,编号,班级,电话,邮箱\n"
        "学生,测试学生,S001,一年级一班,13800138000,student@example.test\n",
        encoding="utf-8",
    )
    source = SourceFile(
        task_id=task.id,
        source_role="authoritative",
        original_name="authority.csv",
        storage_name=f"{uuid4()}.csv",
        storage_path=str(csv_path),
        sha256="a" * 64,
        size_bytes=csv_path.stat().st_size,
        detected_encoding="utf-8",
    )
    session.add(source)
    await session.flush()
    session.add(
        Snapshot(
            id=uuid4(),
            task_id=task.id,
            source_file_id=source.id,
            source_role="authoritative",
            schema_version="agent-contract-v1",
            mapping_version="agent-contract-v1",
            file_hash=source.sha256,
            content_hash="b" * 64,
            summary={},
        )
    )
    await session.flush()
    tools = GraphAnalysisEvidenceTools(
        session,
        task_id=task.id,
        run_id=run.id,
        tenant_id=task.tenant_id,
        tokenization_secret="test-tokenization-secret-1234",
    )
    context = GraphToolContext(
        operator_id="demo-operator",
        tenant_id=task.tenant_id,
        task_id=task.id,
        run_id=run.id,
        graph_run_id=uuid4(),
        graph_node="normalize_input_batches",
        graph_cursor=0,
        action_id="normalize:authority:1",
        evidence_manifest_id=uuid4(),
        invocation_id=uuid4(),
        allowed_tools=frozenset({"read_connector_page"}),
    )

    payload = await tools.read_connector_page(
        context,
        {"resource_id": "source:authoritative:page:1", "limit": 50},
    )

    assert "13800138000" not in str(payload)
    phone = payload["records"][0]["fields"]["电话"]
    assert str(phone).startswith("STUDENT_PHONE_")
