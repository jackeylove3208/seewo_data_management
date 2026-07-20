from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from app.models.analyses import AnalysisRecord
from app.models.differences import DifferenceRecord
from app.models.executions import (
    ExecutionBatchRecord,
    ExecutionOperationRecord,
    ImmutableExecutionRecordError,
)
from app.models.proposals import GovernanceProposalRecord
from app.repositories.executions import ExecutionPersistenceConflict, ExecutionRepository
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


@pytest.fixture
async def execution_context(session):
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
        version=2,
    )
    session.add(difference)
    await session.flush()
    analysis = AnalysisRecord(
        difference_id=difference.id,
        difference_version=difference.version,
        analysis_version="analysis-v2",
        status="succeeded",
        output={"cause": "phone differs"},
        attempt_count=1,
        provider="test-provider",
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
        difference_version=difference.version,
        analysis_id=analysis.id,
        analysis_version=analysis.analysis_version,
        proposal_version=3,
        proposal_source=ProposalSource.AI.value,
        operation_type=OperationType.UPDATE.value,
        changes=[{"field": "phone", "before": "100", "after": "200"}],
        rationale="Use the reviewed authoritative value",
        evidence_refs=["field:phone"],
        risk=RiskLevel.MEDIUM.value,
        created_by="proposal-operator",
        status="pending_execution",
    )
    session.add(proposal)
    await session.flush()
    operation = GovernanceOperation(
        proposal=ProposalVersionRef(
            proposal_id=proposal.id,
            proposal_version=proposal.proposal_version,
        ),
        proposal_source=ProposalSource.AI,
        difference_id=difference.id,
        difference_version=difference.version,
        analysis_id=analysis.id,
        analysis_version=analysis.analysis_version,
        operation_type=OperationType.UPDATE,
        entity_type=EntityType.TEACHER,
        target_source_identifier="sw-teacher-1",
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
        target_version="c" * 64,
        proposals=(operation.proposal,),
        operations=(operation,),
        content_hash="d" * 64,
    )
    return pair, proposal, plan


def test_delete_is_not_an_operation(execution_context) -> None:
    _pair, _proposal, plan = execution_context
    payload = plan.operations[0].model_dump()
    payload["operation_type"] = "delete"

    with pytest.raises(ValidationError):
        GovernanceOperation.model_validate(payload)


@pytest.mark.asyncio
async def test_plan_and_batch_preserve_exact_review_and_backend_actor(
    session,
    execution_context,
) -> None:
    pair, proposal, plan = execution_context
    repository = ExecutionRepository(session)
    root = await repository.create_target_version(
        task_id=pair.task_id,
        tenant_id=pair.tenant_id,
        source_snapshot_id=pair.target_snapshot_id,
        parent_version_id=None,
        batch_id=None,
        file_sha256="c" * 64,
        content_hash="e" * 64,
        storage_path="/tmp/uploaded-target.csv",
    )
    saved_plan = await repository.save_plan(plan, created_by="preview-operator")
    batch = await repository.create_batch(
        plan_id=saved_plan.id,
        plan_version=saved_plan.version,
        input_target_version_id=root.id,
        idempotency_key="execute-reviewed-plan",
        confirmed_by="backend-operator",
        high_risk_acknowledged=False,
        preflight_result={"valid": True, "conflicts": []},
    )
    operations = await repository.list_operations(batch.id)

    assert saved_plan.proposal_versions == [
        {"proposal_id": str(proposal.id), "proposal_version": 3}
    ]
    assert saved_plan.created_by == "preview-operator"
    assert batch.confirmed_by == "backend-operator"
    assert batch.input_target_version_id == root.id
    assert len(operations) == 1
    assert operations[0].proposal_id == proposal.id
    assert operations[0].proposal_version == proposal.proposal_version
    assert operations[0].proposal_source == ProposalSource.AI.value
    assert operations[0].before == {"phone": "100"}
    assert operations[0].after == {"phone": "200"}


