"""Single-attempt model invocation for one durable Agent analysis batch."""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.ai.agent_analysis import validate_agent_model_output
from app.ai.agent_batching import MAX_MODEL_ANALYSIS_BATCH_SIZE
from app.ai.agent_phone_privacy import StudentPhoneTokenizationContext
from app.ai.agent_prompting import render_agent_system_prompt
from app.ai.providers.base import LLMRequest, LLMResponse, Message
from app.ai.skills.registry import SkillRegistry
from app.schemas.agent_reconciliation import AgentFindingPayload


class SingleAttemptModelProvider(Protocol):
    async def complete_json_once(self, request: LLMRequest) -> LLMResponse: ...


@dataclass(frozen=True)
class AgentAnalysisWorkItem:
    work_item_id: UUID
    kind: str
    entity_kind: str
    locator: str
    fields: Mapping[str, str | None]


class _AgentFindingBatchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    findings: tuple[AgentFindingPayload, ...] = Field(min_length=1, max_length=50)


class AgentAnalysisService:
    def __init__(
        self,
        provider: SingleAttemptModelProvider,
        *,
        tokenization_secret: str,
        skill_registry: SkillRegistry | None = None,
    ) -> None:
        self._provider = provider
        self._tokenization_secret = tokenization_secret
        self._skills = skill_registry or SkillRegistry()

    async def analyze(
        self,
        *,
        tenant_id: str,
        task_id: UUID,
        work_items: tuple[AgentAnalysisWorkItem, ...],
    ) -> tuple[AgentFindingPayload, ...]:
        if not 1 <= len(work_items) <= MAX_MODEL_ANALYSIS_BATCH_SIZE:
            raise ValueError("Agent model calls require 1..10 work items")
        tokenizer = StudentPhoneTokenizationContext(
            secret=self._tokenization_secret, tenant_id=tenant_id, task_id=task_id
        )
        evidence = [
            {
                "work_item_id": str(item.work_item_id),
                "kind": item.kind,
                "entity_kind": item.entity_kind,
                "locator": item.locator,
                "fields": {
                    key: tokenizer.tokenize(value, entity_kind=item.entity_kind)
                    if key == "phone"
                    else value
                    for key, value in item.fields.items()
                },
            }
            for item in work_items
        ]
        reconciliation_skill = self._skills.load("reconcile-entity-batch", "1.0.0")
        solution_skill = self._skills.load("generate-governance-solutions", "1.0.0")
        request = LLMRequest(
            messages=(
                Message(
                    role="system",
                    content=render_agent_system_prompt(
                        (reconciliation_skill, solution_skill),
                        invocation_contract=(
                            "本批次必须为输入中的每个 work_item_id 返回且仅返回一个 finding；"
                            "不得遗漏、重复或增加 work_item_id。finding.kind 必须等于服务端给出的"
                            "持久化 kind。每个 finding 必须包含一至三条 solution，且恰好一条"
                            " recommended=true。只输出响应 JSON Schema 的根对象，"
                            "不增加 result 包装。"
                        ),
                    ),
                ),
                Message(
                    role="user",
                    content=json.dumps({"untrusted_evidence": evidence}, ensure_ascii=False),
                ),
            ),
            response_schema=_response_schema(),
        )
        response = await self._provider.complete_json_once(request)
        invalid_ids = {item.work_item_id for item in work_items if item.kind == "authority_invalid"}
        return validate_agent_model_output(
            response.output,
            tuple(item.work_item_id for item in work_items),
            authority_invalid_ids=invalid_ids,
            expected_kinds={item.work_item_id: item.kind for item in work_items},
        )


def _response_schema() -> dict[str, object]:
    return _AgentFindingBatchResponse.model_json_schema()
