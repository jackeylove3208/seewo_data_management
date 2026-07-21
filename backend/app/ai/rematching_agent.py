import json
from collections.abc import Sequence
from uuid import UUID

from pydantic import TypeAdapter, ValidationError

from app.ai.providers.base import LLMProvider, LLMRequest, Message
from app.ai.rematching_policy import manual_review_fallback, validate_rematch_decision
from app.ai.tokenization import TaskTokenizationContext
from app.schemas.rematching import (
    CandidateEdge,
    RematchDecision,
    RematchDecisionRequest,
)

_DECISION_ADAPTER: TypeAdapter[RematchDecision] = TypeAdapter(RematchDecision)


class RematchingAgent:
    def __init__(
        self,
        llm: LLMProvider,
        *,
        tokenization_secret: str | None = None,
        high_confidence_threshold: float = 0.9,
        top_k: int = 3,
    ) -> None:
        self.llm = llm
        self.tokenization_secret = tokenization_secret
        self.high_confidence_threshold = high_confidence_threshold
        self.top_k = top_k

    async def decide(
        self,
        *,
        focal_entity_id: UUID,
        focal_payload: dict[str, object],
        candidate_edges: Sequence[CandidateEdge],
        tenant_id: str,
        task_id: UUID,
    ) -> RematchDecision:
        if self.tokenization_secret is None:
            raise ValueError("rematching tokenization secret is required")
        tokenizer = TaskTokenizationContext(
            secret=self.tokenization_secret,
            tenant_id=tenant_id,
            task_id=task_id,
        )
        payload = {
            "focal_entity_id": str(focal_entity_id),
            "focal": focal_payload,
            "candidates": [
                {
                    **edge.model_dump(mode="json"),
                    "entity_type": focal_payload.get("entity_type"),
                }
                for edge in candidate_edges
            ],
        }
        safe_payload = tokenizer.tokenize(payload)
        response = await self.llm.complete_json(
            LLMRequest(
                messages=(
                    Message(
                        role="system",
                        content=(
                            "你是实体二次匹配审查器。只能从服务端候选中选择，"
                            "输出 accept_candidate、no_match 或 manual_review。"
                            "证据不足或冲突时必须人工复核，原因使用简体中文。"
                        ),
                    ),
                    Message(
                        role="user",
                        content=json.dumps(safe_payload, ensure_ascii=False, separators=(",", ":")),
                    ),
                ),
                response_schema=_DECISION_ADAPTER.json_schema(),
            )
        )
        raw_decision = response.output.get("result", response.output)
        try:
            decision = _DECISION_ADAPTER.validate_python(raw_decision)
            request = RematchDecisionRequest(
                focal_entity_id=focal_entity_id,
                server_candidate_ids=tuple(edge.candidate_entity_id for edge in candidate_edges),
                decision=decision,
            )
            return validate_rematch_decision(
                request,
                candidate_edges=candidate_edges,
                high_confidence_threshold=self.high_confidence_threshold,
                top_k=self.top_k,
            )
        except (ValidationError, ValueError):
            return manual_review_fallback()
