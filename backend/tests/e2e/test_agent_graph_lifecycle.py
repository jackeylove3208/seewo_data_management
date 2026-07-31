import json
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import func, select

from app.agent_graph.analysis_tools import GraphAnalysisEvidenceTools
from app.agent_graph.production_executor import ProductionGraphActionExecutor
from app.agent_graph.repository import AgentGraphRepository
from app.agent_graph.runtime import ProductionGraphCandidateProvider
from app.agent_graph.tools import GraphToolContext
from app.agent_graph.worker import AgentGraphWorker, GraphWorkContext
from app.agent_reporting.service import AgentReportingService
from app.agent_runtime.service import AgentSupervisorService
from app.agent_runtime.task_service import AgentTaskService
from app.ai.graph_supervisor import GraphSupervisorAgent
from app.ai.providers.base import (
    LLMRequest,
    LLMResponse,
    ModelProviderError,
    ModelUsage,
)
from app.api.routes.agent import decide_agent_graph_gate
from app.api_connectors.materializer import ApiAuthorityMaterializer
from app.api_connectors.registry import ProviderRegistry
from app.connectors.configured import ConfiguredApiConnector
from app.core.security import OperatorContext
from app.models.agent_analysis import (
    AgentFindingRecord,
    AgentGovernanceOperationRecord,
    AgentGovernancePlanRecord,
    AgentIdentityClaimRecord,
    AgentInputRecord,
    AgentModelBatchRecord,
    AgentWorkItemRecord,
)
from app.models.agent_graph import (
    AgentEvidenceManifestRecord,
    AgentGraphRunRecord,
    AgentHumanGateRecord,
    AgentSubAgentInvocationRecord,
)
from app.models.agent_runtime import (
    AgentRunRecord,
    AgentTaskEventRecord,
    SchoolTaskLockRecord,
)
from app.models.mappings import EntityMapping
from app.models.reconciliation import ReconciliationTask
from app.models.reporting import AgentReportRecord
from app.models.snapshots import (
    CanonicalEntityRecord,
    RawSnapshotRow,
    Snapshot,
    SourceFile,
)
from app.schemas.agent_graph_api import AgentGraphGateDecisionRequest
from tests.fixtures.connector_store import InMemoryConnectorStore
from tests.integration.agent_runtime.test_api_task_binding import (
    MANIFEST,
    CaptureAdapter,
    StaticDatabaseConnectorRuntime,
    _intent,
    _seed_connection,
    _settings,
)


class ScriptedSupervisorProvider:
    async def complete_json_once(self, request: LLMRequest) -> LLMResponse:
        payload = json.loads(request.messages[-1].content)
        payload = payload["untrusted_evidence"]
        actions = payload["action_set"]["allowed_actions"]
        selected = actions[0]
        return _response(
            {
                "action_id": selected["action_id"],
                "reason_zh": "根据当前完整候选集合选择下一项安全工作。",
                "expected_result": selected["required_evidence"][0],
                "observed_blockers": [],
                "risk_notes_zh": [],
                "why_not_other_actions_zh": [
                    {
                        "action_id": action["action_id"],
                        "reason_zh": "本轮先处理另一项同样安全的工作。",
                    }
                    for action in actions[1:]
                ],
                "operator_message_zh": "任务正在按受控状态图推进。",
            },
            model="scripted-supervisor",
        )


