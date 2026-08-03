import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
from pydantic import SecretStr

from app.ai.providers.base import (
    LLMProvider,
    LLMRequest,
    LLMResponse,
    ModelProviderError,
    ModelUsage,
    TransientModelError,
)
from app.core.config import Settings


class HttpLLMProvider(LLMProvider):
    """Minimal OpenAI-compatible structured JSON client with bounded retries."""

    provider_name = "http"
    requires_tokenization = True

    def __init__(
        self,
        *,
        settings: Settings,
        client: httpx.AsyncClient | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.settings = settings
        self.client = client
        self.sleep = sleep

    async def complete_json(self, request: LLMRequest) -> LLMResponse:
        url = self.settings.llm_url
        api_key = self.settings.llm_api_key
        if not url or api_key is None or not api_key.get_secret_value():
            raise ModelProviderError("LLM provider is not configured")
        client = self.client or httpx.AsyncClient()
        try:
            return await self._complete_with(client, request, url, api_key)
        finally:
            if self.client is None:
                await client.aclose()

    async def complete_json_once(self, request: LLMRequest) -> LLMResponse:
        """Make one transport attempt for the outer durable Agent retry loop."""
        url = self.settings.llm_url
        api_key = self.settings.llm_api_key
        if not url or api_key is None or not api_key.get_secret_value():
            raise ModelProviderError("LLM provider is not configured")
        client = self.client or httpx.AsyncClient()
        try:
            try:
                async with asyncio.timeout(self.settings.llm_timeout_seconds):
                    response = await client.post(
                        url,
                        headers=_request_headers(self.settings, api_key),
                        json=_request_body(self.settings, request),
                        timeout=self.settings.llm_timeout_seconds,
                    )
            except (
                TimeoutError,
                httpx.TimeoutException,
                httpx.TransportError,
            ) as error:
                raise TransientModelError("model transport failed") from error
            if response.status_code in {429, 500, 502, 503, 504}:
                raise TransientModelError(f"model request returned status {response.status_code}")
            if response.is_error:
                raise ModelProviderError(f"model request returned status {response.status_code}")
            try:
                payload = response.json()
            except (json.JSONDecodeError, UnicodeDecodeError) as error:
                raise ModelProviderError("model response contained invalid JSON") from error
            if not isinstance(payload, dict):
                raise ModelProviderError("model response must be a JSON object")
            return _parse_response(payload, self.settings)
        finally:
            if self.client is None:
                await client.aclose()

    async def _complete_with(
        self,
        client: httpx.AsyncClient,
        request: LLMRequest,
        url: str,
        api_key: SecretStr,
    ) -> LLMResponse:
        last_error: Exception | None = None
        for attempt in range(1, self.settings.model_retry_attempts + 1):
            try:
                response = await client.post(
                    url,
                    headers=_request_headers(self.settings, api_key),
                    json=_request_body(self.settings, request),
                    timeout=self.settings.llm_timeout_seconds,
                )
                if response.status_code in {429, 500, 502, 503, 504}:
                    raise TransientModelError(
                        f"model request returned status {response.status_code}"
                    )
                if response.is_error:
                    raise ModelProviderError(
                        f"model request returned status {response.status_code}"
                    )
                try:
                    payload = response.json()
                except (json.JSONDecodeError, UnicodeDecodeError) as error:
                    raise ModelProviderError("model response contained invalid JSON") from error
                if not isinstance(payload, dict):
                    raise ModelProviderError("model response must be a JSON object")
                return _parse_response(payload, self.settings)
            except (httpx.TimeoutException, httpx.TransportError, TransientModelError) as error:
                last_error = error
                if attempt == self.settings.model_retry_attempts:
                    break
                await self.sleep(self.settings.model_retry_wait_seconds * attempt)
        raise TransientModelError(
            f"model request failed after {self.settings.model_retry_attempts} attempts"
        ) from last_error


def _request_headers(settings: Settings, api_key: SecretStr) -> dict[str, str]:
    authentication = f"{settings.llm_auth_scheme} {api_key.get_secret_value()}".strip()
    return {
        **settings.llm_extra_headers_json,
        settings.llm_auth_header: authentication,
    }


def _request_body(settings: Settings, request: LLMRequest) -> dict[str, Any]:
    messages = [message.model_dump() for message in request.messages]
    if (
        settings.llm_response_mode.value == "json_object"
        and request.response_schema
    ):
        contract = _json_object_contract(request)
        system_index = next(
            (
                index
                for index, message in enumerate(messages)
                if message["role"] == "system"
            ),
            None,
        )
        if system_index is None:
            messages.insert(0, {"role": "system", "content": contract})
        else:
            messages[system_index]["content"] += f"\n\n{contract}"
    body: dict[str, Any] = {
        "model": settings.llm_model,
        "messages": messages,
        "temperature": request.temperature,
        "max_tokens": settings.llm_max_output_tokens,
        **settings.llm_extra_body_json,
    }
    if settings.llm_response_mode.value == "json_schema":
        body["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": "governance_response",
                "strict": True,
                "schema": request.response_schema or {"type": "object"},
            },
        }
    elif settings.llm_response_mode.value == "json_object":
        body["response_format"] = {"type": "json_object"}
    return body


def _json_object_contract(request: LLMRequest) -> str:
    sections = [
        "## JSON Schema\n"
        "只返回符合下列 JSON Schema 的 JSON 对象；字段名、数组类型和嵌套结构必须完全一致。\n"
        + json.dumps(
            request.response_schema,
            ensure_ascii=False,
            sort_keys=True,
        )
    ]
    if request.response_example is not None:
        sections.append(
            "## 合法 JSON 示例\n"
            "只模仿字段结构与类型，业务值仍必须来自本次输入。\n"
            + json.dumps(
                request.response_example,
                ensure_ascii=False,
                sort_keys=True,
            )
        )
    return "\n\n".join(sections)


def _parse_response(payload: dict[str, Any], settings: Settings) -> LLMResponse:
    output = payload.get("output")
    if output is None:
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ModelProviderError("model response did not contain structured output")
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str):
            raise ModelProviderError("model response did not contain JSON content")
        try:
            output = json.loads(content)
        except json.JSONDecodeError as error:
            raise ModelProviderError("model response contained invalid JSON") from error
    if not isinstance(output, dict):
        raise ModelProviderError("model response output must be an object")
    usage = payload.get("usage")
    usage_values = usage if isinstance(usage, dict) else {}
    return LLMResponse(
        output=output,
        provider=HttpLLMProvider.provider_name,
        model=settings.llm_model,
        usage=ModelUsage(
            input_tokens=_token_count(usage_values, "input_tokens", "prompt_tokens"),
            output_tokens=_token_count(usage_values, "output_tokens", "completion_tokens"),
        ),
        request_id=_optional_string(payload.get("request_id") or payload.get("id")),
    )


def _optional_string(value: object) -> str | None:
    return str(value) if value is not None else None


def _token_count(values: dict[str, Any], *keys: str) -> int:
    for key in keys:
        value = values.get(key)
        if isinstance(value, int):
            return value
    return 0
