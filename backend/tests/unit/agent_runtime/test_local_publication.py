from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.agent_runtime.local_publication import _expected_destination_hash
from app.models.executions import TargetVersionRecord


@pytest.mark.asyncio
async def test_rollback_publication_expects_the_live_comparison_baseline() -> None:
    source_task = SimpleNamespace(id=uuid4())
    original_version_id = uuid4()
    comparison_version_id = uuid4()
    rollback_task = SimpleNamespace(
        task_kind="rollback",
        agent_intent={
            "target_version_id": str(original_version_id),
            "comparison_target_version_id": str(comparison_version_id),
        },
    )
    versions = {
        original_version_id: SimpleNamespace(
            id=original_version_id,
            task_id=source_task.id,
            file_sha256="original-published-hash",
        ),
        comparison_version_id: SimpleNamespace(
            id=comparison_version_id,
            task_id=source_task.id,
            file_sha256="live-comparison-hash",
        ),
    }

    class _Session:
        async def get(self, model, identifier):
            assert model is TargetVersionRecord
            return versions.get(identifier)

    expected = await _expected_destination_hash(
        _Session(),  # type: ignore[arg-type]
        task=rollback_task,  # type: ignore[arg-type]
        source_task=source_task,  # type: ignore[arg-type]
    )

    assert expected == "live-comparison-hash"
