from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.models.analyses import AnalysisRecord
from app.models.differences import DifferenceRecord
from app.models.proposals import GovernanceProposalRecord
from app.repositories.executions import ExecutionRepository
from app.repositories.reporting import ReportingRepository
from app.schemas.canonical_entities import EntityType
from app.schemas.executions import (
    GovernanceOperation,
    GovernancePlan,
    OperationType,
    ProposalSource,
    ProposalVersionRef,
)
from app.schemas.governance import RiskLevel
from tests.fixtures.organization_factory import create_hierarchy_pair


async def _execution(session):
    pair = await create_hierarchy_pair(session)
    difference = DifferenceRecord(
        task_id=pair.task_id,
        tenant_id=pair.tenant_id,
        source_snapshot_id=pair.source_snapshot_id,
        target_snapshot_id=pair.target_snapshot_id,
        entity_type=EntityType.TEACHER.value,
        difference_type="attribute_conflict",
        proposed_action=OperationType.UPDATE.value,
        evidence={"fields": [{"field": "phone"}]},
        comparison_rule_version="comparison-v1",
        evidence_hash=uuid4().hex,
        version=1,
    )
    session.add(difference)
    await session.flush()
    analysis = AnalysisRecord(
        difference_id=difference.id,
        difference_version=1,
        analysis_version="analysis-v2",
        status="succeeded",
        output={"cause": "phone differs"},
        attempt_count=1,
        provider="test",
        model="test-model",
        skill_name="analyze-data-difference",
        skill_version="1.0.0",
        prompt_version="analysis-prompt-v2",
        tool_trace_ids=[],
        gateway_request_ids=[],
        usage={},
        generated_at=datetime.now(UTC),
    )
    session.add(analysis)
    await session.flush()
    proposal = GovernanceProposalRecord(
        task_id=pair.task_id,
        tenant_id=pair.tenant_id,
        difference_id=difference.id,
        difference_version=1,
        analysis_id=analysis.id,
        analysis_version=analysis.analysis_version,
        proposal_version=1,
        proposal_source="ai",
        operation_type="update",
        changes=[{"field": "phone", "before": "100", "after": "200"}],
        rationale="Use the reviewed value",
        evidence_refs=["field:phone"],
        risk="medium",
        created_by="proposal-operator",
        status="pending_execution",
    )
    session.add(proposal)
    await session.flush()
    operation = GovernanceOperation(
        proposal=ProposalVersionRef(proposal_id=proposal.id, proposal_version=1),
        proposal_source=ProposalSource.AI,
        difference_id=difference.id,
        difference_version=1,
        analysis_id=analysis.id,
        analysis_version=analysis.analysis_version,
        operation_type=OperationType.UPDATE,
        entity_type=EntityType.TEACHER,
        target_source_identifier="teacher-1",
        before={"phone": "100"},
        after={"phone": "200"},
        changed_fields=frozenset({"phone"}),
        reversible=True,
        risk=RiskLevel.MEDIUM,
    )
    plan = GovernancePlan(
        id=uuid4(),
        task_id=pair.task_id,
        source_snapshot_id=pair.source_snapshot_id,
        target_snapshot_id=pair.target_snapshot_id,
        target_version="a" * 64,
        proposals=(operation.proposal,),
        operations=(operation,),
        content_hash="b" * 64,
    )
    executions = ExecutionRepository(session)
    root = await executions.create_target_version(
        task_id=pair.task_id,
        tenant_id=pair.tenant_id,
        source_snapshot_id=pair.target_snapshot_id,
        parent_version_id=None,
        batch_id=None,
        file_sha256="c" * 64,
        content_hash="d" * 64,
        storage_path=f"/tmp/{uuid4()}.csv",
    )
    saved = await executions.save_plan(plan, created_by="operator-1")
    batch = await executions.create_batch(
        plan_id=saved.id,
        plan_version=1,
        input_target_version_id=root.id,
        idempotency_key=f"batch-{uuid4()}",
        confirmed_by="operator-1",
        high_risk_acknowledged=False,
        preflight_result={"valid": True},
    )
    output = await executions.create_target_version(
        task_id=pair.task_id,
        tenant_id=pair.tenant_id,
        source_snapshot_id=pair.target_snapshot_id,
        parent_version_id=root.id,
        batch_id=batch.id,
        file_sha256="e" * 64,
        content_hash="f" * 64,
        storage_path=f"/tmp/{uuid4()}.csv",
    )
    return pair, batch, root, output


@pytest.mark.asyncio
async def test_report_versions_append_and_idempotency_reuses_same_job(session) -> None:
    pair, batch, _root, _output = await _execution(session)
    repository = ReportingRepository(session)

    first = await repository.start_report(
        execution_id=batch.id,
        tenant_id=pair.tenant_id,
        idempotency_key="report-1",
        requested_by="operator-1",
        facts={"execution_id": str(batch.id)},
        facts_hash="1" * 64,
    )
    same = await repository.start_report(
        execution_id=batch.id,
        tenant_id=pair.tenant_id,
        idempotency_key="report-1",
        requested_by="operator-1",
        facts={"execution_id": str(batch.id)},
        facts_hash="1" * 64,
    )
    second = await repository.start_report(
        execution_id=batch.id,
        tenant_id=pair.tenant_id,
        idempotency_key="report-2",
        requested_by="operator-1",
        facts={"execution_id": str(batch.id)},
        facts_hash="1" * 64,
    )

    assert same.id == first.id
    assert (first.version, second.version) == (1, 2)


@pytest.mark.asyncio
async def test_restore_request_links_versions_and_compensation_append_only(session) -> None:
    pair, batch, root, output = await _execution(session)
    repository = ReportingRepository(session)

    restore = await repository.create_restore_request(
        task_id=pair.task_id,
        tenant_id=pair.tenant_id,
        source_version_id=output.id,
        semantic_source_version_id=output.id,
        target_version_id=root.id,
        preview_hash="2" * 64,
        deterministic_plan={"operation_ids": []},
        covered_execution_ids=(batch.id,),
        requested_by="operator-1",
    )
    await repository.link_restore_execution(
        restore_request_id=restore.id,
        compensation_plan_id=batch.plan_id,
        compensation_batch_id=batch.id,
    )

    repeated = await repository.link_restore_execution(
        restore_request_id=restore.id,
        compensation_plan_id=batch.plan_id,
        compensation_batch_id=batch.id,
    )
    assert repeated.restore_request_id == restore.id
