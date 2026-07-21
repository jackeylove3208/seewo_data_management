from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.models.reconciliation import ReconciliationTask
from app.models.snapshots import CanonicalEntityRecord, Snapshot, SourceFile
from app.repositories.rematching import (
    EntityRematchRepository,
    RematchCandidateDraft,
    RematchWorkItemDraft,
)


async def seed_task(session, *, tenant_id: str = "school-1") -> ReconciliationTask:
    task = ReconciliationTask(
        tenant_id=tenant_id,
        scope_id="all",
        snapshot_mode="full",
        entity_types=["student"],
        idempotency_key=f"rematch-task-{uuid4()}",
        request_hash=uuid4().hex,
    )
    session.add(task)
    await session.flush()
    return task


def draft(*, focal_entity_id=None, candidate_entity_id=None, candidate_hash="candidates-v1"):
    focal_entity_id = focal_entity_id or uuid4()
    candidate_entity_id = candidate_entity_id or uuid4()
    return RematchWorkItemDraft(
        entity_type="student",
        focal_entity_id=focal_entity_id,
        focal_role="authoritative",
        candidate_set_hash=candidate_hash,
        candidates=(
            RematchCandidateDraft(
                candidate_entity_id=candidate_entity_id,
                candidate_role="target",
                rank=1,
                vector_score=0.98,
                representation_version="student-v1",
                evidence={"name": 1.0, "phone": 1.0},
            ),
        ),
    )


async def seed_snapshots(session, task, source_snapshot_id, target_snapshot_id, items) -> None:
    snapshots = {
        "authoritative": (source_snapshot_id, "source"),
        "target": (target_snapshot_id, "target"),
    }
    for role, (snapshot_id, label) in snapshots.items():
        source_file = SourceFile(
            task_id=task.id,
            source_role=role,
            original_name=f"{label}.csv",
            storage_name=f"{uuid4()}.csv",
            storage_path=f"/tmp/{uuid4()}.csv",
            sha256=uuid4().hex * 2,
            size_bytes=1,
        )
        session.add(source_file)
        await session.flush()
        session.add(
            Snapshot(
                id=snapshot_id,
                task_id=task.id,
                source_file_id=source_file.id,
                source_role=role,
                schema_version="v1",
                mapping_version="v1",
                file_hash=uuid4().hex,
                content_hash=uuid4().hex,
                summary={},
            )
        )
    await session.flush()
    row_numbers = {"authoritative": 0, "target": 0}
    seen: set[tuple[str, object]] = set()
    for item in items:
        endpoints = ((item.focal_role, item.focal_entity_id),) + tuple(
            (candidate.candidate_role, candidate.candidate_entity_id)
            for candidate in item.candidates
        )
        for role, entity_id in endpoints:
            if (role, entity_id) in seen:
                continue
            seen.add((role, entity_id))
            if await session.get(CanonicalEntityRecord, entity_id) is not None:
                continue
            row_numbers[role] += 1
            session.add(
                CanonicalEntityRecord(
                    id=entity_id,
                    snapshot_id=snapshots[role][0],
                    entity_type="student",
                    source_id=str(entity_id),
                    raw_row_number=row_numbers[role],
                    canonical_payload={},
                    raw_payload={},
                )
            )
    await session.flush()


async def create_job(session, *, tenant_id="school-1", items=None, key=None):
    task = await seed_task(session, tenant_id=tenant_id)
    repository = EntityRematchRepository(session)
    work_items = tuple(items or (draft(),))
    source_snapshot_id, target_snapshot_id = uuid4(), uuid4()
    await seed_snapshots(session, task, source_snapshot_id, target_snapshot_id, work_items)
    return await repository.create_or_get(
        task_id=task.id,
        tenant_id=tenant_id,
        requested_by="operator-1",
        source_snapshot_id=source_snapshot_id,
        target_snapshot_id=target_snapshot_id,
        idempotency_key=key or f"rematch-{uuid4()}",
        policy_version="rematch-v1",
        items=work_items,
    )


@pytest.mark.asyncio
async def test_create_is_idempotent_and_persists_bounded_candidate_edges(session) -> None:
    task = await seed_task(session)
    repository = EntityRematchRepository(session)
    item_draft = draft()
    source_snapshot_id, target_snapshot_id = uuid4(), uuid4()
    await seed_snapshots(session, task, source_snapshot_id, target_snapshot_id, (item_draft,))
    kwargs = {
        "task_id": task.id,
        "tenant_id": "school-1",
        "requested_by": "operator-1",
        "source_snapshot_id": source_snapshot_id,
        "target_snapshot_id": target_snapshot_id,
        "idempotency_key": "same-request",
        "policy_version": "rematch-v1",
        "items": (item_draft,),
    }

    first = await repository.create_or_get(**kwargs)
    second = await repository.create_or_get(**kwargs)
    work_items = await repository.work_items(first.id, "school-1")
    edges = await repository.candidate_edges(work_items[0].id, "school-1")

    assert second.id == first.id
    assert first.total == 1
    assert len(work_items) == 1
    assert len(edges) == 1
    assert edges[0].candidate_entity_id == item_draft.candidates[0].candidate_entity_id


