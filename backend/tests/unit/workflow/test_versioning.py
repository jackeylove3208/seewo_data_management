import pytest

from app.workflow.versioning import LegacyWorkflowOnlyError, require_legacy_workflow


def test_legacy_workflow_accepts_only_legacy_version() -> None:
    require_legacy_workflow("legacy-v1")

    with pytest.raises(LegacyWorkflowOnlyError, match="new-agent-v1"):
        require_legacy_workflow("new-agent-v1")
