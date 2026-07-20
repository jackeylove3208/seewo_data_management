import json

import httpx
import pytest
from pydantic import ValidationError

from app.ai.providers.base import (
    LLMRequest,
    Message,
    ModelProviderError,
    TransientModelError,
)
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
                "provider": "untrusted-provider",
                "model": "untrusted-model",
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
    assert response.provider == "http"
    assert response.model == "test-model"
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
async def test_llm_failure_traceback_does_not_contain_api_key() -> None:
    secret = "test-api-key-that-must-not-leak"

    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection failed", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = HttpLLMProvider(
            settings=Settings(
                _env_file=None,
                llm_url="https://model.example.test/chat/completions",
                llm_api_key=secret,
                tokenization_secret="tokenization-secret",
                model_retry_wait_seconds=0,
            ),
            client=client,
        )
        with pytest.raises(TransientModelError) as captured:
            await provider.complete_json(
                LLMRequest(messages=(Message(role="user", content="ping"),))
            )

    traceback = captured.value.__traceback__
    while traceback is not None:
        if traceback.tb_frame.f_code.co_filename.endswith("app/ai/providers/llm.py"):
            assert secret not in repr(traceback.tb_frame.f_locals)
        traceback = traceback.tb_next


@pytest.mark.asyncio
async def test_llm_extracts_json_from_openai_compatible_response() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["model"] == "test-model"
        assert "response_schema" not in payload
        assert payload["response_format"] == {
            "type": "json_schema",
            "json_schema": {
                "name": "governance_response",
                "strict": True,
                "schema": {"type": "object"},
            },
        }
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-1",
                "choices": [{"message": {"content": '{"cause":"attribute mismatch"}'}}],
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
                llm_response_mode="json_schema",
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
@pytest.mark.parametrize(
    ("mode", "expected_response_format"),
    [
        ("json_object", {"type": "json_object"}),
        ("prompt_json", None),
    ],
)
async def test_llm_supports_enterprise_response_modes(
    mode: str,
    expected_response_format: dict | None,
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload.get("response_format") == expected_response_format
        return httpx.Response(200, json={"output": {"cause": "ok"}}, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = HttpLLMProvider(
            settings=Settings(
                llm_url="https://gateway.example.test/v1/chat/completions",
                llm_api_key="secret-token",
                llm_response_mode=mode,
            ),
            client=client,
        )
        response = await provider.complete_json(
            LLMRequest(messages=(Message(role="user", content="analyze"),))
        )

    assert response.output == {"cause": "ok"}


@pytest.mark.asyncio
async def test_llm_merges_validated_enterprise_headers_and_body() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert request.headers["X-API-Key"] == "secret-token"
        assert request.headers["X-Gateway-App"] == "reconciliation"
        assert payload["top_p"] == 0.8
        assert payload["model"] == "enterprise-model"
        return httpx.Response(200, json={"output": {"cause": "ok"}}, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = HttpLLMProvider(
            settings=Settings(
                llm_url="https://gateway.example.test/v1/chat/completions",
                llm_api_key="secret-token",
                llm_model="enterprise-model",
                llm_auth_header="X-API-Key",
                llm_auth_scheme="",
                llm_extra_headers_json={"X-Gateway-App": "reconciliation"},
                llm_extra_body_json={"top_p": 0.8},
            ),
            client=client,
        )
        await provider.complete_json(
            LLMRequest(messages=(Message(role="user", content="analyze"),))
        )


@pytest.mark.parametrize("reserved", ["model", "messages", "response_format", "stream"])
def test_settings_rejects_reserved_extra_body_fields(reserved: str) -> None:
    with pytest.raises(ValidationError, match="reserved LLM body field"):
        Settings(llm_extra_body_json={reserved: "override"})


def test_settings_rejects_auth_header_override() -> None:
    with pytest.raises(ValidationError, match="reserved LLM header"):
        Settings(llm_extra_headers_json={"authorization": "spoofed"})


@pytest.mark.asyncio
async def test_embedding_provider_returns_vectors_and_usage() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["input"] == ["teacher 张三"]
        return httpx.Response(
            200,
            json={
                "data": [{"embedding": [0.1, 0.2, 0.3]}],
                "provider": "untrusted-provider",
                "model": "untrusted-model",
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
    assert batch.provider == "http"
    assert batch.model == "test-embedding"


@pytest.mark.asyncio
async def test_embedding_provider_retries_transient_failure() -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, request=request)
        return httpx.Response(
            200,
            json={"data": [{"embedding": [0.1, 0.2, 0.3]}]},
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = HttpEmbeddingProvider(
            settings=Settings(
                embedding_url="https://model.example.test/v1/embeddings",
                embedding_api_key="secret-token",
                embedding_dimensions=3,
                model_retry_wait_seconds=0,
            ),
            client=client,
        )
        batch = await provider.embed(["teacher 张三"])

    assert batch.vectors == [[0.1, 0.2, 0.3]]
    assert calls == 2


@pytest.mark.asyncio
async def test_llm_normalizes_a_non_json_success_response() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not-json", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = HttpLLMProvider(
            settings=Settings(
                llm_url="https://model.example.test/v1/analyze",
                llm_api_key="secret-token",
                model_retry_wait_seconds=0,
            ),
            client=client,
        )
        with pytest.raises(ModelProviderError, match="invalid JSON"):
            await provider.complete_json(
                LLMRequest(messages=(Message(role="user", content="analyze"),))
            )


@pytest.mark.asyncio
async def test_embedding_normalizes_a_non_json_success_response() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not-json", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = HttpEmbeddingProvider(
            settings=Settings(
                embedding_url="https://model.example.test/v1/embeddings",
                embedding_api_key="secret-token",
                model_retry_wait_seconds=0,
            ),
            client=client,
        )
        with pytest.raises(ModelProviderError, match="invalid JSON"):
            await provider.embed(["teacher 张三"])


@pytest.mark.asyncio
async def test_embedding_rejects_a_mismatched_vector_dimension() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"data": [{"embedding": [0.1, 0.2]}]},
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = HttpEmbeddingProvider(
            settings=Settings(
                embedding_url="https://model.example.test/v1/embeddings",
                embedding_api_key="secret-token",
                embedding_dimensions=3,
            ),
            client=client,
        )
        with pytest.raises(ModelProviderError, match="dimension"):
            await provider.embed(["teacher 张三"])