class ScriptedSkillProvider:
    async def complete_json_once(self, request: LLMRequest) -> LLMResponse:
        system = request.messages[0].content
        latest = json.loads(request.messages[-1].content)
        if "inspect-external-data-source@1.0.0" in system:
            if "authorized_tool_result" not in latest:
                bounded = json.loads(request.messages[1].content)[
                    "bounded_input_contract"
                ]
                return _response(
                    {
                        "tool_call": {
                            "name": "inspect_configured_source",
                            "arguments": {
                                "resource_id": bounded["connector_ref"],
                            },
                        }
                    }
                )
            return _response(
                {
                    "schema_version": "agent-contract-v1",
                    "recognized": True,
                    "detected_fields": [
                        "category",
                        "name",
                        "number",
                        "class",
                        "phone",
                        "email",
                    ],
                    "entity_kinds": ["student"],
                    "safe_problem_codes": [],
                }
            )
        if "normalize-organization-data-batch@1.0.0" in system:
            if "authorized_tool_result" not in latest:
                bounded = json.loads(request.messages[1].content)[
                    "bounded_input_contract"
                ]
                return _response(
                    {
                        "tool_call": {
                            "name": "read_connector_page",
                            "arguments": {
                                "resource_id": bounded["batch_resource_ids"][0],
                                "limit": 50,
                            },
                        }
                    }
                )
            records = latest["authorized_tool_result"]["records"]
            return _response(
                {
                    "schema_version": "agent-contract-v1",
                    "records": [
                        {
                            "locator": item["locator"],
                            "entity_kind": "student",
                            "category": item["fields"]["category"],
                            "name": item["fields"]["name"],
                            "number": item["fields"]["number"],
                            "class_name": item["fields"]["class"],
                            "phone_token": item["fields"]["phone"],
                            "email": item["fields"]["email"],
                            "invalid": False,
                            "exclusion_codes": [],
                        }
                        for item in records
                    ],
                }
            )
        if "reconcile-entity-batch@1.0.0" in system:
            bounded = json.loads(request.messages[1].content)[
                "bounded_input_contract"
            ]
            work = bounded["work_items"][0]
            evidence_ref = work["candidate_evidence_refs"][0]
            if "authorized_tool_result" not in latest:
                return _response(
                    {
                        "tool_call": {
                            "name": "read_paired_record_evidence",
                            "arguments": {"evidence_ref": evidence_ref},
                        }
                    }
                )
            return _response(
                {
                    "schema_version": "agent-contract-v1",
                    "findings": [
                        {
                            "finding_id": str(uuid4()),
                            "work_item_id": work["work_item_id"],
                            "disposition": "field_difference",
                            "category_zh": "学生班级不一致",
                            "analysis_zh": "权威数据与希沃数据的班级字段不一致。",
                            "proposed_operation": "update",
                            "evidence_refs": [evidence_ref],
                            "solution_zh": "以第三方权威班级字段更新希沃数据。",
                            "risk": "medium",
                            "dependency_finding_ids": [],
                        }
                    ],
                }
            )
        if "generate-governance-solutions@1.0.0" in system:
            bounded = json.loads(request.messages[1].content)[
                "bounded_input_contract"
            ]
            finding = bounded["findings"][0]
            return _response(
                {
                    "schema_version": "agent-contract-v1",
                    "solutions": [
                        {
                            "finding_id": finding["finding_id"],
                            "solution_zh": "以第三方权威班级字段更新希沃数据。",
                            "operation": finding["proposed_operation"],
                            "risk": "medium",
                            "dependency_finding_ids": [],
                        }
                    ],
                }
            )
        if "execute-approved-governance-plan@1.0.0" in system:
            bounded = json.loads(request.messages[1].content)[
                "bounded_input_contract"
            ]
            outcomes = []
            for message in request.messages[2:]:
                if message.role != "user":
                    continue
                value = json.loads(message.content)
                result = value.get("authorized_tool_result")
                if isinstance(result, dict) and "operation_id" in result:
                    outcomes.append(result)
            completed = {item["operation_id"] for item in outcomes}
            remaining = [
                item
                for item in bounded["operation_ids"]
                if item not in completed
            ]
            if remaining:
                return _response(
                    {
                        "tool_call": {
                            "name": "request_operation_execution",
                            "arguments": {
                                "resource_id": f"operation:{remaining[0]}",
                            },
                        }
                    }
                )
            return _response(
                {
                    "schema_version": "agent-contract-v1",
                    "outcomes": outcomes,
                }
            )
        if "assess-agent-rollback-impact@2.1.0" in system:
            if "authorized_tool_result" not in latest:
                bounded = json.loads(request.messages[1].content)[
                    "bounded_input_contract"
                ]
                return _response(
                    {
                        "tool_call": {
                            "name": "read_verified_mutations",
                            "arguments": {
                                "resource_id": f"rollback-facts:{bounded['run_id']}",
                            },
                        }
                    }
                )
            mutations = latest["authorized_tool_result"]["verified_mutations"]
            return _response(
                {
                    "schema_version": "agent-contract-v1",
                    "restorable_operation_ids": [
                        item["id"] for item in mutations
                    ],
                    "already_restored_operation_ids": [],
                    "conflict_operation_ids": [],
                    "impact_zh": "当前版本连续，可按验证成功事实执行补偿。",
                    "requires_confirmation": True,
                }
            )
        if "execute-approved-rollback@2.1.0" in system:
            bounded = json.loads(request.messages[1].content)[
                "bounded_input_contract"
            ]
            outcomes = []
            for message in request.messages[2:]:
                if message.role != "user":
                    continue
                value = json.loads(message.content)
                result = value.get("authorized_tool_result")
                if isinstance(result, dict) and "operation_id" in result:
                    outcomes.append(result)
            completed = {item["operation_id"] for item in outcomes}
            remaining = [
                item
                for item in bounded["operation_ids"]
                if item not in completed
            ]
            if remaining:
                return _response(
                    {
                        "tool_call": {
                            "name": "request_operation_execution",
                            "arguments": {
                                "resource_id": f"operation:{remaining[0]}",
                            },
                        }
                    }
                )
            return _response(
                {
                    "schema_version": "agent-contract-v1",
                    "outcomes": outcomes,
                }
            )
        if "generate-agent-governance-report@1.0.0" in system:
            if "authorized_tool_result" not in latest:
                bounded = json.loads(request.messages[1].content)[
                    "bounded_input_contract"
                ]
                return _response(
                    {
                        "tool_call": {
                            "name": "read_report_fact_manifest",
                            "arguments": {
                                "resource_id": bounded["fact_refs"][0],
                            },
                        }
                    }
                )
            facts = latest["authorized_tool_result"]
            fact_ref = facts["resource_id"]
            rollback_eligible = any(
                item.get("status") == "succeeded"
                for item in facts["facts"].get("mutations", [])
            )
            return _response(
                {
                    "schema_version": "agent-contract-v1",
                    "title_zh": "组织数据同步报告",
                    "summary_zh": "已完成组织数据分析、治理与结果核验。",
                    "input_exception_analyses": [],
                    "fact_refs": [fact_ref],
                    "rollback_evidence_eligible": rollback_eligible,
                }
            )
        raise AssertionError(f"unexpected graph Skill request: {system[:120]}")