@pytest.mark.asyncio
async def test_attempts_append_instead_of_overwrite(session, execution_context) -> None:
    pair, _proposal, plan = execution_context
    repository = ExecutionRepository(session)
    root = await repository.create_target_version(
        task_id=pair.task_id,
        tenant_id=pair.tenant_id,
        source_snapshot_id=pair.target_snapshot_id,
        parent_version_id=None,
        batch_id=None,
        file_sha256="c" * 64,
        content_hash="e" * 64,
        storage_path="/tmp/uploaded-target.csv",
    )
    saved_plan = await repository.save_plan(plan, created_by="preview-operator")
    batch = await repository.create_batch(
        plan_id=saved_plan.id,
        plan_version=1,
        input_target_version_id=root.id,
        idempotency_key="append-attempts",
        confirmed_by="backend-operator",
        high_risk_acknowledged=False,
        preflight_result={"valid": True},
    )
    operation = (await repository.list_operations(batch.id))[0]

    first = await repository.append_attempt(
        operation.id,
        status="failed",
        error_code="timeout",
        error_detail={"message": "gateway timed out"},
        retryable=True,
    )
    second = await repository.append_attempt(
        operation.id,
        status="succeeded",
        actual_after={"phone": "200"},
        verification={"valid": True},
    )

    attempts = await repository.list_attempts(operation.id)
    assert [attempt.attempt_number for attempt in attempts] == [1, 2]
    assert first.error_code == "timeout"
    assert first.retryable is True
    assert second.actual_after == {"phone": "200"}


@pytest.mark.asyncio
async def test_plan_and_batch_records_are_immutable(session, execution_context) -> None:
    pair, _proposal, plan = execution_context
    repository = ExecutionRepository(session)
    root = await repository.create_target_version(
        task_id=pair.task_id,
        tenant_id=pair.tenant_id,
        source_snapshot_id=pair.target_snapshot_id,
        parent_version_id=None,
        batch_id=None,
        file_sha256="c" * 64,
        content_hash="e" * 64,
        storage_path="/tmp/uploaded-target.csv",
    )
    saved_plan = await repository.save_plan(plan, created_by="preview-operator")
    batch = await repository.create_batch(
        plan_id=saved_plan.id,
        plan_version=1,
        input_target_version_id=root.id,
        idempotency_key="immutable-batch",
        confirmed_by="backend-operator",
        high_risk_acknowledged=False,
        preflight_result={"valid": True},
    )
    await session.commit()
    batch_id = batch.id

    saved_plan.created_by = "changed"
    with pytest.raises(ImmutableExecutionRecordError):
        await session.flush()
    await session.rollback()

    batch = await session.get(ExecutionBatchRecord, batch_id)
    assert batch is not None
    batch.confirmed_by = "changed"
    with pytest.raises(ImmutableExecutionRecordError):
        await session.flush()


@pytest.mark.asyncio
async def test_batch_and_operations_roll_back_together(session, execution_context) -> None:
    pair, _proposal, plan = execution_context
    duplicated = plan.model_copy(update={"operations": (plan.operations[0], plan.operations[0])})
    repository = ExecutionRepository(session)
    root = await repository.create_target_version(
        task_id=pair.task_id,
        tenant_id=pair.tenant_id,
        source_snapshot_id=pair.target_snapshot_id,
        parent_version_id=None,
        batch_id=None,
        file_sha256="c" * 64,
        content_hash="e" * 64,
        storage_path="/tmp/uploaded-target.csv",
    )
    saved_plan = await repository.save_plan(duplicated, created_by="preview-operator")

    with pytest.raises(ExecutionPersistenceConflict):
        await repository.create_batch(
            plan_id=saved_plan.id,
            plan_version=1,
            input_target_version_id=root.id,
            idempotency_key="atomic-batch",
            confirmed_by="backend-operator",
            high_risk_acknowledged=False,
            preflight_result={"valid": True},
        )

    assert await session.scalar(select(func.count()).select_from(ExecutionBatchRecord)) == 0
    assert await session.scalar(select(func.count()).select_from(ExecutionOperationRecord)) == 0


