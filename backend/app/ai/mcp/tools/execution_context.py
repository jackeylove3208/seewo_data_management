from typing import Any

from app.schemas.differences import DifferenceItem


def read_execution_context(difference: DifferenceItem) -> dict[str, Any]:
    return {
        "task_id": str(difference.task_id),
        "difference_id": str(difference.id),
        "difference_version": difference.version,
        "source_snapshot_id": str(difference.evidence.source_snapshot_id),
        "target_snapshot_id": str(difference.evidence.target_snapshot_id),
        "proposed_action": difference.proposed_action.value,
        "resolution_status": difference.status.value,
    }
