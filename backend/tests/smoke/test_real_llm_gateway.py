import os

import pytest

from app.ai.providers.base import LLMRequest, Message
from app.ai.providers.llm import HttpLLMProvider
from app.core.config import Settings


@pytest.mark.asyncio
@pytest.mark.skipif(
    os.getenv("RUN_REAL_LLM_TEST") != "1",
    reason="set RUN_REAL_LLM_TEST=1 to call the configured enterprise gateway",
)
async def test_configured_enterprise_gateway_returns_structured_json() -> None:
    settings = Settings()
    assert settings.model_gateway_configured

    response = await HttpLLMProvider(settings=settings).complete_json(
        LLMRequest(
            messages=(
                Message(
                    role="user",
                    content='Return {"status":"ok"} as structured JSON.',
                ),
            ),
            response_schema={
                "type": "object",
                "additionalProperties": False,
                "properties": {"status": {"type": "string", "const": "ok"}},
                "required": ["status"],
            },
        )
    )

    assert response.output == {"status": "ok"}
    assert response.request_id
