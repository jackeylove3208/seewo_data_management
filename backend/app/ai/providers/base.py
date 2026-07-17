from collections.abc import Sequence
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field


class ModelProviderError(RuntimeError):
    """The configured external model provider did not return a usable response."""


class TransientModelError(ModelProviderError):
    """A retryable provider failure, such as a timeout, rate limit, or 5xx response."""


class Message(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    role: Literal["system", "user", "assistant", "tool"]
    content: str = Field(min_length=1)


class ModelUsage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)


class LLMRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    messages: tuple[Message, ...] = Field(min_length=1)
    response_schema: dict[str, Any] = Field(default_factory=dict)
    temperature: float = Field(default=0, ge=0, le=2)


class LLMResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    output: dict[str, Any]
    provider: str = Field(min_length=1, max_length=128)
    model: str = Field(min_length=1, max_length=255)
    usage: ModelUsage = Field(default_factory=ModelUsage)
    request_id: str | None = Field(default=None, max_length=255)


class LLMProvider(Protocol):
    async def complete_json(self, request: LLMRequest) -> LLMResponse: ...


class EmbeddingBatch(BaseModel):
    model_config = ConfigDict(frozen=True)

    vectors: list[list[float]]
    provider: str = Field(min_length=1, max_length=128)
    model: str = Field(min_length=1, max_length=255)
    usage_tokens: int = Field(default=0, ge=0)


class EmbeddingProvider(Protocol):
    dimensions: int
    provider_name: str
    model: str

    async def embed(self, texts: Sequence[str]) -> EmbeddingBatch: ...