class UnavailableModelProvider:
    async def complete_json_once(self, _request: LLMRequest) -> LLMResponse:
        raise ModelProviderError("synthetic model outage")


def _response(
    result: dict[str, object],
    *,
    model: str = "scripted-skill",
) -> LLMResponse:
    return LLMResponse(
        output={"result": result},
        provider="scripted",
        model=model,
        request_id=str(uuid4()),
        usage=ModelUsage(input_tokens=10, output_tokens=5),
    )


@pytest.mark.asyncio
async def test_graph_lifecycle_has_no_legacy_delegation(
    database,
    tmp_path: Path,
) -> None:
    authority = tmp_path / "authority.csv"
    target = tmp_path / "target.csv"
    csv_content = (
        "category,name,number,class,phone,email\n"
        "student,测试学生,S-001,一年级一班,13800138000,student@example.test\n"
    )
    authority.write_text(csv_content, encoding="utf-8")
    target.write_text(
        csv_content.replace("13800138000", "13900139000"),
        encoding="utf-8",
    )
    operator = OperatorContext(
        operator_id="demo-operator",
        tenant_id="school-graph-e2e",
    )

    async with database.session_factory() as session:
        async with session.begin():
            task = ReconciliationTask(
                tenant_id=operator.tenant_id,
                scope_id="all",
                snapshot_mode="full",
                entity_types=["student"],
                status="created",
                stage="ingestion",
                workflow_version="agent-graph-v1",
                task_kind="sync",
                title="受控图全链路测试",
                idempotency_key=str(uuid4()),
                request_hash=uuid4().hex * 2,
            )
            session.add(task)
            await session.flush()
            for role, path in (
                ("authoritative", authority),
                ("target", target),
            ):
                source = SourceFile(
                    task_id=task.id,
                    source_role=role,
                    original_name=path.name,
                    storage_name=f"{uuid4()}.csv",
                    storage_path=str(path),
                    sha256=uuid4().hex * 2,
                    size_bytes=path.stat().st_size,
                    detected_encoding="utf-8",
                )
                session.add(source)
                await session.flush()
                session.add(
                    Snapshot(
                        id=uuid4(),
                        task_id=task.id,
                        source_file_id=source.id,
                        source_role=role,
                        schema_version="agent-contract-v1",
                        mapping_version="agent-contract-v1",
                        file_hash=source.sha256,
                        content_hash=uuid4().hex * 2,
                        state="published",
                        summary={},
                    )
                )
            run = await AgentSupervisorService(
                session,
                operator=operator,
            ).start(task_id=task.id, conversation_id=None)
            task_id = task.id
            run_id = run.id

    candidate_provider = ProductionGraphCandidateProvider(
        database.session_factory
    )
    executor = ProductionGraphActionExecutor(
        database.session_factory,
        provider=ScriptedSkillProvider(),
        tokenization_secret="graph-e2e-tokenization-secret",
        max_retries=0,
        output_root=tmp_path / "outputs",
        csv_execution_enabled=True,
    )
    worker = AgentGraphWorker(
        database.session_factory,
        worker_id="agent-graph-e2e-worker",
        lease_seconds=60,
        supervisor=GraphSupervisorAgent(
            ScriptedSupervisorProvider(),
            max_retries=0,
        ),
        candidate_provider=candidate_provider,
        executor=executor,
    )

    aggregation_replay_checked = False
    for _step in range(30):
        await worker.run_once()
        aggregation_context: GraphWorkContext | None = None
        pending_gate_id: UUID | None = None
        async with database.session_factory() as session:
            async with session.begin():
                current = await session.get(AgentRunRecord, run_id)
                assert current is not None
                graph = await session.scalar(
                    select(AgentGraphRunRecord).where(
                        AgentGraphRunRecord.run_id == current.id
                    )
                )
                assert graph is not None
                if (
                    graph.current_node == "aggregate_risk"
                    and not aggregation_replay_checked
                ):
                    aggregation_context = GraphWorkContext(
                        worker_id="agent-graph-replay-test",
                        run_id=current.id,
                        task_id=task_id,
                        tenant_id=operator.tenant_id,
                        graph_run_id=graph.id,
                        graph_version=graph.graph_version,
                        current_node=graph.current_node,
                        graph_cursor=graph.cursor,
                        attempt_count=current.attempt_count,
                        lease_token=uuid4(),
                    )
                if current.status == "waiting_human":
                    assert graph.current_node == "wait_high_risk_approvals"
                    gate = await session.scalar(
                        select(AgentHumanGateRecord).where(
                            AgentHumanGateRecord.graph_run_id == graph.id,
                            AgentHumanGateRecord.gate_kind
                            == "high_risk_approval",
                            AgentHumanGateRecord.status == "pending",
                        )
                    )
                    assert gate is not None
                    pending_gate_id = gate.id
                if current.status == "completed":
                    break
        if aggregation_context is not None:
            action = next(
                evaluation.action
                for evaluation in (
                    await candidate_provider(aggregation_context)
                ).candidate_evaluations
                if evaluation.passed
            )
            first = await executor(aggregation_context, action)
            replay = await executor(aggregation_context, action)
            assert first.pause_for_human is True
            assert replay.pause_for_human is True
            async with database.session_factory() as session:
                approval_events = int(
                    (
                        await session.scalar(
                            select(func.count())
                            .select_from(AgentTaskEventRecord)
                            .where(
                                AgentTaskEventRecord.run_id == run_id,
                                AgentTaskEventRecord.event_type
                                == "approval_required",
                            )
                        )
                    )
                    or 0
                )
                aggregate_events = int(
                    (
                        await session.scalar(
                            select(func.count())
                            .select_from(AgentTaskEventRecord)
                            .where(
                                AgentTaskEventRecord.run_id == run_id,
                                AgentTaskEventRecord.event_type
                                == "agent_approvals_aggregated",
                            )
                        )
                    )
                    or 0
                )
                gate_count = int(
                    (
                        await session.scalar(
                            select(func.count())
                            .select_from(AgentHumanGateRecord)
                            .where(
                                AgentHumanGateRecord.graph_run_id
                                == aggregation_context.graph_run_id,
                                AgentHumanGateRecord.gate_kind
                                == "high_risk_approval",
                            )
                        )
                    )
                    or 0
                )
                invocation_count = int(
                    (
                        await session.scalar(
                            select(func.count())
                            .select_from(AgentSubAgentInvocationRecord)
                            .where(
                                AgentSubAgentInvocationRecord.graph_run_id
                                == aggregation_context.graph_run_id,
                                AgentSubAgentInvocationRecord.cursor
                                == aggregation_context.graph_cursor,
                                AgentSubAgentInvocationRecord.action_id
                                == action.action_id,
                                AgentSubAgentInvocationRecord.skill_name
                                == "server-guard",
                            )
                        )
                    )
                    or 0
                )
            assert approval_events == 1
            assert aggregate_events == 1
            assert gate_count == 1
            assert invocation_count == 1
            aggregation_replay_checked = True
        if pending_gate_id is not None:
            async with database.session_factory() as session:
                async with session.begin():
                    response = await decide_agent_graph_gate(
                        task_id=task_id,
                        gate_id=pending_gate_id,
                        body=AgentGraphGateDecisionRequest(
                            decision="approve",
                            reason="端到端测试确认学生手机号修改",
                        ),
                        request=SimpleNamespace(
                            app=SimpleNamespace(
                                state=SimpleNamespace(
                                    settings=SimpleNamespace(
                                        new_agent_enabled=True,
                                    )
                                )
                            )
                        ),
                        session=session,
                        operator=operator,
                    )
                    assert response.status == "approved"
    else:
        pytest.fail("controlled Agent graph did not reach terminal state")
    assert aggregation_replay_checked is True

    async with database.session_factory() as session:
        async with session.begin():
            source_report = await session.scalar(
                select(AgentReportRecord).where(
                    AgentReportRecord.task_id == task_id
                )
            )
            assert source_report is not None
            preview = await AgentReportingService(session).create_rollback_task(
                source_task_id=task_id,
                tenant_id=operator.tenant_id,
                requested_by=operator.operator_id,
                target_version_id=UUID(
                    str(source_report.facts["output_target_version_id"])
                ),
            )
            rollback_run = await AgentSupervisorService(
                session,
                operator=operator,
            ).confirm_rollback(task_id=preview.task_id)
            rollback_task_id = preview.task_id
            rollback_run_id = rollback_run.id

    for _step in range(20):
        await worker.run_once()
        pending_rollback_gate_id: UUID | None = None
        async with database.session_factory() as session:
            async with session.begin():
                current = await session.get(AgentRunRecord, rollback_run_id)
                assert current is not None
                if current.status == "waiting_human":
                    graph_id = await session.scalar(
                        select(AgentGraphRunRecord.id).where(
                            AgentGraphRunRecord.run_id == current.id
                        )
                    )
                    assert graph_id is not None
                    gate = await session.scalar(
                        select(AgentHumanGateRecord).where(
                            AgentHumanGateRecord.graph_run_id == graph_id,
                            AgentHumanGateRecord.gate_kind
                            == "rollback_approval",
                            AgentHumanGateRecord.status == "pending",
                        )
                    )
                    assert gate is not None
                    pending_rollback_gate_id = gate.id
                if current.status == "completed":
                    break
        if pending_rollback_gate_id is not None:
            async with database.session_factory() as session:
                async with session.begin():
                    response = await decide_agent_graph_gate(
                        task_id=rollback_task_id,
                        gate_id=pending_rollback_gate_id,
                        body=AgentGraphGateDecisionRequest(
                            decision="approve",
                            reason="端到端测试确认回滚",
                        ),
                        request=SimpleNamespace(
                            app=SimpleNamespace(
                                state=SimpleNamespace(
                                    settings=SimpleNamespace(
                                        new_agent_enabled=True,
                                    )
                                )
                            )
                        ),
                        session=session,
                        operator=operator,
                    )
                    assert response.status == "approved"
    else:
        pytest.fail("independent rollback graph did not reach terminal state")

    async with database.session_factory() as session:
        work_item = await session.scalar(
            select(AgentWorkItemRecord).where(
                AgentWorkItemRecord.run_id == run_id,
                AgentWorkItemRecord.kind == "field_difference",
            )
        )
        assert work_item is not None
        evidence_payload = await GraphAnalysisEvidenceTools(
            session,
            task_id=task_id,
            run_id=run_id,
            tenant_id=operator.tenant_id,
            tokenization_secret="graph-e2e-tokenization-secret",
        ).read_paired_record_evidence(
            GraphToolContext(
                operator_id=operator.operator_id,
                tenant_id=operator.tenant_id,
                task_id=task_id,
                run_id=run_id,
                graph_run_id=uuid4(),
                graph_node="analyze_actionable_batches",
                graph_cursor=0,
                action_id="inspect-paired-evidence",
                evidence_manifest_id=uuid4(),
                invocation_id=uuid4(),
                allowed_tools=frozenset({"read_paired_record_evidence"}),
            ),
            {"evidence_ref": f"paired-record:{work_item.id}"},
        )
        assert evidence_payload["persisted_kind"] == "field_difference"
        assert evidence_payload["field_differences"] == ["phone"]
        assert evidence_payload["allowed_operations"] == ["retain", "update"]
        assert evidence_payload["identity_key_hits"]
        assert evidence_payload["authority_claim"]
        assert evidence_payload["target_stable_order"] == 1
        assert "13800138000" not in str(evidence_payload)

        invocations = tuple(
            await session.scalars(
                select(AgentSubAgentInvocationRecord).where(
                    AgentSubAgentInvocationRecord.graph_run_id
                    == select_graph_run_id(run_id)
                )
            )
        )
        report = await session.scalar(
            select(AgentReportRecord).where(AgentReportRecord.task_id == task_id)
        )
        rollback_invocations = tuple(
            await session.scalars(
                select(AgentSubAgentInvocationRecord).where(
                    AgentSubAgentInvocationRecord.graph_run_id
                    == select_graph_run_id(rollback_run_id)
                )
            )
        )
        rollback_report = await session.scalar(
            select(AgentReportRecord).where(
                AgentReportRecord.task_id == rollback_task_id
            )
        )
        active_lock = await session.scalar(
            select(SchoolTaskLockRecord.id).where(
                SchoolTaskLockRecord.owner_run_id == run_id,
                SchoolTaskLockRecord.active.is_(True),
            )
        )
        normalization_manifest_ids = {
            item.evidence_manifest_id
            for item in invocations
            if item.skill_name == "normalize-organization-data-batch"
        }
        normalization_manifests = tuple(
            await session.scalars(
                select(AgentEvidenceManifestRecord).where(
                    AgentEvidenceManifestRecord.id.in_(
                        normalization_manifest_ids
                    ),
                )
            )
        )
        assert {item.execution_mode for item in invocations} == {
            "skill_model",
            "deterministic_guarded",
        }
        assert {
            item.skill_name
            for item in invocations
            if item.execution_mode == "skill_model"
        } >= {
                "inspect-external-data-source",
                "normalize-organization-data-batch",
                "reconcile-entity-batch",
                "execute-approved-governance-plan",
                "generate-agent-governance-report",
            }
        assert report is not None
        assert report.rollback_eligible is True
        assert rollback_report is not None
        assert rollback_report.id != report.id
        assert {
            item.skill_name
            for item in rollback_invocations
            if item.execution_mode == "skill_model"
        } >= {
            "assess-agent-rollback-impact",
            "execute-approved-rollback",
            "generate-agent-governance-report",
        }
        assert active_lock is None
        assert normalization_manifests
        assert all(
            item.manifest["snapshot_pair"]
            and item.manifest["target_version"]
            and item.manifest["issued_sensitive_tokens"]
            for item in normalization_manifests
        )