@pytest.mark.asyncio
async def test_current_lookup_and_get_are_tenant_scoped(session) -> None:
    job = await create_job(session)
    repository = EntityRematchRepository(session)

    assert await repository.get_for_tenant(job.id, "other-school") is None
    assert await repository.current_for_task(job.task_id, "other-school") is None
    assert (await repository.current_for_task(job.task_id, "school-1")).id == job.id


@pytest.mark.asyncio
async def test_claim_heartbeat_retry_backoff_and_expired_lease_recovery(session) -> None:
    job = await create_job(session)
    repository = EntityRematchRepository(session)
    now = datetime.now(UTC)

    claimed = await repository.claim_next(
        job.id,
        "school-1",
        worker_id="worker-1",
        lease_seconds=30,
        now=now,
    )
    assert claimed is not None
    assert claimed.attempt_count == 1
    assert await repository.heartbeat(
        claimed.id,
        "school-1",
        worker_id="worker-1",
        lease_seconds=60,
        now=now + timedelta(seconds=1),
    )

    await repository.schedule_retry(
        claimed.id,
        "school-1",
        worker_id="worker-1",
        base_delay_seconds=10,
        failure_code="gateway_unavailable",
        now=now + timedelta(seconds=2),
    )
    assert claimed.status == "retry_wait"
    assert claimed.available_at == now + timedelta(seconds=12)

    reclaimed = await repository.claim_next(
        job.id,
        "school-1",
        worker_id="worker-2",
        lease_seconds=-1,
        now=now + timedelta(seconds=13),
    )
    assert reclaimed is not None
    assert reclaimed.attempt_count == 2
    recovered = await repository.recover_expired_leases("school-1", now=now + timedelta(seconds=14))
    assert recovered == 1
    assert reclaimed.status == "queued"
    assert reclaimed.lease_owner is None


@pytest.mark.asyncio
async def test_wrong_tenant_or_lease_owner_cannot_change_item(session) -> None:
    job = await create_job(session)
    repository = EntityRematchRepository(session)
    item = await repository.claim_next(job.id, "school-1", worker_id="worker-1", lease_seconds=60)
    assert item is not None

    assert not await repository.heartbeat(
        item.id, "other-school", worker_id="worker-1", lease_seconds=60
    )
    with pytest.raises(ValueError, match="lease"):
        await repository.complete_item(
            item.id,
            "school-1",
            worker_id="worker-2",
            outcome_status="ai_recovered",
            outcome={"decision": "accept_candidate"},
        )


@pytest.mark.asyncio
async def test_completion_and_counter_reconciliation_are_idempotent(session) -> None:
    job = await create_job(session)
    repository = EntityRematchRepository(session)
    item = await repository.claim_next(job.id, "school-1", worker_id="worker-1", lease_seconds=60)
    assert item is not None
    outcome = {"decision": "manual_review", "reason": "证据冲突，需要人工确认"}

    await repository.complete_item(
        item.id,
        "school-1",
        worker_id="worker-1",
        outcome_status="manual_review",
        outcome=outcome,
    )
    reused = await repository.complete_item(
        item.id,
        "school-1",
        worker_id="worker-1",
        outcome_status="manual_review",
        outcome=outcome,
    )
    reconciled = await repository.reconcile_counters(job.id, "school-1")

    assert reused is item
    assert reconciled is not None
    assert reconciled.processed == 1
    assert reconciled.manual_review == 1
    assert reconciled.status == "completed"


@pytest.mark.asyncio
async def test_terminal_outcome_is_reused_without_mutating_previous_item(session) -> None:
    focal_id = uuid4()
    first_job = await create_job(session, items=(draft(focal_entity_id=focal_id),))
    repository = EntityRematchRepository(session)
    first_item = await repository.claim_next(
        first_job.id, "school-1", worker_id="worker-1", lease_seconds=60
    )
    assert first_item is not None
    outcome = {"decision": "no_match", "reason": "候选均不匹配"}
    await repository.complete_item(
        first_item.id,
        "school-1",
        worker_id="worker-1",
        outcome_status="no_match",
        outcome=outcome,
    )

    second_job = await create_job(session, items=(draft(focal_entity_id=focal_id),))
    second_item = (await repository.work_items(second_job.id, "school-1"))[0]
    reused = await repository.reuse_outcome(second_item.id, "school-1")

    assert reused is True
    assert second_item.outcome == outcome
    assert second_item.reused_from_item_id == first_item.id
    assert first_item.reused_from_item_id is None


@pytest.mark.asyncio
async def test_cancel_is_tenant_scoped_and_releases_pending_work(session) -> None:
    job = await create_job(session)
    repository = EntityRematchRepository(session)

    assert await repository.cancel(job.id, "other-school") is None
    canceled = await repository.cancel(job.id, "school-1")
    items = await repository.work_items(job.id, "school-1")

    assert canceled is not None
    assert canceled.status == "canceled"
    assert items[0].status == "canceled"
