from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.agent_runtime.repository import AgentRuntimeRepository
from app.agent_runtime.state_machine import AgentRunKind
from app.models.agent_analysis import (
    AgentFindingRecord,
    AgentIdentityPostingRecord,
    AgentModelAttemptRecord,
    AgentModelBatchRecord,
    ImmutableAgentAnalysisRecordError,
)
from app.models.agent_runtime import AgentRunRecord
from app.models.reconciliation import ReconciliationTask
from app.models.snapshots import Snapshot, SourceFile
from app.repositories.agent_analysis import AgentAnalysisRepository, ReplayConflict
from app.schemas.agent_ingestion import (
    AgentContractRecord,
    AgentEntityKind,
    AgentInputMark,
    AgentSourceRole,
)
from app.schemas.agent_reconciliation import AgentFindingPayload, AgentSolutionPayload


async def _run_context(session):
    task = ReconciliationTask(
        tenant_id="school-1",
        scope_id="all",
        snapshot_mode="full",
        entity_types=["student"],
        workflow_version="new-agent-v1",
        idempotency_key=f"agent-analysis-{uuid4()}",
        request_hash="a" * 64,
    )
    session.add(task)
    await session.flush()
    run = await AgentRuntimeRepository(session).create_run(
        task_id=task.id,
        tenant_id=task.tenant_id,
        conversation_id=None,
        kind=AgentRunKind.SYNC,
    )
    snapshots = []
    for role in ("authoritative", "target"):
        source_file = SourceFile(
            task_id=task.id,
            source_role=role,
            original_name=f"{role}.csv",
            storage_name=f"{uuid4()}.csv",
            storage_path=f"/synthetic/{uuid4()}.csv",
            sha256=uuid4().hex * 2,
            size_bytes=1,
        )
        session.add(source_file)
        await session.flush()
        snapshot = Snapshot(
            id=uuid4(),
            task_id=task.id,
            source_file_id=source_file.id,
            source_role=role,
            schema_version="agent-contract-v1",
            mapping_version="agent-contract-v1",
            file_hash=source_file.sha256,
            content_hash=uuid4().hex * 2,
            summary={},
        )
        session.add(snapshot)
        snapshots.append(snapshot)
    await session.flush()
    return task, run, snapshots[0], snapshots[1]


async def _lease_run(session, run: AgentRunRecord, worker_id: str = "worker-1"):
    run.status = "running"
    run.phase = "analyze_batches"
    run.lease_owner = worker_id
    run.lease_token = uuid4()
    run.lease_expires_at = datetime.now(UTC) + timedelta(minutes=1)
    await session.flush()
    return run.lease_token


def _record(task_id, run_id, **changes):
    values = {
        "task_id": task_id,
        "run_id": run_id,
        "snapshot_id": uuid4(),
        "tenant_id": "school-1",
        "source_role": AgentSourceRole.AUTHORITATIVE,
        "stable_locator": "csv:authority:2",
        "stable_order": 2,
        "entity_kind": AgentEntityKind.STUDENT,
        "category": "学生",
        "name": "测试学生",
        "number": "S-001",
        "class_name": "一年级一班",
        "phone": "13800000000",
        "email": "student@example.test",
    }
    values.update(changes)
    return AgentContractRecord.model_validate(values)


@pytest.mark.asyncio
async def test_input_replay_is_idempotent_and_mismatched_locator_fails_closed(session) -> None:
    task, run, authority_snapshot, _ = await _run_context(session)
    repository = AgentAnalysisRepository(session)
    record = _record(task.id, run.id, snapshot_id=authority_snapshot.id)

    first = (await repository.persist_inputs((record,)))[0]
    replay = (await repository.persist_inputs((record,)))[0]

    assert replay.id == first.id
    with pytest.raises(ReplayConflict, match="stable locator"):
        await repository.persist_inputs(
            (_record(task.id, run.id, snapshot_id=authority_snapshot.id, name="另一位学生"),)
        )


@pytest.mark.asyncio
async def test_input_persistence_rejects_cross_tenant_run_and_wrong_snapshot(session) -> None:
    task, run, authority_snapshot, target_snapshot = await _run_context(session)
    repository = AgentAnalysisRepository(session)

    with pytest.raises(ReplayConflict, match="tenant"):
        await repository.persist_inputs(
            (_record(task.id, run.id, snapshot_id=authority_snapshot.id, tenant_id="school-2"),)
        )
    with pytest.raises(ReplayConflict, match="source role"):
        await repository.persist_inputs((_record(task.id, run.id, snapshot_id=target_snapshot.id),))