def select_graph_run_id(run_id):
    from app.models.agent_graph import AgentGraphRunRecord

    return select(AgentGraphRunRecord.id).where(
        AgentGraphRunRecord.run_id == run_id
    ).scalar_subquery()


@pytest.mark.asyncio
async def test_api_authority_reuses_agent_governance_and_mysql_execution(
    database,
    tmp_path: Path,
) -> None:
    key = Fernet.generate_key()
    settings = _settings(key).model_copy(
        update={"upload_root": tmp_path / "api-uploads"}
    )
    adapter = CaptureAdapter()
    registry = ProviderRegistry()
    registry.register(MANIFEST, adapter)
    operator = OperatorContext(
        operator_id="operator-1",
        tenant_id="school-1",
    )
    async with database.session_factory() as session:
        async with session.begin():
            connection = await _seed_connection(session, fernet_key=key)
            task, run = await AgentTaskService(
                session,
                operator=operator,
                settings=settings,
                provider_registry=registry,
            ).create(
                _intent(connection.id),
                idempotency_key="api-agent-graph-e2e",
            )
            graph = await AgentGraphRepository(session).get_run_state_for_agent_run(
                run.id
            )
            assert graph is not None

    target_store = InMemoryConnectorStore(
        records=[
            {
                "id": "target-teacher-1",
                "row_version": "v1",
                "category": "教师",
                "name": "周明远-old",
                "number": None,
                "class_name": None,
                "phone": "13800000001",
                "email": None,
            }
        ]
    )
    target_runtime = StaticDatabaseConnectorRuntime(
        ConfiguredApiConnector(
            configuration=settings.database_connector_configurations[
                "seewo-mysql"
            ],
            store=target_store,
        )
    )
    candidate_provider = ProductionGraphCandidateProvider(
        database.session_factory,
        database_connectors=target_runtime,
    )
    executor = ProductionGraphActionExecutor(
        database.session_factory,
        provider=ScriptedSkillProvider(),
        tokenization_secret="api-graph-e2e-tokenization-secret",
        max_retries=0,
        output_root=tmp_path / "api-outputs",
        settings=settings,
        database_connectors=target_runtime,
        api_materializer=ApiAuthorityMaterializer(
            settings,
            registry=registry,
            fernet_key=key,
        ),
    )
    worker = AgentGraphWorker(
        database.session_factory,
        worker_id="api-agent-graph-e2e-worker",
        lease_seconds=60,
        supervisor=GraphSupervisorAgent(
            ScriptedSupervisorProvider(),
            max_retries=0,
        ),
        candidate_provider=candidate_provider,
        executor=executor,
    )

    for _step in range(40):
        await worker.run_once()
        pending_gate_id: UUID | None = None
        async with database.session_factory() as session:
            current = await session.get(AgentRunRecord, run.id)
            assert current is not None
            if current.status == "waiting_human":
                pending_gate_id = await session.scalar(
                    select(AgentHumanGateRecord.id).where(
                        AgentHumanGateRecord.graph_run_id == graph.id,
                        AgentHumanGateRecord.status == "pending",
                    )
                )
                assert pending_gate_id is not None
            if current.status == "completed":
                break
        if pending_gate_id is not None:
            async with database.session_factory() as session:
                async with session.begin():
                    await decide_agent_graph_gate(
                        task_id=task.id,
                        gate_id=pending_gate_id,
                        body=AgentGraphGateDecisionRequest(
                            decision="approve",
                            reason="合成测试确认执行",
                        ),
                        request=SimpleNamespace(
                            app=SimpleNamespace(
                                state=SimpleNamespace(
                                    settings=SimpleNamespace(
                                        new_agent_enabled=True,
                                    )
                                )
                            )
                        ),
                        session=session,
                        operator=operator,
                    )
    else:
        async with database.session_factory() as session:
            current = await session.get(AgentRunRecord, run.id)
            current_graph = await session.get(AgentGraphRunRecord, graph.id)
            events = tuple(
                await session.scalars(
                    select(AgentTaskEventRecord)
                    .where(AgentTaskEventRecord.run_id == run.id)
                    .order_by(AgentTaskEventRecord.sequence.desc())
                    .limit(5)
                )
            )
            operations = tuple(
                await session.scalars(
                    select(AgentGovernanceOperationRecord).where(
                        AgentGovernanceOperationRecord.run_id == run.id
                    )
                )
            )
        operation_states = [
            (
                item.status,
                item.target_source_identifier,
                item.before,
                item.after,
                item.error_code,
            )
            for item in operations
        ]
        pytest.fail(
            "API Agent graph did not reach terminal state: "
            f"run={current.status if current else None}, "
            f"node={current_graph.current_node if current_graph else None}, "
            f"events={[(item.event_type, item.payload) for item in events]}, "
            f"operations={operation_states}"
        )

    async with database.session_factory() as session:
        async def count(model, *criteria) -> int:
            return int(
                (
                    await session.scalar(
                        select(func.count()).select_from(model).where(*criteria)
                    )
                )
                or 0
            )

        assert graph.graph_version == "agent-sync-graph-v2"
        assert run.ingestion_contract_version == "source-ingestion-v3"
        assert run.execution_contract_version == "deterministic-execution-v2"
        assert await count(
            AgentInputRecord,
            AgentInputRecord.run_id == run.id,
            AgentInputRecord.source_role == "authoritative",
        ) == 1
        assert await count(
            AgentIdentityClaimRecord,
            AgentIdentityClaimRecord.run_id == run.id,
        ) > 0
        assert await count(
            AgentWorkItemRecord,
            AgentWorkItemRecord.run_id == run.id,
        ) > 0
        assert await count(
            AgentModelBatchRecord,
            AgentModelBatchRecord.run_id == run.id,
        ) > 0
        assert await count(
            AgentFindingRecord,
            AgentFindingRecord.run_id == run.id,
        ) > 0
        assert await count(
            AgentGovernancePlanRecord,
            AgentGovernancePlanRecord.run_id == run.id,
        ) == 1
        assert await count(
            AgentGovernanceOperationRecord,
            AgentGovernanceOperationRecord.run_id == run.id,
            AgentGovernanceOperationRecord.status == "succeeded",
        ) > 0
        assert await count(RawSnapshotRow) == 0
        assert await count(CanonicalEntityRecord) == 0
        assert await count(EntityMapping) == 0
        report = await session.scalar(
            select(AgentReportRecord).where(AgentReportRecord.task_id == task.id)
        )
        assert report is not None

    target = await target_store.record(
        identifier="target-teacher-1",
        record_id_field="id",
    )
    assert target is not None
    assert target["name"] == "周明远"
    assert adapter.calls == 1


