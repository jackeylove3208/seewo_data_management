from collections.abc import Sequence

import httpx

from app.ai.providers.base import EmbeddingBatch, EmbeddingProvider, ModelProviderError
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
        if url is None or api_key is None:
            raise ModelProviderError("embedding provider is not configured")
        if not texts:
            return EmbeddingBatch(vectors=[], provider=self.provider_name, model=self.model)
        client = self.client or httpx.AsyncClient()
        try:
            response = await client.post(
                url,
                headers={"Authorization": f"Bearer {api_key.get_secret_value()}"},
                json={"model": self.model, "input": list(texts)},
                timeout=self.settings.embedding_timeout_seconds,
            )
            if response.is_error:
                raise ModelProviderError(
                    f"embedding request returned status {response.status_code}"
                )
            payload = response.json()
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
                raise ModelProviderError(
                    "embedding response did not contain one vector per input"
                )
            usage = payload.get("usage")
            usage_tokens = usage.get("total_tokens", 0) if isinstance(usage, dict) else 0
            return EmbeddingBatch(
                vectors=vectors,
                provider=str(payload.get("provider") or self.provider_name),
                model=str(payload.get("model") or self.model),
                usage_tokens=int(usage_tokens),
            )
        finally:
            if self.client is None:
                await client.aclose()