@pytest.mark.asyncio
async def test_append_only_input_rows_cannot_be_updated(session) -> None:
    task, run, authority_snapshot, _ = await _run_context(session)
    input_record = (
        await AgentAnalysisRepository(session).persist_inputs(
            (_record(task.id, run.id, snapshot_id=authority_snapshot.id),)
        )
    )[0]

    input_record.name = "被修改"
    with pytest.raises(ImmutableAgentAnalysisRecordError):
        await session.flush()


@pytest.mark.asyncio
async def test_identity_postings_allow_duplicate_values_but_forbid_replay_of_same_row(
    session,
) -> None:
    task, run, authority_snapshot, _ = await _run_context(session)
    repository = AgentAnalysisRepository(session)
    first, second = await repository.persist_inputs(
        (
            _record(task.id, run.id, snapshot_id=authority_snapshot.id),
            _record(
                task.id,
                run.id,
                snapshot_id=authority_snapshot.id,
                stable_locator="csv:authority:3",
                stable_order=3,
            ),
        )
    )

    await repository.persist_identity_postings(
        run_id=run.id,
        task_id=task.id,
        tenant_id=task.tenant_id,
        snapshot_id=first.snapshot_id,
        postings=((first.id, "number", "S-001"), (second.id, "number", "S-001")),
    )
    rows = tuple(await session.scalars(select(AgentIdentityPostingRecord)))
    assert len(rows) == 2
    with pytest.raises(IntegrityError):
        session.add(
            AgentIdentityPostingRecord(
                run_id=run.id,
                task_id=task.id,
                tenant_id=task.tenant_id,
                snapshot_id=first.snapshot_id,
                input_record_id=first.id,
                entity_kind="student",
                key_kind="number",
                normalized_value="S-001",
            )
        )
        await session.flush()


@pytest.mark.asyncio
async def test_batch_claim_and_atomic_finalization_enforce_fencing_and_attempt_limits(
    session,
) -> None:
    task, run, authority_snapshot, target_snapshot = await _run_context(session)
    repository = AgentAnalysisRepository(session)
    inputs = await repository.persist_inputs(
        tuple(
            _record(
                task.id,
                run.id,
                snapshot_id=target_snapshot.id,
                source_role=AgentSourceRole.TARGET,
                stable_locator=f"csv:target:{index}",
                stable_order=index,
            )
            for index in (2, 3)
        )
    )
    work_items = []
    for input_record in inputs:
        work_items.append(
            await repository.persist_work_item(
                run_id=run.id,
                task_id=task.id,
                tenant_id=task.tenant_id,
                source_snapshot_id=authority_snapshot.id,
                target_snapshot_id=target_snapshot.id,
                subject_input_id=input_record.id,
                entity_kind="student",
                kind="target_extra",
                idempotency_hash=uuid4().hex * 2,
                evidence_hash=uuid4().hex * 2,
            )
        )
    item_ids = tuple(item.id for item in work_items)
    batch = await repository.create_or_get_batch(
        run_id=run.id,
        task_id=task.id,
        tenant_id=task.tenant_id,
        entity_kind="student",
        input_hash="b" * 64,
        work_item_ids=item_ids,
    )
    assert (
        batch.id
        == (
            await repository.create_or_get_batch(
                run_id=run.id,
                task_id=task.id,
                tenant_id=task.tenant_id,
                entity_kind="student",
                input_hash="b" * 64,
                work_item_ids=item_ids,
            )
        ).id
    )
    run_lease_token = await _lease_run(session, run)
    claim = await repository.claim_batch(
        batch.id,
        worker_id="worker-1",
        run_lease_token=run_lease_token,
        lease_seconds=60,
    )
    assert claim is not None and claim.lease_token is not None
    finding = AgentFindingPayload(
        work_item_id=item_ids[0],
        kind="target_extra",
        category_zh="多余记录",
        analysis_zh="没有权威候选。",
        evidence_refs=("evidence:1",),
        solutions=(
            AgentSolutionPayload(
                operation="delete", risk="high", solution_zh="删除", recommended=True
            ),
        ),
    )
    with pytest.raises(ReplayConflict, match="exactly"):
        await repository.finalize_batch(
            batch_id=batch.id,
            worker_id="worker-1",
            lease_token=claim.lease_token,
            run_lease_token=run_lease_token,
            output_hash="untrusted",
            findings=(finding,),
        )
    with pytest.raises(ReplayConflict, match="duplicate"):
        await repository.finalize_batch(
            batch_id=batch.id,
            worker_id="worker-1",
            lease_token=claim.lease_token,
            run_lease_token=run_lease_token,
            output_hash="untrusted",
            findings=(finding, finding),
        )
    second_finding = finding.model_copy(update={"work_item_id": item_ids[1]})
    await repository.finalize_batch(
        batch_id=batch.id,
        worker_id="worker-1",
        lease_token=claim.lease_token,
        run_lease_token=run_lease_token,
        output_hash="untrusted",
        findings=(finding, second_finding),
    )
    completed = await session.get(AgentModelBatchRecord, batch.id)
    assert completed is not None and completed.status == "completed"
    assert len(tuple(await session.scalars(select(AgentModelAttemptRecord)))) == 1
    assert (
        await repository.claim_batch(
            batch.id,
            worker_id="worker-2",
            run_lease_token=uuid4(),
            lease_seconds=60,
        )
        is None
    )
    changed = finding.model_copy(update={"analysis_zh": "伪造的新分析"})
    with pytest.raises(ReplayConflict, match="completed"):
        await repository.finalize_batch(
            batch_id=batch.id,
            worker_id="worker-1",
            lease_token=claim.lease_token,
            run_lease_token=run_lease_token,
            output_hash=completed.output_hash or "",
            findings=(changed, second_finding),
        )
    saved_findings = tuple(
        await session.scalars(select(AgentFindingRecord).order_by(AgentFindingRecord.work_item_id))
    )
    dependency = await repository.persist_dependency(
        finding_id=saved_findings[0].id,
        depends_on_finding_id=saved_findings[1].id,
    )
    assert dependency.finding_id == saved_findings[0].id
    assert (
        await repository.persist_dependency(
            finding_id=saved_findings[0].id,
            depends_on_finding_id=saved_findings[1].id,
        )
    ).finding_id == saved_findings[0].id
    with pytest.raises(ReplayConflict, match="itself"):
        await repository.persist_dependency(
            finding_id=saved_findings[0].id,
            depends_on_finding_id=saved_findings[0].id,
        )


