import json

import httpx
import pytest

from app.ai.agent import GovernanceAgent
from app.ai.analysis_service import AnalysisService
from app.ai.mcp.server import MCPToolGateway
from app.ai.providers.base import LLMRequest, Message, TransientModelError
from app.ai.providers.llm import HttpLLMProvider
from app.core.config import Settings
from app.schemas.differences import DifferenceType
from app.schemas.governance import (
    AnalysisStatus,
    AutoExecutableResolution,
    CauseAnalysisV2,
    CauseAnalysisV3,
    ManualResolution,
)
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


@pytest.mark.asyncio
async def test_exhausted_gateway_503_remains_transient_for_worker_retry() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, request=request)

    settings = Settings(
        llm_url="https://gateway.example.test/v1/chat/completions",
        llm_api_key="enterprise-key",
        llm_model="enterprise-model",
        tokenization_secret="enterprise-tokenization-secret",
        model_retry_attempts=2,
        model_retry_wait_seconds=0,
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = HttpLLMProvider(settings=settings, client=client)
        with pytest.raises(TransientModelError):
            await provider.complete_json(
                LLMRequest(
                    messages=(Message(role="user", content="测试请求"),),
                )
            )


@pytest.mark.asyncio
async def test_v3_analysis_uses_enterprise_gateway_and_chinese_contract(session) -> None:
    difference = await seed_difference(session, DifferenceType.ATTRIBUTE_CONFLICT)
    captured: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        request_text = request.content.decode()
        captured.append(request_text)
        assert "13800000000" not in request_text
        assert "13900000000" not in request_text
        body = json.loads(request.content)
        evidence = json.loads(body["messages"][-1]["content"])["input_payload"]["evidence"]
        field = evidence["fields"][0]
        output = {
            "result": {
                "locale": "zh-CN",
                "issue_title": "教师手机号不一致",
                "cause_summary": "权威记录与希沃保存的手机号不同。",
                "evidence_summary": "系统比对了双方快照中的手机号字段。",
                "business_impact": "教师可能无法收到教学通知。",
                "recommended_solution_id": "solution-1",
                "solutions": [
                    {
                        "solution_id": "solution-1",
                        "mode": "auto_executable",
                        "title": "更新教师手机号",
                        "rationale": "采用第三方权威记录中的手机号。",
                        "risk": "low",
                        "risk_reason": "仅修改已确认教师的一项联系方式。",
                        "confidence": 0.95,
                        "evidence_refs": ["field:phone"],
                        "preconditions": [],
                        "recommended": True,
                        "action": {
                            "operation_type": "update",
                            "target_entity_id": evidence["target_entity_id"],
                            "proposed_changes": [
                                {
                                    "field": "phone",
                                    "before": field["target_value"],
                                    "after": field["source_value"],
                                }
                            ],
                        },
                    }
                ],
            }
        }
        return httpx.Response(
            200,
            json={
                "id": "enterprise-v3-request",
                "choices": [{"message": {"content": json.dumps(output)}}],
                "usage": {"prompt_tokens": 29, "completion_tokens": 21},
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
        result = await AnalysisService(
            session,
            agent=GovernanceAgent(
                HttpLLMProvider(settings=settings, client=client),
                MCPToolGateway(session),
                tokenization_secret=settings.tokenization_secret.get_secret_value(),
            ),
        ).analyze_v3(difference.id)

    assert result.status is AnalysisStatus.SUCCEEDED
    assert isinstance(result.output, CauseAnalysisV3)
    assert isinstance(result.output.solutions[0], AutoExecutableResolution)
    assert result.output.solutions[0].action.proposed_changes[0].after == "13800000000"
    assert result.provenance.gateway_request_ids == ("enterprise-v3-request",)
    assert len(captured) == 1


@pytest.mark.asyncio
async def test_v3_gateway_repairs_policy_invalid_output_after_feedback(session) -> None:
    difference = await seed_difference(session, DifferenceType.ATTRIBUTE_CONFLICT)
    captured_inputs: list[dict[str, object]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        input_payload = json.loads(body["messages"][-1]["content"])["input_payload"]
        captured_inputs.append(input_payload)
        evidence = input_payload["evidence"]
        field = evidence["fields"][0]
        cause_summary = (
            "Phone values do not match"
            if len(captured_inputs) == 1
            else "权威记录与希沃保存的手机号不同。"
        )
        return httpx.Response(
            200,
            json={
                "id": f"enterprise-v3-repair-{len(captured_inputs)}",
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                _v3_auto_response(
                                    evidence,
                                    field,
                                    cause_summary=cause_summary,
                                )
                            )
                        }
                    }
                ],
                "usage": {"prompt_tokens": 11, "completion_tokens": 7},
            },
            request=request,
        )

    settings = _gateway_settings()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await AnalysisService(
            session,
            agent=GovernanceAgent(
                HttpLLMProvider(settings=settings, client=client),
                MCPToolGateway(session),
                tokenization_secret=settings.tokenization_secret.get_secret_value(),
            ),
        ).analyze_v3(difference.id)

    assert result.status is AnalysisStatus.SUCCEEDED
    assert isinstance(result.output, CauseAnalysisV3)
    assert result.output.cause_summary == "权威记录与希沃保存的手机号不同。"
    assert len(captured_inputs) == 2
    assert "validation_feedback" not in captured_inputs[0]
    assert captured_inputs[1]["validation_feedback"] == "analysis_policy_error"