@pytest.mark.asyncio
async def test_target_versions_and_audit_events_preserve_execution_facts(
    session,
    execution_context,
) -> None:
    pair, _proposal, plan = execution_context
    repository = ExecutionRepository(session)
    root = await repository.create_target_version(
        task_id=pair.task_id,
        tenant_id=pair.tenant_id,
        source_snapshot_id=pair.target_snapshot_id,
        parent_version_id=None,
        batch_id=None,
        file_sha256="c" * 64,
        content_hash="e" * 64,
        storage_path="/tmp/uploaded-target.csv",
    )
    saved_plan = await repository.save_plan(plan, created_by="preview-operator")
    batch = await repository.create_batch(
        plan_id=saved_plan.id,
        plan_version=1,
        input_target_version_id=root.id,
        idempotency_key="target-audit",
        confirmed_by="backend-operator",
        high_risk_acknowledged=False,
        preflight_result={"valid": True},
    )
    child = await repository.create_target_version(
        task_id=pair.task_id,
        tenant_id=pair.tenant_id,
        source_snapshot_id=pair.target_snapshot_id,
        parent_version_id=root.id,
        batch_id=batch.id,
        file_sha256="f" * 64,
        content_hash="1" * 64,
        storage_path="/tmp/derived-target.csv",
    )
    event = await repository.append_audit_event(
        batch_id=batch.id,
        actor_id="backend-operator",
        event_type="target_version_created",
        details={"target_version_id": str(child.id)},
    )

    assert child.parent_version_id == root.id
    assert child.task_id == pair.task_id
    assert child.tenant_id == pair.tenant_id
    assert child.source_snapshot_id == pair.target_snapshot_id
    assert child.batch_id == batch.id
    assert child.file_sha256 == "f" * 64
    assert child.content_hash == "1" * 64
    assert child.storage_path == "/tmp/derived-target.csv"
    assert event.actor_id == "backend-operator"


@pytest.mark.asyncio
async def test_target_version_rejects_snapshot_from_another_task(
    session,
    execution_context,
) -> None:
    pair, _proposal, _plan = execution_context
    other_pair = await create_hierarchy_pair(session)

    with pytest.raises(ExecutionPersistenceConflict, match="snapshot"):
        await ExecutionRepository(session).create_target_version(
            task_id=pair.task_id,
            tenant_id=pair.tenant_id,
            source_snapshot_id=other_pair.target_snapshot_id,
            parent_version_id=None,
            batch_id=None,
            file_sha256="2" * 64,
            content_hash="3" * 64,
            storage_path="/tmp/cross-task-target.csv",
        )


@pytest.mark.asyncio
async def test_idempotency_key_cannot_change_confirmation_facts(
    session,
    execution_context,
) -> None:
    pair, _proposal, plan = execution_context
    repository = ExecutionRepository(session)
    root = await repository.create_target_version(
        task_id=pair.task_id,
        tenant_id=pair.tenant_id,
        source_snapshot_id=pair.target_snapshot_id,
        parent_version_id=None,
        batch_id=None,
        file_sha256="c" * 64,
        content_hash="e" * 64,
        storage_path="/tmp/uploaded-target.csv",
    )
    saved_plan = await repository.save_plan(plan, created_by="preview-operator")
    common = {
        "plan_id": saved_plan.id,
        "plan_version": 1,
        "input_target_version_id": root.id,
        "idempotency_key": "fixed-confirmation",
        "confirmed_by": "backend-operator",
        "preflight_result": {"valid": True},
    }
    await repository.create_batch(high_risk_acknowledged=False, **common)

    with pytest.raises(ExecutionPersistenceConflict, match="idempotency"):
        await repository.create_batch(high_risk_acknowledged=True, **common)