@pytest.mark.asyncio
async def test_graph_termination_releases_lock_when_report_model_is_unavailable(
    database,
    tmp_path: Path,
) -> None:
    operator = OperatorContext(
        operator_id="demo-operator",
        tenant_id="school-graph-termination-fallback",
    )
    async with database.session_factory() as session:
        async with session.begin():
            task = ReconciliationTask(
                tenant_id=operator.tenant_id,
                scope_id="all",
                snapshot_mode="full",
                entity_types=["student"],
                status="running",
                stage="analysis",
                workflow_version="agent-graph-v1",
                task_kind="sync",
                title="终止降级报告测试",
                idempotency_key=str(uuid4()),
                request_hash=uuid4().hex * 2,
            )
            session.add(task)
            await session.flush()
            run = await AgentSupervisorService(
                session,
                operator=operator,
            ).start(task_id=task.id, conversation_id=None)
            graph = await session.scalar(
                select(AgentGraphRunRecord).where(
                    AgentGraphRunRecord.run_id == run.id
                )
            )
            assert graph is not None
            graph.current_node = "blocked_model_error"
            graph.cursor = 3
            graph.status = "blocked_model_error"
            run.status = "blocked_model_error"
            await AgentSupervisorService(
                session,
                operator=operator,
            ).terminate(run_id=run.id, reason="operator_confirmed")
            task_id = task.id
            run_id = run.id

    provider = UnavailableModelProvider()
    worker = AgentGraphWorker(
        database.session_factory,
        worker_id="agent-graph-termination-worker",
        lease_seconds=60,
        supervisor=GraphSupervisorAgent(provider, max_retries=0),
        candidate_provider=ProductionGraphCandidateProvider(
            database.session_factory
        ),
        executor=ProductionGraphActionExecutor(
            database.session_factory,
            provider=provider,
            tokenization_secret="graph-termination-tokenization-secret",
            max_retries=0,
            output_root=tmp_path / "outputs",
            csv_execution_enabled=True,
        ),
    )

    for _step in range(5):
        await worker.run_once()
        async with database.session_factory() as session:
            current = await session.get(AgentRunRecord, run_id)
            assert current is not None
            if current.status == "terminated":
                break
    else:
        pytest.fail("termination did not reach terminal state during model outage")

    async with database.session_factory() as session:
        active_lock = await session.scalar(
            select(SchoolTaskLockRecord.id).where(
                SchoolTaskLockRecord.owner_run_id == run_id,
                SchoolTaskLockRecord.active.is_(True),
            )
        )
        report = await session.scalar(
            select(AgentReportRecord).where(
                AgentReportRecord.task_id == task_id
            )
        )
        assert active_lock is None
        assert report is not None
        assert report.terminal_state == "terminated"
        assert report.generated_by == "agent-graph-termination-fallback-v1"
