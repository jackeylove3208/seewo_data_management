import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from app.agent_graph.analysis_tools import GraphAnalysisEvidenceTools
from app.agent_graph.production_executor import ProductionGraphActionExecutor
from app.agent_graph.runtime import ProductionGraphCandidateProvider
from app.agent_graph.tools import GraphToolContext
from app.agent_graph.worker import AgentGraphWorker
from app.agent_reporting.service import AgentReportingService
from app.agent_runtime.service import AgentSupervisorService
from app.ai.graph_supervisor import GraphSupervisorAgent
from app.ai.providers.base import (
    LLMRequest,
    LLMResponse,
    ModelProviderError,
    ModelUsage,
)
from app.core.security import OperatorContext
from app.models.agent_analysis import AgentApprovalGroupRecord, AgentWorkItemRecord
from app.models.agent_graph import (
    AgentEvidenceManifestRecord,
    AgentGraphRunRecord,
    AgentHumanGateRecord,
    AgentSubAgentInvocationRecord,
)
from app.models.agent_runtime import AgentRunRecord, SchoolTaskLockRecord
from app.models.reconciliation import ReconciliationTask
from app.models.reporting import AgentReportRecord
from app.models.snapshots import Snapshot, SourceFile


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
        if "assess-agent-rollback-impact@1.0.0" in system:
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
                    "conflict_operation_ids": [],
                    "impact_zh": "当前版本连续，可按验证成功事实执行补偿。",
                    "requires_confirmation": True,
                }
            )
        if "execute-approved-rollback@1.0.0" in system:
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

    worker = AgentGraphWorker(
        database.session_factory,
        worker_id="agent-graph-e2e-worker",
        lease_seconds=60,
        supervisor=GraphSupervisorAgent(
            ScriptedSupervisorProvider(),
            max_retries=0,
        ),
        candidate_provider=ProductionGraphCandidateProvider(
            database.session_factory
        ),
        executor=ProductionGraphActionExecutor(
            database.session_factory,
            provider=ScriptedSkillProvider(),
            tokenization_secret="graph-e2e-tokenization-secret",
            max_retries=0,
            output_root=tmp_path / "outputs",
            csv_execution_enabled=True,
        ),
    )

    for _step in range(30):
        await worker.run_once()
        async with database.session_factory() as session:
            async with session.begin():
                current = await session.get(AgentRunRecord, run_id)
                assert current is not None
                if current.status == "waiting_human":
                    graph = await session.scalar(
                        select(AgentGraphRunRecord).where(
                            AgentGraphRunRecord.run_id == current.id
                        )
                    )
                    assert graph is not None
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
                    approval_groups = tuple(
                        await session.scalars(
                            select(AgentApprovalGroupRecord).where(
                                AgentApprovalGroupRecord.run_id == current.id,
                            )
                        )
                    )
                    approval_group = next(
                        (
                            group
                            for group in approval_groups
                            if group.finding_ids == gate.member_ids
                        ),
                        None,
                    )
                    assert approval_group is not None
                    gate.status = "approved"
                    gate.decision = {
                        "decision": "approve",
                        "reason": "端到端测试确认学生手机号修改",
                    }
                    gate.decided_by = operator.operator_id
                    gate.decided_at = datetime.now(UTC)
                    approval_group.status = "approved"
                    approval_group.decided_by = operator.operator_id
                    approval_group.decision_reason = "端到端测试确认学生手机号修改"
                    approval_group.decided_at = gate.decided_at
                    approval_group.updated_at = gate.decided_at
                    current.status = "running"
                if current.status == "completed":
                    break
    else:
        pytest.fail("controlled Agent graph did not reach terminal state")

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
                    gate.status = "approved"
                    gate.decision = {
                        "decision": "approve",
                        "reason": "端到端测试确认回滚",
                    }
                    gate.decided_by = operator.operator_id
                    gate.decided_at = datetime.now(UTC)
                    current.status = "running"
                if current.status == "completed":
                    break
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
            "generate-governance-solutions",
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