@pytest.mark.asyncio
async def test_expired_run_lease_cannot_write_attempts_or_findings(session) -> None:
    task, run, authority_snapshot, target_snapshot = await _run_context(session)
    repository = AgentAnalysisRepository(session)
    target = (
        await repository.persist_inputs(
            (
                _record(
                    task.id,
                    run.id,
                    snapshot_id=target_snapshot.id,
                    source_role=AgentSourceRole.TARGET,
                ),
            )
        )
    )[0]
    work_item = await repository.persist_work_item(
        run_id=run.id,
        task_id=task.id,
        tenant_id=task.tenant_id,
        source_snapshot_id=authority_snapshot.id,
        target_snapshot_id=target_snapshot.id,
        subject_input_id=target.id,
        entity_kind="student",
        kind="target_extra",
        idempotency_hash="d" * 64,
        evidence_hash="e" * 64,
    )
    batch = await repository.create_or_get_batch(
        run_id=run.id,
        task_id=task.id,
        tenant_id=task.tenant_id,
        entity_kind="student",
        input_hash="f" * 64,
        work_item_ids=(work_item.id,),
    )
    run_token = await _lease_run(session, run)
    claim = await repository.claim_batch(
        batch.id,
        worker_id="worker-1",
        run_lease_token=run_token,
        lease_seconds=60,
    )
    assert claim is not None and claim.lease_token is not None
    run.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await session.flush()

    with pytest.raises(ReplayConflict, match="run claim"):
        await repository.finalize_batch(
            batch_id=batch.id,
            worker_id="worker-1",
            run_lease_token=run_token,
            lease_token=claim.lease_token,
            output_hash="0" * 64,
            findings=(
                AgentFindingPayload(
                    work_item_id=work_item.id,
                    kind="target_extra",
                    category_zh="多余记录",
                    analysis_zh="没有权威候选。",
                    evidence_refs=("evidence:1",),
                    solutions=(
                        AgentSolutionPayload(
                            operation="delete",
                            risk="high",
                            solution_zh="删除",
                            recommended=True,
                        ),
                    ),
                ),
            ),
        )
    assert tuple(await session.scalars(select(AgentModelAttemptRecord))) == ()


