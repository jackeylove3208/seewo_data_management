from uuid import uuid4

from app.ai.providers.base import LLMResponse, ModelUsage


def model_response(output: dict) -> LLMResponse:
    return LLMResponse(
        output={"result": output},
        provider="stub-provider",
        model="stub-model-v1",
        usage=ModelUsage(input_tokens=7, output_tokens=4),
        request_id=str(uuid4()),
    )


def valid_attribute_analysis() -> dict:
    return {
        "cause": "The normalized governed attribute differs",
        "evidence_summary": "The persisted source and target phone values are different",
        "recommended_action": "update",
        "risk": "low",
        "confidence": 0.9,
    }


def tool_call(name: str, arguments: dict | None = None) -> dict:
    return {"tool_call": {"name": name, "arguments": arguments or {}}}