@pytest.mark.asyncio
async def test_v3_gateway_exhaustion_persists_chinese_manual_fallback(session) -> None:
    difference = await seed_difference(session, DifferenceType.ATTRIBUTE_CONFLICT)
    request_count = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        body = json.loads(request.content)
        evidence = json.loads(body["messages"][-1]["content"])["input_payload"]["evidence"]
        field = evidence["fields"][0]
        return httpx.Response(
            200,
            json={
                "id": f"enterprise-v3-invalid-{request_count}",
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                _v3_auto_response(
                                    evidence,
                                    field,
                                    cause_summary="Phone values do not match",
                                )
                            )
                        }
                    }
                ],
                "usage": {"prompt_tokens": 11, "completion_tokens": 7},
            },
            request=request,
        )

    settings = _gateway_settings()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await AnalysisService(
            session,
            agent=GovernanceAgent(
                HttpLLMProvider(settings=settings, client=client),
                MCPToolGateway(session),
                tokenization_secret=settings.tokenization_secret.get_secret_value(),
            ),
        ).analyze_v3(difference.id)

    assert request_count == 2
    assert result.status is AnalysisStatus.MANUAL_REVIEW
    assert result.failure_code == "analysis_policy_error"
    assert isinstance(result.output, CauseAnalysisV3)
    assert isinstance(result.output.solutions[0], ManualResolution)
    assert result.output.solutions
    assert "人工" in result.output.issue_title


@pytest.mark.asyncio
async def test_v3_gateway_closes_mcp_read_transaction_before_follow_up_model_call(
    session,
) -> None:
    difference = await seed_difference(session, DifferenceType.ATTRIBUTE_CONFLICT)
    transaction_states: list[bool] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        transaction_states.append(session.in_transaction())
        response = (
            {
                "result": {
                    "tool_call": {
                        "name": "difference_context",
                        "arguments": {"difference_id": str(difference.id)},
                    }
                }
            }
            if len(transaction_states) == 1
            else _v3_manual_response()
        )
        return httpx.Response(
            200,
            json={
                "id": f"enterprise-v3-tool-{len(transaction_states)}",
                "choices": [{"message": {"content": json.dumps(response)}}],
                "usage": {"prompt_tokens": 9, "completion_tokens": 5},
            },
            request=request,
        )

    settings = _gateway_settings()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await AnalysisService(
            session,
            agent=GovernanceAgent(
                HttpLLMProvider(settings=settings, client=client),
                MCPToolGateway(session),
                tokenization_secret=settings.tokenization_secret.get_secret_value(),
            ),
        ).analyze_v3(difference.id)

    assert isinstance(result.output, CauseAnalysisV3)
    assert transaction_states == [False, False]


