"""Model-backed Skill runner for evidence-bounded Agent graph sub-agents."""

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_graph.repository import AgentGraphRepository
from app.agent_graph.tools import (
    GRAPH_NODE_TOOL_NAMES,
    GraphPhaseToolGateway,
    GraphToolAuthorizationError,
    GraphToolContext,
)
from app.ai.agent_prompting import extract_model_result, render_agent_system_prompt
from app.ai.providers.base import (
    LLMRequest,
    LLMResponse,
    Message,
    ModelProviderError,
)
from app.ai.skills.registry import SkillDefinition, SkillRegistry, UnsafeSkillError
from app.core.security import OperatorContext


class GraphSubAgentFailure(RuntimeError):
    """Raised after all bounded model attempts fail closed."""


class GraphSkillInvocation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: UUID
    run_id: UUID
    graph_run_id: UUID
    graph_node: str = Field(min_length=1, max_length=128)
    graph_cursor: int = Field(ge=0)
    action_id: str = Field(min_length=1, max_length=128)
    evidence_manifest_id: UUID
    skill_name: str = Field(min_length=1, max_length=128)
    skill_version: str = Field(min_length=1, max_length=64)
    input_payload: dict[str, Any]


@dataclass(frozen=True)
class GraphSkillRunResult:
    output: BaseModel
    invocation_id: UUID
    attempt_count: int


ResultValidator = Callable[[BaseModel], BaseModel]


class GraphSkillModelProvider(Protocol):
    async def complete_json_once(self, request: LLMRequest) -> LLMResponse: ...


