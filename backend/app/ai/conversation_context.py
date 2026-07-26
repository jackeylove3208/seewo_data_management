"""Conservative capacity checks for complete conversation requests."""

import math

from app.ai.providers.base import LLMRequest


class ConversationContextLimitError(RuntimeError):
    """The complete request cannot fit without dropping conversation history."""

    def __init__(self, estimated_tokens: int, available_tokens: int) -> None:
        self.estimated_tokens = estimated_tokens
        self.available_tokens = available_tokens
        super().__init__(
            "conversation context exceeds configured budget: "
            f"estimated={estimated_tokens}, available={available_tokens}"
        )


def estimate_request_tokens(request: LLMRequest) -> int:
    """Overestimate common Chinese/ASCII chat payloads without provider tokenizers."""

    byte_count = sum(
        len(message.content.encode("utf-8")) for message in request.messages
    )
    message_overhead = len(request.messages) * 8
    return math.ceil(byte_count / 3) + message_overhead


def ensure_conversation_request_fits(
    request: LLMRequest,
    *,
    max_context_tokens: int,
    reserved_output_tokens: int,
) -> None:
    available_tokens = max_context_tokens - reserved_output_tokens
    estimated_tokens = estimate_request_tokens(request)
    if estimated_tokens > available_tokens:
        raise ConversationContextLimitError(estimated_tokens, available_tokens)
