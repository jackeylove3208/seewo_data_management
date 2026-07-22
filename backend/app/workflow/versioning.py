class LegacyWorkflowOnlyError(ValueError):
    pass


def require_legacy_workflow(workflow_version: str) -> None:
    if workflow_version != "legacy-v1":
        raise LegacyWorkflowOnlyError(
            f"legacy workflow cannot process task version {workflow_version}"
        )
