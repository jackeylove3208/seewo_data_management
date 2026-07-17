from datetime import UTC, datetime
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.ai.mcp.authorization import ToolAuthorizationError, ToolContext
from app.ai.mcp.server import ToolResult
from app.ai.prompting import (
    PROMPT_VERSION,
    build_messages,
    response_schema,
    tool_messages,
)
from app.ai.providers.base import LLMProvider, LLMRequest, ModelProviderError, ModelUsage
from app.ai.skills.registry import SkillRegistry
from app.schemas.governance import AnalysisProvenance, CauseAnalysis


class AgentFailure(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        provenance: AnalysisProvenance,
        cause: Exception | None = None,
    ) -> None:
        super().__init__(message)
        self.provenance = provenance
        self.cause = cause


class UnsafeToolCall(AgentFailure):
    pass


class ToolLimitExceeded(AgentFailure):
    pass


class InvalidAgentOutput(AgentFailure):
    pass


class AgentProviderFailure(AgentFailure):
    pass


class AgentToolFailure(AgentFailure):
    pass


class ToolGateway(Protocol):
    async def call(
        self,
        name: str,
        arguments: dict[str, Any],
        context: ToolContext,
    ) -> ToolResult: ...


class AgentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    skill_name: str = Field(min_length=1, max_length=128)
    skill_version: str = Field(min_length=1, max_length=64)
    input_payload: dict[str, Any]
    tool_context: ToolContext | None = None


class AgentResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    output: CauseAnalysis
    provenance: AnalysisProvenance


class GovernanceAgent:
    def __init__(
        self,
        llm: LLMProvider,
        tools: ToolGateway,
        *,
        skills: SkillRegistry | None = None,
        max_tool_calls: int = 4,
    ) -> None:
        self.llm = llm
        self.tools = tools
        self.skills = skills or SkillRegistry()
        self.max_tool_calls = max_tool_calls

    async def analyze(self, request: AgentRequest) -> AgentResult:
        skill = self.skills.load(request.skill_name, request.skill_version)
        messages = build_messages(skill, request.input_payload)
        trace_ids: list[str] = []
        input_tokens = 0
        output_tokens = 0
        provider = "unavailable"
        model = "unavailable"

        while True:
            try:
                response = await self.llm.complete_json(
                    LLMRequest(
                        messages=tuple(messages),
                        response_schema=response_schema(
                            skill,
                            CauseAnalysis.model_json_schema(),
                        ),
                    )
                )
            except ModelProviderError as error:
                raise AgentProviderFailure(
                    str(error),
                    provenance=_provenance(
                        provider,
                        model,
                        skill.name,
                        skill.version,
                        trace_ids,
                        input_tokens,
                        output_tokens,
                    ),
                    cause=error,
                ) from error
            provider = response.provider
            model = response.model
            input_tokens += response.usage.input_tokens
            output_tokens += response.usage.output_tokens
            provenance = _provenance(
                provider,
                model,
                skill.name,
                skill.version,
                trace_ids,
                input_tokens,
                output_tokens,
            )
            result_payload = response.output.get("result")
            if not isinstance(result_payload, dict):
                failure = ValueError("model response must contain an object result")
                raise InvalidAgentOutput(
                    str(failure),
                    provenance=provenance,
                    cause=failure,
                ) from failure
            try:
                tool_call = _parse_tool_call(result_payload)
            except ValueError as error:
                raise UnsafeToolCall(str(error), provenance=provenance, cause=error) from error
            if tool_call is not None:
                name, arguments = tool_call
                if name not in skill.allowed_tools:
                    raise UnsafeToolCall(name, provenance=provenance)
                if request.tool_context is None:
                    raise UnsafeToolCall(
                        f"{name} requires authorized tool context",
                        provenance=provenance,
                    )
                if len(trace_ids) >= self.max_tool_calls:
                    raise ToolLimitExceeded(str(self.max_tool_calls), provenance=provenance)
                try:
                    tool_result = await self.tools.call(name, arguments, request.tool_context)
                except (ToolAuthorizationError, ValueError) as error:
                    raise AgentToolFailure(
                        str(error), provenance=provenance, cause=error
                    ) from error
                trace_ids.append(tool_result.trace_id)
                messages.extend(tool_messages(response.output, tool_result.payload))
                continue

            try:
                output = CauseAnalysis.model_validate(result_payload)
            except ValidationError as error:
                raise InvalidAgentOutput(
                    "model output failed CauseAnalysis validation",
                    provenance=provenance,
                    cause=error,
                ) from error
            return AgentResult(
                output=output,
                provenance=_provenance(
                    provider,
                    model,
                    skill.name,
                    skill.version,
                    trace_ids,
                    input_tokens,
                    output_tokens,
                ),
            )


def _parse_tool_call(output: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    value = output.get("tool_call")
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("invalid tool call")
    name = value.get("name")
    arguments = value.get("arguments", {})
    if not isinstance(name, str) or not isinstance(arguments, dict):
        raise ValueError("invalid tool call")
    return name, arguments


def _provenance(
    provider: str,
    model: str,
    skill_name: str,
    skill_version: str,
    trace_ids: list[str],
    input_tokens: int,
    output_tokens: int,
) -> AnalysisProvenance:
    return AnalysisProvenance(
        provider=provider,
        model=model,
        skill_name=skill_name,
        skill_version=skill_version,
        prompt_version=PROMPT_VERSION,
        tool_trace_ids=tuple(trace_ids),
        usage=ModelUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        ),
        generated_at=datetime.now(UTC),
    )
