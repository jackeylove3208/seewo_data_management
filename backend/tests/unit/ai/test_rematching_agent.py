from uuid import uuid4

import pytest

from app.ai.providers.base import LLMResponse, ModelUsage
from app.ai.rematching_agent import RematchingAgent
from app.schemas.rematching import CandidateEdge, CandidateRole, ManualReviewDecision


class ModelStub:
    requires_tokenization = True

    def __init__(self, output):
        self.output = output
        self.requests = []

    async def complete_json(self, request):
        self.requests.append(request)
        return LLMResponse(
            output=self.output,
            provider="test-provider",
            model="rematch-model",
            usage=ModelUsage(input_tokens=4, output_tokens=3),
        )


def edge(candidate_id=None):
    return CandidateEdge(
        focal_entity_id=FOCAL_ID,
        focal_role=CandidateRole.AUTHORITATIVE,
        candidate_entity_id=candidate_id or CANDIDATE_ID,
        candidate_role=CandidateRole.TARGET,
        rank=1,
        vector_score=0.98,
        representation_version="student-v1",
        evidence=(
            {
                "field": "name",
                "source_value": "张三",
                "target_value": "张三",
                "matched": True,
            },
            {
                "field": "phone",
                "source_value": "13800000000",
                "target_value": "13800000000",
                "matched": True,
            },
        ),
    )


FOCAL_ID = uuid4()
CANDIDATE_ID = uuid4()


@pytest.mark.asyncio
async def test_agent_returns_structured_accept_and_tokenizes_sensitive_prompt_values() -> None:
    model = ModelStub(
        {
            "result": {
                "decision": "accept_candidate",
                "candidate_entity_id": str(CANDIDATE_ID),
                "confidence": 0.97,
                "reason": "姓名和手机号一致",
                "strong_evidence_features": ["name", "phone"],
            }
        }
    )
    result = await RematchingAgent(
        model, tokenization_secret="enterprise-tokenization-secret"
    ).decide(
        focal_entity_id=FOCAL_ID,
        focal_payload={"entity_type": "student", "name": "张三", "phone": "13800000000"},
        candidate_edges=(edge(),),
        tenant_id="school-1",
        task_id=uuid4(),
    )

    assert result.decision == "accept_candidate"
    prompt = " ".join(message.content for message in model.requests[0].messages)
    assert "张三" not in prompt
    assert "13800000000" not in prompt
    assert "PERSON_NAME_" in prompt
    assert str(CANDIDATE_ID) in prompt


@pytest.mark.asyncio
async def test_agent_rejects_model_id_outside_server_candidates_with_chinese_fallback() -> None:
    model = ModelStub(
        {
            "result": {
                "decision": "accept_candidate",
                "candidate_entity_id": str(uuid4()),
                "confidence": 0.99,
                "reason": "姓名和手机号一致",
                "strong_evidence_features": ["name", "phone"],
            }
        }
    )

    result = await RematchingAgent(
        model, tokenization_secret="enterprise-tokenization-secret"
    ).decide(
        focal_entity_id=FOCAL_ID,
        focal_payload={"entity_type": "student", "name": "张三"},
        candidate_edges=(edge(),),
        tenant_id="school-1",
        task_id=uuid4(),
    )

    assert isinstance(result, ManualReviewDecision)
    assert "人工" in result.reason


@pytest.mark.asyncio
async def test_agent_converts_invalid_model_output_to_manual_review() -> None:
    model = ModelStub({"result": {"decision": "not-a-decision"}})

    result = await RematchingAgent(
        model, tokenization_secret="enterprise-tokenization-secret"
    ).decide(
        focal_entity_id=FOCAL_ID,
        focal_payload={"entity_type": "student", "name": "张三"},
        candidate_edges=(edge(),),
        tenant_id="school-1",
        task_id=uuid4(),
    )

    assert isinstance(result, ManualReviewDecision)


@pytest.mark.asyncio
async def test_agent_requires_tokenization_secret_for_model_visible_identity_data() -> None:
    model = ModelStub(
        {"result": {"decision": "no_match", "confidence": 0.9, "reason": "没有匹配记录"}}
    )

    with pytest.raises(ValueError, match="tokenization"):
        await RematchingAgent(model).decide(
            focal_entity_id=FOCAL_ID,
            focal_payload={"entity_type": "student", "name": "张三"},
            candidate_edges=(edge(),),
            tenant_id="school-1",
            task_id=uuid4(),
        )
