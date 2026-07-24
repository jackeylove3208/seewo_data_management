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


class SimulatedProcessCrash(BaseException):
    pass


class CrashBeforeResponseProvider:
    async def complete_json_once(self, _request: LLMRequest) -> LLMResponse:
        raise SimulatedProcessCrash()


class CrashAfterToolResultProvider:
    def __init__(self) -> None:
        self.calls = 0

    async def complete_json_once(self, _request: LLMRequest) -> LLMResponse:
        self.calls += 1
        if self.calls == 1:
            return LLMResponse(
                output={
                    "result": {
                        "tool_call": {
                            "name": "inspect_configured_source",
                            "arguments": {
                                "resource_id": "source:authoritative:page:1",
                            },
                        }
                    }
                },
                provider="scripted",
                model="scripted-long-context",
                request_id="request-before-tool-crash",
                usage=ModelUsage(input_tokens=10, output_tokens=5),
            )
        raise SimulatedProcessCrash()


class InvalidThenCrashProvider:
    def __init__(self) -> None:
        self.calls = 0

    async def complete_json_once(self, _request: LLMRequest) -> LLMResponse:
        self.calls += 1
        if self.calls == 1:
            return LLMResponse(
                output={
                    "result": {
                        "schema_version": "agent-contract-v1",
                        "recognized": True,
                        "detected_fields": "category",
                        "entity_kinds": ["student"],
                        "safe_problem_codes": [],
                    }
                },
                provider="scripted",
                model="scripted-long-context",
                request_id="request-before-repair-crash",
            )
        raise SimulatedProcessCrash()


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
    invocation_request = GraphSkillInvocation(
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
    result = await runner.run(invocation_request)
    replay = await runner.run(invocation_request)

    assert isinstance(result.output, BaseModel)
    assert replay.output == result.output
    assert replay.invocation_id == result.invocation_id
    assert len(provider.requests) == 2
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
async def test_real_skill_invocation_accepts_flat_json_object_response(session) -> None:
    task, run, state, manifest = await _graph_invocation_fixture(
        session,
        node="inspect_sources",
        action_id="inspect_authority:page-1",
    )
    provider = ScriptedProvider(
        [
            {
                "schema_version": "agent-contract-v1",
                "recognized": True,
                "detected_fields": ["category", "name", "number"],
                "entity_kinds": ["student"],
                "safe_problem_codes": [],
            }
        ]
    )
    operator = OperatorContext(
        operator_id="demo-operator",
        tenant_id=task.tenant_id,
    )
    result = await GraphSkillModelRunner(
        session,
        provider=provider,
        tool_gateway=GraphPhaseToolGateway(session, operator=operator, tools={}),
        operator=operator,
        max_retries=0,
    ).run(
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

    assert result.output.model_dump()["recognized"] is True
    assert len(provider.requests) == 1


@pytest.mark.asyncio
async def test_real_skill_invocation_repairs_invalid_output_shape(session) -> None:
    task, run, state, manifest = await _graph_invocation_fixture(
        session,
        node="inspect_sources",
        action_id="inspect_authority:page-1",
    )
    provider = ScriptedProvider(
        [
            {
                "schema_version": "agent-contract-v1",
                "recognized": True,
                "detected_fields": "category",
                "entity_kinds": ["student"],
                "safe_problem_codes": [],
            },
            {
                "schema_version": "agent-contract-v1",
                "recognized": True,
                "detected_fields": ["category"],
                "entity_kinds": ["student"],
                "safe_problem_codes": [],
            },
        ]
    )
    operator = OperatorContext(
        operator_id="demo-operator",
        tenant_id=task.tenant_id,
    )

    result = await GraphSkillModelRunner(
        session,
        provider=provider,
        tool_gateway=GraphPhaseToolGateway(session, operator=operator, tools={}),
        operator=operator,
        max_retries=1,
    ).run(
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

    assert result.output.model_dump()["detected_fields"] == ("category",)
    assert len(provider.requests) == 2
    assert "detected_fields" in provider.requests[1].messages[-1].content


@pytest.mark.asyncio
async def test_repair_retry_replays_tools_under_the_new_invocation(session) -> None:
    task, run, state, manifest = await _graph_invocation_fixture(
        session,
        node="inspect_sources",
        action_id="inspect_authority:page-1",
    )
    tool_call = {
        "result": {
            "tool_call": {
                "name": "inspect_configured_source",
                "arguments": {
                    "resource_id": "source:authoritative:page:1",
                },
            }
        }
    }
    invalid_result = {
        "result": {
            "schema_version": "agent-contract-v1",
            "recognized": True,
            "detected_fields": "category",
            "entity_kinds": ["student"],
            "safe_problem_codes": [],
        }
    }
    valid_result = {
        "result": {
            "schema_version": "agent-contract-v1",
            "recognized": True,
            "detected_fields": ["category"],
            "entity_kinds": ["student"],
            "safe_problem_codes": [],
        }
    }
    provider = ScriptedProvider(
        [tool_call, invalid_result, tool_call, valid_result]
    )

    async def inspect_source(_context, arguments):
        return {
            "resource_id": arguments["resource_id"],
            "connector_kind": "csv",
            "stable_order": True,
        }

    operator = OperatorContext(
        operator_id="demo-operator",
        tenant_id=task.tenant_id,
    )
    result = await GraphSkillModelRunner(
        session,
        provider=provider,
        tool_gateway=GraphPhaseToolGateway(
            session,
            operator=operator,
            tools={"inspect_configured_source": inspect_source},
        ),
        operator=operator,
        max_retries=1,
    ).run(
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

    assert result.output.model_dump()["detected_fields"] == ("category",)
    assert len(provider.requests) == 4
    second_attempt_messages = provider.requests[2].messages
    assert "validation_errors" in second_attempt_messages[-1].content
    assert all(
        "authorized_tool_result" not in message.content
        for message in second_attempt_messages
    )
    invocations = tuple(
        await session.scalars(
            select(AgentSubAgentInvocationRecord)
            .where(AgentSubAgentInvocationRecord.graph_run_id == state.id)
            .order_by(AgentSubAgentInvocationRecord.attempt)
        )
    )
    assert [item.status for item in invocations] == ["failed", "completed"]
    tool_records = tuple(
        await session.scalars(
            select(AgentToolCallRecord).order_by(
                AgentToolCallRecord.invocation_id,
                AgentToolCallRecord.sequence,
            )
        )
    )
    assert {item.invocation_id for item in tool_records} == {
        invocations[0].id,
        invocations[1].id,
    }
    assert invocations[0].model_provenance["request_ids"] == [
        "request-1",
        "request-2",
    ]
    assert invocations[1].model_provenance["request_ids"] == [
        "request-3",
        "request-4",
    ]
    assert invocations[1].model_provenance["tool_call_count"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid_output",
    [
        {"result": "not-an-object"},
        {"result": {"tool_call": "not-an-object"}},
        {
            "result": {
                "tool_call": {
                    "name": "inspect_configured_source",
                    "arguments": {},
                }
            }
        },
    ],
)
async def test_all_model_shape_failures_receive_repair_feedback(
    session,
    invalid_output,
) -> None:
    task, run, state, manifest = await _graph_invocation_fixture(
        session,
        node="inspect_sources",
        action_id="inspect_authority:page-1",
    )
    provider = ScriptedProvider(
        [
            invalid_output,
            {
                "result": {
                    "schema_version": "agent-contract-v1",
                    "recognized": True,
                    "detected_fields": ["category"],
                    "entity_kinds": ["student"],
                    "safe_problem_codes": [],
                }
            },
        ]
    )
    operator = OperatorContext(
        operator_id="demo-operator",
        tenant_id=task.tenant_id,
    )

    result = await GraphSkillModelRunner(
        session,
        provider=provider,
        tool_gateway=GraphPhaseToolGateway(session, operator=operator, tools={}),
        operator=operator,
        max_retries=1,
    ).run(
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

    assert result.output.model_dump()["recognized"] is True
    assert "validation_errors" in provider.requests[1].messages[-1].content


@pytest.mark.asyncio
async def test_repair_feedback_survives_worker_interruption_between_attempts(
    session,
) -> None:
    task, run, state, manifest = await _graph_invocation_fixture(
        session,
        node="inspect_sources",
        action_id="inspect_authority:page-1",
    )
    operator = OperatorContext(
        operator_id="demo-operator",
        tenant_id=task.tenant_id,
    )
    request = GraphSkillInvocation(
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

    with pytest.raises(SimulatedProcessCrash):
        await GraphSkillModelRunner(
            session,
            provider=InvalidThenCrashProvider(),
            tool_gateway=GraphPhaseToolGateway(session, operator=operator, tools={}),
            operator=operator,
        ).run(request)

    recovery_provider = ScriptedProvider(
        [
            {
                "result": {
                    "schema_version": "agent-contract-v1",
                    "recognized": True,
                    "detected_fields": ["category"],
                    "entity_kinds": ["student"],
                    "safe_problem_codes": [],
                }
            }
        ]
    )
    recovered = await GraphSkillModelRunner(
        session,
        provider=recovery_provider,
        tool_gateway=GraphPhaseToolGateway(session, operator=operator, tools={}),
        operator=operator,
    ).run(request)

    assert recovered.attempt_count == 3
    assert "validation_errors" in recovery_provider.requests[0].messages[-1].content
    attempts = tuple(
        await session.scalars(
            select(AgentSubAgentInvocationRecord)
            .where(AgentSubAgentInvocationRecord.graph_run_id == state.id)
            .order_by(AgentSubAgentInvocationRecord.attempt)
        )
    )
    assert [item.status for item in attempts] == ["failed", "failed", "completed"]
    assert attempts[1].model_provenance["repair_feedback"][0]["path"] == (
        "detected_fields"
    )


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
    await session.refresh(state)
    assert state.retry_count == 3


@pytest.mark.asyncio
async def test_interrupted_invocation_resumes_with_the_next_durable_attempt(
    session,
) -> None:
    task, run, state, manifest = await _graph_invocation_fixture(
        session,
        node="inspect_sources",
        action_id="inspect_authority:page-1",
    )
    operator = OperatorContext(
        operator_id="demo-operator",
        tenant_id=task.tenant_id,
    )
    request = GraphSkillInvocation(
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
    crashed_runner = GraphSkillModelRunner(
        session,
        provider=CrashBeforeResponseProvider(),
        tool_gateway=GraphPhaseToolGateway(session, operator=operator, tools={}),
        operator=operator,
    )

    with pytest.raises(SimulatedProcessCrash):
        await crashed_runner.run(request)

    recovery_provider = ScriptedProvider(
        [
            {
                "result": {
                    "schema_version": "agent-contract-v1",
                    "recognized": True,
                    "detected_fields": ["category", "name", "number"],
                    "entity_kinds": ["student"],
                    "safe_problem_codes": [],
                }
            }
        ]
    )
    recovered = await GraphSkillModelRunner(
        session,
        provider=recovery_provider,
        tool_gateway=GraphPhaseToolGateway(session, operator=operator, tools={}),
        operator=operator,
    ).run(request)

    attempts = tuple(
        await session.scalars(
            select(AgentSubAgentInvocationRecord)
            .where(
                AgentSubAgentInvocationRecord.graph_run_id == state.id,
                AgentSubAgentInvocationRecord.action_id == request.action_id,
            )
            .order_by(AgentSubAgentInvocationRecord.attempt)
        )
    )
    assert recovered.attempt_count == 2
    assert [(item.attempt, item.status) for item in attempts] == [
        (1, "failed"),
        (2, "completed"),
    ]


@pytest.mark.asyncio
async def test_interrupted_invocation_after_tool_result_preserves_audit_and_resumes(
    session,
) -> None:
    task, run, state, manifest = await _graph_invocation_fixture(
        session,
        node="inspect_sources",
        action_id="inspect_authority:page-1",
    )
    operator = OperatorContext(
        operator_id="demo-operator",
        tenant_id=task.tenant_id,
    )
    tool_executions = 0

    async def inspect_source(_context, arguments):
        nonlocal tool_executions
        tool_executions += 1
        return {
            "resource_id": arguments["resource_id"],
            "connector_kind": "csv",
            "stable_order": True,
        }

    request = GraphSkillInvocation(
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
    with pytest.raises(SimulatedProcessCrash):
        await GraphSkillModelRunner(
            session,
            provider=CrashAfterToolResultProvider(),
            tool_gateway=GraphPhaseToolGateway(
                session,
                operator=operator,
                tools={"inspect_configured_source": inspect_source},
            ),
            operator=operator,
        ).run(request)

    recovery_provider = ScriptedProvider(
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
                    "detected_fields": ["category", "name", "number"],
                    "entity_kinds": ["student"],
                    "safe_problem_codes": [],
                }
            },
        ]
    )
    recovered = await GraphSkillModelRunner(
        session,
        provider=recovery_provider,
        tool_gateway=GraphPhaseToolGateway(
            session,
            operator=operator,
            tools={"inspect_configured_source": inspect_source},
        ),
        operator=operator,
    ).run(request)

    tool_calls = tuple(
        await session.scalars(
            select(AgentToolCallRecord).order_by(AgentToolCallRecord.created_at)
        )
    )
    assert recovered.attempt_count == 2
    assert tool_executions == 2
    assert len(tool_calls) == 2
    assert all(item.authorized and item.status == "completed" for item in tool_calls)


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

    manifest_tokens = await tools.prepare_manifest_tokens(
        ("source:authoritative:page:1",)
    )
    payload = await tools.read_connector_page(
        context,
        {"resource_id": "source:authoritative:page:1", "limit": 50},
    )

    assert "13800138000" not in str(payload)
    phone = payload["records"][0]["fields"]["电话"]
    assert str(phone).startswith("STUDENT_PHONE_")
    assert manifest_tokens == (phone,)
