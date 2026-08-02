from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.schemas.analysis_jobs import (
    AnalysisJobProgress,
    AnalysisJobStatus,
    AnalysisWorkItemStatus,
)


def test_analysis_job_progress_counts_are_consistent() -> None:
    progress = AnalysisJobProgress(
        job_id=uuid4(),
        task_id=uuid4(),
        status=AnalysisJobStatus.RUNNING,
        total=5,
        completed=3,
        succeeded=1,
        manual_required=1,
        needs_information=1,
        manual_only=0,
        failed=1,
        updated_at=datetime.now(UTC),
    )

    assert progress.completed == 3


def test_analysis_job_progress_rejects_completed_count_drift() -> None:
    with pytest.raises(ValidationError, match="completed count"):
        AnalysisJobProgress(
            job_id=uuid4(),
            task_id=uuid4(),
            status=AnalysisJobStatus.RUNNING,
            total=5,
            completed=2,
            succeeded=1,
            manual_required=1,
            needs_information=1,
            manual_only=0,
            failed=1,
            updated_at=datetime.now(UTC),
        )


def test_work_item_status_has_recoverable_and_terminal_values() -> None:
    assert AnalysisWorkItemStatus.RETRY_WAIT.value == "retry_wait"
    assert AnalysisWorkItemStatus.MANUAL_REQUIRED.value == "manual_required"


def test_analysis_job_progress_requires_manual_subtotals_to_match() -> None:
    with pytest.raises(ValidationError, match="manual-required count"):
        AnalysisJobProgress(
            job_id=uuid4(),
            task_id=uuid4(),
            status=AnalysisJobStatus.RUNNING,
            total=2,
            completed=1,
            succeeded=0,
            manual_required=1,
            needs_information=0,
            manual_only=0,
            failed=0,
            updated_at=datetime.now(UTC),
        )