@pytest.mark.asyncio
async def test_replay_safe_supporting_records_validate_context(session) -> None:
    task, run, authority_snapshot, target_snapshot = await _run_context(session)
    repository = AgentAnalysisRepository(session)
    capability = await repository.persist_capability(
        run_id=run.id,
        task_id=task.id,
        tenant_id=task.tenant_id,
        source_role="authoritative",
        connector_kind="csv",
        capabilities={"read": True, "write": False},
    )
    assert (
        capability.id
        == (
            await repository.persist_capability(
                run_id=run.id,
                task_id=task.id,
                tenant_id=task.tenant_id,
                source_role="authoritative",
                connector_kind="csv",
                capabilities={"read": True, "write": False},
            )
        ).id
    )
    authority, target = await repository.persist_inputs(
        (
            _record(task.id, run.id, snapshot_id=authority_snapshot.id),
            _record(
                task.id,
                run.id,
                snapshot_id=target_snapshot.id,
                source_role=AgentSourceRole.TARGET,
                stable_locator="csv:target:2",
            ),
        )
    )
    mark = AgentInputMark(
        input_record_id=target.id,
        reason_code="missing_name",
        affected_fields=("name",),
        inclusion_state="anomaly",
        report_disposition="report",
        safe_evidence={"row_number": 2, "missing_count": 1},
    )
    first_mark = (await repository.persist_marks((mark,)))[0]
    assert first_mark.id == (await repository.persist_marks((mark,)))[0].id
    postings = await repository.persist_identity_postings(
        run_id=run.id,
        task_id=task.id,
        tenant_id=task.tenant_id,
        snapshot_id=authority_snapshot.id,
        postings=((authority.id, "number", "S-001"),),
    )
    work_item = await repository.persist_work_item(
        run_id=run.id,
        task_id=task.id,
        tenant_id=task.tenant_id,
        source_snapshot_id=authority_snapshot.id,
        target_snapshot_id=target_snapshot.id,
        subject_input_id=target.id,
        entity_kind="student",
        kind="resolved",
        idempotency_hash="4" * 64,
        evidence_hash="5" * 64,
    )
    assert (
        work_item.id
        == (
            await repository.persist_work_item(
                run_id=run.id,
                task_id=task.id,
                tenant_id=task.tenant_id,
                source_snapshot_id=authority_snapshot.id,
                target_snapshot_id=target_snapshot.id,
                subject_input_id=target.id,
                entity_kind="student",
                kind="resolved",
                idempotency_hash="4" * 64,
                evidence_hash="5" * 64,
            )
        ).id
    )
    evidence = await repository.persist_identity_evidence(
        work_item_id=work_item.id, posting_id=postings[0].id, evidence_hash="6" * 64
    )
    assert (
        evidence.id
        == (
            await repository.persist_identity_evidence(
                work_item_id=work_item.id,
                posting_id=postings[0].id,
                evidence_hash="6" * 64,
            )
        ).id
    )
    claim = await repository.persist_identity_claim(
        run_id=run.id,
        task_id=task.id,
        source_snapshot_id=authority_snapshot.id,
        target_snapshot_id=target_snapshot.id,
        authority_input_id=authority.id,
        target_input_id=target.id,
        work_item_id=work_item.id,
    )
    assert (
        claim.id
        == (
            await repository.persist_identity_claim(
                run_id=run.id,
                task_id=task.id,
                source_snapshot_id=authority_snapshot.id,
                target_snapshot_id=target_snapshot.id,
                authority_input_id=authority.id,
                target_input_id=target.id,
                work_item_id=work_item.id,
            )
        ).id
    )


@pytest.mark.asyncio
async def test_four_failed_attempts_exhaust_batch_and_phase_change_fences_claim(session) -> None:
    task, run, authority_snapshot, target_snapshot = await _run_context(session)
    repository = AgentAnalysisRepository(session)
    target = (
        await repository.persist_inputs(
            (
                _record(
                    task.id,
                    run.id,
                    snapshot_id=target_snapshot.id,
                    source_role=AgentSourceRole.TARGET,
                ),
            )
        )
    )[0]
    work_item = await repository.persist_work_item(
        run_id=run.id,
        task_id=task.id,
        tenant_id=task.tenant_id,
        source_snapshot_id=authority_snapshot.id,
        target_snapshot_id=target_snapshot.id,
        subject_input_id=target.id,
        entity_kind="student",
        kind="target_extra",
        idempotency_hash="7" * 64,
        evidence_hash="8" * 64,
    )
    batch = await repository.create_or_get_batch(
        run_id=run.id,
        task_id=task.id,
        tenant_id=task.tenant_id,
        entity_kind="student",
        input_hash="9" * 64,
        work_item_ids=(work_item.id,),
    )
    run_token = await _lease_run(session, run)
    for attempt_number in range(1, 5):
        claim = await repository.claim_batch(
            batch.id,
            worker_id="worker-1",
            run_lease_token=run_token,
            lease_seconds=60,
        )
        assert claim is not None and claim.lease_token is not None
        attempt = await repository.append_failed_attempt(
            batch_id=batch.id,
            worker_id="worker-1",
            run_lease_token=run_token,
            lease_token=claim.lease_token,
            provider="stub",
            model="stub-model",
            skill_name="reconcile-entity-batch",
            skill_version="v1",
            prompt_version="v1",
            safe_error_code="invalid_output",
        )
        assert attempt.attempt_number == attempt_number
    assert (
        await repository.claim_batch(
            batch.id,
            worker_id="worker-1",
            run_lease_token=run_token,
            lease_seconds=60,
        )
        is None
    )
    run.phase = "build_identity_work"
    await session.flush()
    assert (
        await repository.claim_batch(
            batch.id,
            worker_id="worker-1",
            run_lease_token=run_token,
            lease_seconds=60,
        )
        is None
    )