class GraphSkillModelRunner:
    """Execute one pinned Skill with an audited, phase-scoped tool loop."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        provider: GraphSkillModelProvider,
        tool_gateway: GraphPhaseToolGateway,
        operator: OperatorContext,
        skills: SkillRegistry | None = None,
        max_retries: int = 3,
        max_tool_calls: int = 8,
    ) -> None:
        if not 0 <= max_retries <= 3:
            raise ValueError("graph sub-agent max_retries must be between zero and three")
        if not 0 <= max_tool_calls <= 16:
            raise ValueError("graph sub-agent max_tool_calls must be between zero and sixteen")
        self._session = session
        self._provider = provider
        self._tool_gateway = tool_gateway
        self._operator = operator
        self._skills = skills or SkillRegistry()
        self._max_retries = max_retries
        self._max_tool_calls = max_tool_calls
        self._repository = AgentGraphRepository(session)

    async def run(
        self,
        invocation: GraphSkillInvocation,
        *,
        result_validator: ResultValidator | None = None,
    ) -> GraphSkillRunResult:
        skill = self._skills.load(invocation.skill_name, invocation.skill_version)
        try:
            validated_input = self._skills.validate_input(
                skill,
                invocation.input_payload,
            )
        except (ValidationError, UnsafeSkillError) as error:
            raise GraphSubAgentFailure("graph sub-agent input contract is invalid") from error
        self._validate_skill_tool_boundary(skill, invocation.graph_node)
        input_payload = validated_input.model_dump(mode="json")
        input_hash = _safe_hash(input_payload)
        completed = await self._repository.find_completed_invocation(
            graph_run_id=invocation.graph_run_id,
            cursor=invocation.graph_cursor,
            action_id=invocation.action_id,
            skill_name=skill.name,
            input_hash=input_hash,
        )
        if completed is not None:
            output = self._skills.validate_output(skill, completed.output_payload)
            if result_validator is not None:
                output = result_validator(output)
            return GraphSkillRunResult(
                output=output,
                invocation_id=completed.id,
                attempt_count=completed.attempt,
            )
        last_error: Exception | None = None
        total_attempts = self._max_retries + 1
        start_attempt = await self._repository.prepare_invocation_resume(
            graph_run_id=invocation.graph_run_id,
            cursor=invocation.graph_cursor,
            action_id=invocation.action_id,
            skill_name=skill.name,
            input_hash=input_hash,
        )

        for attempt in range(start_attempt, total_attempts + 1):
            record = await self._repository.record_invocation(
                graph_run_id=invocation.graph_run_id,
                cursor=invocation.graph_cursor,
                action_id=invocation.action_id,
                evidence_manifest_id=invocation.evidence_manifest_id,
                execution_mode="skill_model",
                skill_name=skill.name,
                skill_version=skill.version,
                schema_version=skill.output_schema,
                attempt=attempt,
                status="running",
                input_hash=input_hash,
                output_hash=_safe_hash({}),
                model_provenance={},
            )
            try:
                output, provenance = await self._run_attempt(
                    invocation,
                    record.id,
                    skill,
                    input_payload,
                )
                if result_validator is not None:
                    output = result_validator(output)
                await self._repository.finalize_invocation(
                    record.id,
                    status="completed",
                    output_hash=_safe_hash(output.model_dump(mode="json")),
                    output_payload=output.model_dump(mode="json"),
                    model_provenance=provenance,
                )
                return GraphSkillRunResult(
                    output=output,
                    invocation_id=record.id,
                    attempt_count=attempt,
                )
            except Exception as error:
                last_error = error
                await self._repository.finalize_invocation(
                    record.id,
                    status="failed",
                    output_hash=_safe_hash({"safe_error_code": _safe_error_code(error)}),
                    model_provenance={
                        "safe_error_code": _safe_error_code(error),
                        "attempt": attempt,
                        "request_ids": [],
                    },
                )

        raise GraphSubAgentFailure(
            f"graph sub-agent failed after four attempts: {skill.name}"
        ) from last_error

    async def _run_attempt(
        self,
        invocation: GraphSkillInvocation,
        invocation_id: UUID,
        skill: SkillDefinition,
        input_payload: dict[str, Any],
    ) -> tuple[BaseModel, dict[str, Any]]:
        messages = _initial_messages(skill, invocation, input_payload)
        request_ids: list[str] = []
        input_tokens = 0
        output_tokens = 0
        provider = "unavailable"
        model = "unavailable"
        allowed_tools = frozenset(skill.allowed_tools)
        context = GraphToolContext(
            operator_id=self._operator.operator_id,
            tenant_id=self._operator.tenant_id,
            task_id=invocation.task_id,
            run_id=invocation.run_id,
            graph_run_id=invocation.graph_run_id,
            graph_node=invocation.graph_node,
            graph_cursor=invocation.graph_cursor,
            action_id=invocation.action_id,
            evidence_manifest_id=invocation.evidence_manifest_id,
            invocation_id=invocation_id,
            allowed_tools=allowed_tools,
        )
        tool_calls = 0

        while True:
            response = await self._provider.complete_json_once(
                LLMRequest(
                    messages=tuple(messages),
                    response_schema=_response_schema(skill, self._skills),
                )
            )
            provider = response.provider
            model = response.model
            input_tokens += response.usage.input_tokens
            output_tokens += response.usage.output_tokens
            if response.request_id:
                request_ids.append(response.request_id)
            result = extract_model_result(response.output)
            tool_call = result.get("tool_call")
            if tool_call is None:
                output = self._skills.validate_output(skill, result)
                return output, {
                    "provider": provider,
                    "model": model,
                    "request_ids": request_ids,
                    "tool_call_count": tool_calls,
                    "usage": {
                        "input_tokens": input_tokens,
                        "output_tokens": output_tokens,
                    },
                }
            if not isinstance(tool_call, dict):
                raise ValueError("graph sub-agent tool_call must be an object")
            name = tool_call.get("name")
            arguments = tool_call.get("arguments")
            if not isinstance(name, str) or not isinstance(arguments, dict):
                raise ValueError("graph sub-agent tool call is invalid")
            if name not in allowed_tools:
                raise GraphToolAuthorizationError("Skill did not authorize phase tool")
            tool_calls += 1
            if tool_calls > self._max_tool_calls:
                raise ValueError("graph sub-agent tool-call limit exceeded")
            tool_result = await self._tool_gateway.call(
                name,
                context=context,
                arguments=arguments,
                resource_id=_optional_argument(arguments, "resource_id"),
                evidence_ref=_optional_argument(arguments, "evidence_ref"),
                sensitive_token=_optional_argument(arguments, "sensitive_token"),
            )
            messages.extend(
                (
                    Message(
                        role="assistant",
                        content=json.dumps(
                            response.output,
                            ensure_ascii=False,
                            sort_keys=True,
                            default=str,
                        ),
                    ),
                    Message(
                        role="user",
                        content=json.dumps(
                            {
                                "authorized_tool_result": tool_result.payload,
                                "trace_id": tool_result.trace_id,
                            },
                            ensure_ascii=False,
                            sort_keys=True,
                            default=str,
                        ),
                    ),
                )
            )

    @staticmethod
    def _validate_skill_tool_boundary(
        skill: SkillDefinition,
        graph_node: str,
    ) -> None:
        node_tools = GRAPH_NODE_TOOL_NAMES.get(graph_node, frozenset())
        unknown = set(skill.allowed_tools).difference(node_tools)
        if unknown:
            raise GraphSubAgentFailure(
                f"Skill tools are outside graph node boundary: {skill.name}"
            )


def _initial_messages(
    skill: SkillDefinition,
    invocation: GraphSkillInvocation,
    input_payload: dict[str, Any],
) -> list[Message]:
    return [
        Message(
            role="system",
            content=(
                f"{render_agent_system_prompt((skill,))}\n\n"
                f"## 当前绑定\nSkill={skill.name}@{skill.version}; "
                f"graph_node={invocation.graph_node}; action_id={invocation.action_id}。\n"
                "你不能直接访问输入源。需要证据时，只能返回一个允许工具调用；"
                "获得工具结果后再继续。最终只返回 result 中符合输出 schema 的对象。"
            ),
        ),
        Message(
            role="user",
            content=json.dumps(
                {
                    "evidence_manifest_id": str(invocation.evidence_manifest_id),
                    "bounded_input_contract": input_payload,
                },
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            ),
        ),
    ]


def _response_schema(
    skill: SkillDefinition,
    registry: SkillRegistry,
) -> dict[str, Any]:
    from app.ai.skills.contracts import AGENT_SKILL_SCHEMAS

    del registry
    model_type = AGENT_SKILL_SCHEMAS[skill.output_schema]
    result_schema = model_type.model_json_schema()
    definitions = result_schema.pop("$defs", {})
    tool_options = [
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "tool_call": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "name": {"type": "string", "const": name},
                        "arguments": _tool_arguments_schema(name),
                    },
                    "required": ["name", "arguments"],
                }
            },
            "required": ["tool_call"],
        }
        for name in skill.allowed_tools
    ]
    return {
        "$defs": definitions,
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "result": {
                "anyOf": [result_schema, *tool_options],
            }
        },
        "required": ["result"],
    }


def _tool_arguments_schema(name: str) -> dict[str, Any]:
    if name in {
        "inspect_configured_source",
        "read_connector_page",
        "read_work_item",
        "query_identity_postings",
        "read_claim_state",
    }:
        properties: dict[str, Any] = {
            "resource_id": {"type": "string", "minLength": 1},
        }
        if name == "read_connector_page":
            properties["page_locator"] = {"type": ["string", "null"]}
            properties["limit"] = {"type": "integer", "minimum": 1, "maximum": 50}
        return {
            "type": "object",
            "additionalProperties": False,
            "properties": properties,
            "required": ["resource_id"],
        }
    if name == "read_paired_record_evidence":
        return {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "evidence_ref": {"type": "string", "minLength": 1},
            },
            "required": ["evidence_ref"],
        }
    if name in {
        "submit_input_contract_verdict",
        "submit_normalized_batch",
        "submit_input_marks",
        "submit_finding_batch",
    }:
        return {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "resource_id": {"type": "string", "minLength": 1},
                "submission": {"type": "object"},
            },
            "required": ["resource_id", "submission"],
        }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "resource_id": {"type": "string", "minLength": 1},
            "evidence_ref": {"type": "string", "minLength": 1},
        },
        "minProperties": 1,
    }


def _optional_argument(arguments: Mapping[str, object], key: str) -> str | None:
    value = arguments.get(key)
    return value if isinstance(value, str) else None


def _safe_hash(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode()
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _safe_error_code(error: Exception) -> str:
    if isinstance(error, ModelProviderError):
        return "model_provider_failure"
    if isinstance(error, GraphToolAuthorizationError):
        return "tool_authorization_failure"
    if isinstance(error, (ValidationError, UnsafeSkillError)):
        return "model_contract_failure"
    return "model_output_failure"
