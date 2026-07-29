from app.ai.agent_analysis import AgentModelOutputError
from app.ai.agent_durable_analysis import _is_retryable_model_failure
from app.ai.providers.base import TransientModelError


def test_only_provider_and_validated_output_failures_are_retryable() -> None:
    assert _is_retryable_model_failure(TransientModelError("temporary")) is True
    assert _is_retryable_model_failure(AgentModelOutputError("invalid output")) is True
    assert _is_retryable_model_failure(FileNotFoundError("skill missing")) is False
    assert _is_retryable_model_failure(RuntimeError("database unavailable")) is False
