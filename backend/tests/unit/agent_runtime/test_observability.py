import json
from uuid import UUID, uuid4

import pytest

from app.agent_runtime import observability as observability_module
from app.agent_runtime.observability import AgentObservability


def test_agent_observability_emits_only_allowlisted_privacy_safe_fields(monkeypatch) -> None:
    observability = AgentObservability()
    task_id = uuid4()
    messages: list[str] = []
    monkeypatch.setattr(
        observability_module.logger,
        "info",
        lambda message: messages.append(message),
    )

    observability.observe(
        "phase_completed",
        task_id=task_id,
        phase="analyze_batches",
        duration_ms=12.5,
        batch_size=50,
        outcome="succeeded",
    )

    payload = json.loads(messages[-1])
    assert payload == {
        "batch_size": 50,
        "duration_ms": 12.5,
        "event": "phase_completed",
        "outcome": "succeeded",
        "phase": "analyze_batches",
        "task_id": str(task_id),
    }
    assert observability.snapshot()["phase_completed"] == 1


def test_agent_observability_rejects_row_content_and_phone_values() -> None:
    observability = AgentObservability()

    with pytest.raises(TypeError):
        observability.observe("phase_failed", row={"phone": "13800000001"})  # type: ignore[call-arg]
    with pytest.raises(ValueError, match="sensitive"):
        observability.observe("connector_failed", error_code="13800000001")


def test_agent_observability_does_not_treat_uuid_digits_as_phone_content() -> None:
    observability = AgentObservability()

    observability.observe(
        "phase_started",
        task_id=UUID("59866fb0-40d4-4ae8-b5f0-13800000001a"),
    )

    assert observability.snapshot()["phase_started"] == 1
