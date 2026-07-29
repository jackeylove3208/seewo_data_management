from app.agent_runtime.retry import AgentModelRetriesExhausted


def test_model_retry_exhaustion_exposes_only_a_stable_safe_error() -> None:
    error = AgentModelRetriesExhausted(4)

    assert error.code == "agent_model_retries_exhausted"
    assert error.attempt_count == 4
    assert str(error) == "Agent model processing failed after 4 attempts"
