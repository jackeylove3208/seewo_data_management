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


def valid_attribute_analysis(
    *,
    target_entity_id: str,
    field: str = "phone",
    before: str = "13900000000",
    after: str = "13800000000",
) -> dict:
    return {
        "cause": "The normalized governed attribute differs",
        "evidence_summary": "The persisted source and target phone values are different",
        "manual_only": False,
        "options": [
            {
                "option_id": "update-authoritative-field",
                "operation_type": "update",
                "target_entity_id": target_entity_id,
                "proposed_changes": [
                    {"field": field, "before": before, "after": after},
                ],
                "rationale": "Use the authoritative field value",
                "evidence_refs": [f"field:{field}"],
                "risk": "low",
                "confidence": 0.9,
                "preconditions": [],
                "recommended": True,
            }
        ],
    }


def tool_call(name: str, arguments: dict | None = None) -> dict:
    return {"tool_call": {"name": name, "arguments": arguments or {}}}
