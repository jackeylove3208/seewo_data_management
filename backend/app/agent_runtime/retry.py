class AgentModelRetriesExhausted(RuntimeError):
    code = "agent_model_retries_exhausted"

    def __init__(self, attempt_count: int) -> None:
        self.attempt_count = attempt_count
        super().__init__(f"Agent model processing failed after {attempt_count} attempts")