@pytest.mark.asyncio
async def test_v3_gateway_closes_mcp_read_transaction_after_tool_error(session) -> None:
    difference = await seed_difference(session, DifferenceType.ATTRIBUTE_CONFLICT)
    transaction_states: list[bool] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        transaction_states.append(session.in_transaction())
        response = (
            {
                "result": {
                    "tool_call": {
                        "name": "candidate_search",
                        "arguments": {
                            "difference_id": str(difference.id),
                            "query": "教师候选",
                            "top_k": "invalid",
                        },
                    }
                }
            }
            if len(transaction_states) == 1
            else _v3_manual_response()
        )
        return httpx.Response(
            200,
            json={
                "id": f"enterprise-v3-tool-error-{len(transaction_states)}",
                "choices": [{"message": {"content": json.dumps(response)}}],
                "usage": {"prompt_tokens": 9, "completion_tokens": 5},
            },
            request=request,
        )

    settings = _gateway_settings()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await AnalysisService(
            session,
            agent=GovernanceAgent(
                HttpLLMProvider(settings=settings, client=client),
                MCPToolGateway(session),
                tokenization_secret=settings.tokenization_secret.get_secret_value(),
            ),
        ).analyze_v3(difference.id)

    assert isinstance(result.output, CauseAnalysisV3)
    assert transaction_states == [False, False]


@pytest.mark.asyncio
@pytest.mark.parametrize("difference_type", tuple(DifferenceType))
async def test_v3_every_difference_type_has_a_resolution_path(
    session,
    difference_type: DifferenceType,
) -> None:
    difference = await seed_difference(session, difference_type)

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": f"enterprise-v3-{difference_type.value}",
                "choices": [{"message": {"content": json.dumps(_v3_manual_response())}}],
                "usage": {"prompt_tokens": 9, "completion_tokens": 5},
            },
            request=request,
        )

    settings = _gateway_settings()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await AnalysisService(
            session,
            agent=GovernanceAgent(
                HttpLLMProvider(settings=settings, client=client),
                MCPToolGateway(session),
                tokenization_secret=settings.tokenization_secret.get_secret_value(),
            ),
        ).analyze_v3(difference.id)

    assert isinstance(result.output, CauseAnalysisV3)
    assert result.output.solutions
    assert any(
        solution.solution_id == result.output.recommended_solution_id
        for solution in result.output.solutions
    )


def _gateway_settings() -> Settings:
    return Settings(
        llm_url="https://gateway.example.test/v1/chat/completions",
        llm_api_key="enterprise-key",
        llm_model="enterprise-model",
        tokenization_secret="enterprise-tokenization-secret",
    )


def _v3_auto_response(
    evidence: dict[str, object],
    field: dict[str, object],
    *,
    cause_summary: str,
) -> dict[str, object]:
    return {
        "result": {
            "locale": "zh-CN",
            "issue_title": "教师手机号不一致",
            "cause_summary": cause_summary,
            "evidence_summary": "系统比对了双方快照中的手机号字段。",
            "business_impact": "教师可能无法收到教学通知。",
            "recommended_solution_id": "solution-1",
            "solutions": [
                {
                    "solution_id": "solution-1",
                    "mode": "auto_executable",
                    "title": "更新教师手机号",
                    "rationale": "采用第三方权威记录中的手机号。",
                    "risk": "low",
                    "risk_reason": "仅修改已确认教师的一项联系方式。",
                    "confidence": 0.95,
                    "evidence_refs": ["field:phone"],
                    "preconditions": [],
                    "recommended": True,
                    "action": {
                        "operation_type": "update",
                        "target_entity_id": evidence["target_entity_id"],
                        "proposed_changes": [
                            {
                                "field": "phone",
                                "before": field["target_value"],
                                "after": field["source_value"],
                            }
                        ],
                    },
                }
            ],
        }
    }


def _v3_manual_response() -> dict[str, object]:
    return {
        "result": {
            "locale": "zh-CN",
            "issue_title": "需要人工核对数据差异",
            "cause_summary": "当前证据不足以安全确定自动处理方式。",
            "evidence_summary": "系统已保留双方快照和当前差异证据。",
            "business_impact": "未经核对直接修改可能影响错误记录。",
            "recommended_solution_id": "manual-1",
            "solutions": [
                {
                    "solution_id": "manual-1",
                    "mode": "manual_only",
                    "title": "人工核对差异",
                    "rationale": "先核对实体身份和当前归属，再决定处理方式。",
                    "risk": "high",
                    "risk_reason": "现有证据无法排除错误修改其他记录的风险。",
                    "confidence": 0.2,
                    "evidence_refs": [],
                    "preconditions": [],
                    "recommended": True,
                    "manual_steps": [
                        {"order": 1, "instruction": "联系学校管理员核对当前记录。"},
                        {"order": 2, "instruction": "确认后生成待执行治理方案。"},
                    ],
                }
            ],
        }
    }
