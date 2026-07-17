import json

import httpx
import pytest

from app.ai.providers.base import LLMRequest, Message, ModelProviderError
from app.ai.providers.embeddings import HttpEmbeddingProvider
from app.ai.providers.llm import HttpLLMProvider
from app.core.config import Settings


@pytest.mark.asyncio
async def test_llm_retries_transient_failure_and_returns_usage() -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(503, request=request)
        return httpx.Response(
            200,
            json={
                "output": {"cause": "mapping drift"},
                "usage": {"input_tokens": 10, "output_tokens": 4},
                "request_id": "model-request-1",
            },
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = HttpLLMProvider(
            settings=Settings(
                llm_url="https://model.example.test/v1/analyze",
                llm_api_key="secret-token",
                llm_model="test-model",
                model_retry_wait_seconds=0,
            ),
            client=client,
        )
        response = await provider.complete_json(
            LLMRequest(messages=(Message(role="user", content="analyze"),))
        )

    assert response.output == {"cause": "mapping drift"}
    assert response.usage.input_tokens == 10
    assert response.usage.output_tokens == 4
    assert response.request_id == "model-request-1"
    assert calls == 2


@pytest.mark.asyncio
async def test_llm_does_not_retry_client_error_or_log_authorization(caplog) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "unauthorized"}, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = HttpLLMProvider(
            settings=Settings(
                llm_url="https://model.example.test/v1/analyze",
                llm_api_key="secret-token",
                model_retry_wait_seconds=0,
            ),
            client=client,
        )
        with pytest.raises(ModelProviderError, match="status 401"):
            await provider.complete_json(
                LLMRequest(messages=(Message(role="user", content="analyze"),))
            )

    assert "secret-token" not in caplog.text
    assert "Bearer" not in caplog.text


@pytest.mark.asyncio
async def test_llm_extracts_json_from_openai_compatible_response() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["model"] == "test-model"
        assert payload["response_format"] == {"type": "json_object"}
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-1",
                "choices": [
                    {"message": {"content": '{"cause":"attribute mismatch"}'}}
                ],
                "usage": {"prompt_tokens": 3, "completion_tokens": 2},
            },
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = HttpLLMProvider(
            settings=Settings(
                llm_url="https://model.example.test/v1/chat/completions",
                llm_api_key="secret-token",
                llm_model="test-model",
            ),
            client=client,
        )
        response = await provider.complete_json(
            LLMRequest(messages=(Message(role="user", content="analyze"),))
        )

    assert response.output == {"cause": "attribute mismatch"}
    assert response.usage.input_tokens == 3
    assert response.usage.output_tokens == 2


@pytest.mark.asyncio
async def test_embedding_provider_returns_vectors_and_usage() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["input"] == ["teacher 张三"]
        return httpx.Response(
            200,
            json={
                "data": [{"embedding": [0.1, 0.2, 0.3]}],
                "usage": {"total_tokens": 5},
            },
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = HttpEmbeddingProvider(
            settings=Settings(
                embedding_url="https://model.example.test/v1/embeddings",
                embedding_api_key="secret-token",
                embedding_model="test-embedding",
                embedding_dimensions=3,
            ),
            client=client,
        )
        batch = await provider.embed(["teacher 张三"])

    assert batch.vectors == [[0.1, 0.2, 0.3]]
    assert batch.usage_tokens == 5
    assert batch.model == "test-embedding"
