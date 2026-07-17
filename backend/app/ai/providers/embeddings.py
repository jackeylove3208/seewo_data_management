import asyncio
import json
from collections.abc import Sequence
from typing import Any

import httpx

from app.ai.providers.base import (
    EmbeddingBatch,
    EmbeddingProvider,
    ModelProviderError,
    TransientModelError,
)
from app.core.config import Settings


class HttpEmbeddingProvider(EmbeddingProvider):
    provider_name = "http"

    def __init__(self, *, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self.settings = settings
        self.client = client
        self.model = settings.embedding_model
        self.dimensions = settings.embedding_dimensions

    async def embed(self, texts: Sequence[str]) -> EmbeddingBatch:
        url = self.settings.embedding_url
        api_key = self.settings.embedding_api_key
        api_key_value = api_key.get_secret_value() if api_key is not None else ""
        if not url or not api_key_value:
            raise ModelProviderError("embedding provider is not configured")
        if not texts:
            return EmbeddingBatch(vectors=[], provider=self.provider_name, model=self.model)
        client = self.client or httpx.AsyncClient()
        try:
            payload = await self._request_with_retries(client, url, api_key_value, texts)
            data = payload.get("data")
            if not isinstance(data, list):
                raise ModelProviderError("embedding response did not contain data")
            vectors: list[list[float]] = []
            for item in data:
                vector = item.get("embedding") if isinstance(item, dict) else None
                if not isinstance(vector, list) or not all(
                    isinstance(value, int | float) for value in vector
                ):
                    raise ModelProviderError("embedding response contained a non-numeric vector")
                vectors.append([float(value) for value in vector])
            if len(vectors) != len(texts):
                raise ModelProviderError("embedding response did not contain one vector per input")
            if any(len(vector) != self.dimensions for vector in vectors):
                raise ModelProviderError(
                    "embedding response contained an unexpected vector dimension"
                )
            usage = payload.get("usage")
            usage_tokens = usage.get("total_tokens", 0) if isinstance(usage, dict) else 0
            return EmbeddingBatch(
                vectors=vectors,
                provider=self.provider_name,
                model=self.model,
                usage_tokens=int(usage_tokens),
            )
        finally:
            if self.client is None:
                await client.aclose()

    async def _request_with_retries(
        self,
        client: httpx.AsyncClient,
        url: str,
        api_key: str,
        texts: Sequence[str],
    ) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(1, self.settings.model_retry_attempts + 1):
            try:
                response = await client.post(
                    url,
                    headers={"Authorization": f"Bearer {api_key}"},
                    json={"model": self.model, "input": list(texts)},
                    timeout=self.settings.embedding_timeout_seconds,
                )
                if response.status_code in {429, 500, 502, 503, 504}:
                    raise TransientModelError(
                        f"embedding request returned status {response.status_code}"
                    )
                if response.is_error:
                    raise ModelProviderError(
                        f"embedding request returned status {response.status_code}"
                    )
                try:
                    payload = response.json()
                except (json.JSONDecodeError, UnicodeDecodeError) as error:
                    raise ModelProviderError("embedding response contained invalid JSON") from error
                if not isinstance(payload, dict):
                    raise ModelProviderError("embedding response must be an object")
                return payload
            except (httpx.TimeoutException, httpx.TransportError, TransientModelError) as error:
                last_error = error
                if attempt == self.settings.model_retry_attempts:
                    break
                await asyncio.sleep(self.settings.model_retry_wait_seconds * attempt)
        raise ModelProviderError(
            f"embedding request failed after {self.settings.model_retry_attempts} attempts"
        ) from last_error
