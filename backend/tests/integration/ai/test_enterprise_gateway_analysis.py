import json

import httpx
import pytest

from app.ai.agent import GovernanceAgent
from app.ai.analysis_service import AnalysisService
from app.ai.mcp.server import MCPToolGateway
from app.ai.providers.llm import HttpLLMProvider
from app.core.config import Settings
from app.schemas.differences import DifferenceType
from app.schemas.governance import AnalysisStatus, CauseAnalysisV2
from tests.integration.ai.test_analysis_service import seed_difference


@pytest.mark.asyncio
async def test_semantic_analysis_uses_real_http_provider_with_tokenized_evidence(session) -> None:
    difference = await seed_difference(session, DifferenceType.ATTRIBUTE_CONFLICT)
    captured: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        request_text = request.content.decode()
        captured.append(request_text)
        assert "13800000000" not in request_text
        assert "13900000000" not in request_text
        assert "PHONE_" in request_text
        body = json.loads(request.content)
        user_payload = json.loads(body["messages"][-1]["content"])["input_payload"]
        evidence = user_payload["evidence"]
        field = evidence["fields"][0]
        output = {
            "result": {
                "cause": "The governed phone values differ",
                "evidence_summary": "Persisted field evidence supports an update",
                "manual_only": False,
                "options": [
                    {
                        "option_id": "update-authoritative-phone",
                        "operation_type": "update",
                        "target_entity_id": evidence["target_entity_id"],
                        "proposed_changes": [
                            {
                                "field": "phone",
                                "before": field["target_value"],
                                "after": field["source_value"],
                            }
                        ],
                        "rationale": "Use the authoritative phone value",
                        "evidence_refs": ["field:phone"],
                        "risk": "low",
                        "confidence": 0.94,
                        "preconditions": [],
                        "recommended": True,
                    }
                ],
            }
        }
        return httpx.Response(
            200,
            json={
                "id": "enterprise-request-1",
                "choices": [{"message": {"content": json.dumps(output)}}],
                "usage": {"prompt_tokens": 31, "completion_tokens": 17},
            },
            request=request,
        )

    settings = Settings(
        llm_url="https://gateway.example.test/v1/chat/completions",
        llm_api_key="enterprise-key",
        llm_model="enterprise-model",
        tokenization_secret="enterprise-tokenization-secret",
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        agent = GovernanceAgent(
            HttpLLMProvider(settings=settings, client=client),
            MCPToolGateway(session),
            tokenization_secret=settings.tokenization_secret.get_secret_value(),
        )
        result = await AnalysisService(session, agent=agent).analyze(difference.id)

    assert result.status is AnalysisStatus.SUCCEEDED
    assert isinstance(result.output, CauseAnalysisV2)
    assert result.output.options[0].proposed_changes[0].after == "13800000000"
    assert result.provenance.provider == "http"
    assert result.provenance.model == "enterprise-model"
    assert result.provenance.usage.input_tokens == 31
    assert result.provenance.gateway_request_ids == ("enterprise-request-1",)
    assert len(captured) == 1
