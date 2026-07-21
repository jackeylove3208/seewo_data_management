from uuid import uuid4

import pytest

from app.models.reconciliation import ReconciliationTask
from app.repositories.quality import MatchingQualityRepository


@pytest.mark.asyncio
async def test_save_persists_matching_quality_updated_timestamp(session) -> None:
    task = ReconciliationTask(
        tenant_id="school-1",
        scope_id="all",
        snapshot_mode="full",
        entity_types=["teacher"],
        status="ready",
        stage="matching",
        idempotency_key=str(uuid4()),
        request_hash="a" * 64,
    )
    session.add(task)
    await session.flush()

    record = await MatchingQualityRepository(session).save(
        task_id=task.id,
        tenant_id=task.tenant_id,
        policy_version="matching-quality-v1",
        mapping_versions=["mapping-v1"],
        result={"passed": True},
    )

    assert record.updated_at is not None
