from datetime import UTC, datetime
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.ai.mcp.authorization import ToolAuthorizationError, ToolContext
from app.ai.mcp.server import ToolResult
from app.ai.prompting import (
    PROMPT_VERSION,
    PROMPT_VERSION_V2,
    PROMPT_VERSION_V3,
    build_messages,
    response_schema,
    tool_messages,
)
from app.ai.providers.base import LLMProvider, LLMRequest, ModelProviderError, ModelUsage
from app.ai.skills.registry import SkillRegistry
from app.ai.tokenization import TaskTokenizationContext, UnknownTokenError
from app.schemas.governance import (
    AnalysisProvenance,
    CauseAnalysis,
    CauseAnalysisV2,
    CauseAnalysisV3,
)


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

    async def close_read_transaction(self) -> None: ...


class AgentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    skill_name: str = Field(min_length=1, max_length=128)
    skill_version: str = Field(min_length=1, max_length=64)
    input_payload: dict[str, Any]
    tool_context: ToolContext | None = None
    analysis_version: Literal["analysis-v1", "analysis-v2", "analysis-v3"] = "analysis-v1"


class AgentResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    output: CauseAnalysis | CauseAnalysisV2 | CauseAnalysisV3
    provenance: AnalysisProvenance


class GovernanceAgent:
    def __init__(
        self,
        llm: LLMProvider,
        tools: ToolGateway,
        *,
        skills: SkillRegistry | None = None,
        max_tool_calls: int = 4,
        tokenization_secret: str | None = None,
    ) -> None:
        self.llm = llm
        self.tools = tools
        self.skills = skills or SkillRegistry()
        self.max_tool_calls = max_tool_calls
        self.tokenization_secret = tokenization_secret

    async def analyze(self, request: AgentRequest) -> AgentResult:
        skill = self.skills.load(request.skill_name, request.skill_version)
        tokenizer = self._tokenizer(request)
        safe_payload = (
            tokenizer.tokenize(request.input_payload) if tokenizer else request.input_payload
        )
        messages = build_messages(skill, safe_payload)
        output_model: type[CauseAnalysis] | type[CauseAnalysisV2] | type[CauseAnalysisV3]
        if request.analysis_version == "analysis-v3":
            output_model = CauseAnalysisV3
            prompt_version = PROMPT_VERSION_V3
        elif request.analysis_version == "analysis-v2":
            output_model = CauseAnalysisV2
            prompt_version = PROMPT_VERSION_V2
        else:
            output_model = CauseAnalysis
            prompt_version = PROMPT_VERSION
        trace_ids: list[str] = []
        gateway_request_ids: list[str] = []
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
                            output_model.model_json_schema(),
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
                        prompt_version,
                        gateway_request_ids=gateway_request_ids,
                    ),
                    cause=error,
                ) from error
            provider = response.provider
            model = response.model
            input_tokens += response.usage.input_tokens
            output_tokens += response.usage.output_tokens
            if response.request_id is not None:
                gateway_request_ids.append(response.request_id)
            provenance = _provenance(
                provider,
                model,
                skill.name,
                skill.version,
                trace_ids,
                input_tokens,
                output_tokens,
                prompt_version,
                gateway_request_ids=gateway_request_ids,
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
                if tokenizer is not None:
                    try:
                        arguments = tokenizer.detokenize(arguments)
                    except UnknownTokenError as error:
                        raise InvalidAgentOutput(
                            str(error), provenance=provenance, cause=error
                        ) from error
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
                finally:
                    await self.tools.close_read_transaction()
                trace_ids.append(tool_result.trace_id)
                safe_tool_payload = (
                    tokenizer.tokenize(tool_result.payload)
                    if tokenizer is not None
                    else tool_result.payload
                )
                messages.extend(tool_messages(response.output, safe_tool_payload))
                continue

            if tokenizer is not None:
                try:
                    result_payload = tokenizer.detokenize(result_payload)
                except UnknownTokenError as error:
                    raise InvalidAgentOutput(
                        str(error), provenance=provenance, cause=error
                    ) from error
            try:
                output = output_model.model_validate(result_payload)
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
                    prompt_version,
                    gateway_request_ids=gateway_request_ids,
                ),
            )

    def _tokenizer(self, request: AgentRequest) -> TaskTokenizationContext | None:
        requires_tokenization = bool(getattr(self.llm, "requires_tokenization", False))
        if self.tokenization_secret is None:
            if requires_tokenization:
                provenance = _provenance(
                    "unavailable",
                    "unavailable",
                    request.skill_name,
                    request.skill_version,
                    [],
                    0,
                    0,
                    _prompt_version(request.analysis_version),
                )
                error = ModelProviderError("LLM tokenization is not configured")
                raise AgentProviderFailure(str(error), provenance=provenance, cause=error)
            return None
        if request.tool_context is None:
            raise ValueError("tokenized Agent requests require authorized task context")
        return TaskTokenizationContext(
            secret=self.tokenization_secret,
            tenant_id=request.tool_context.tenant_id,
            task_id=request.tool_context.task_id,
        )


def _prompt_version(analysis_version: str) -> str:
    if analysis_version == "analysis-v3":
        return PROMPT_VERSION_V3
    if analysis_version == "analysis-v2":
        return PROMPT_VERSION_V2
    return PROMPT_VERSION


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
    prompt_version: str = PROMPT_VERSION,
    gateway_request_ids: list[str] | None = None,
) -> AnalysisProvenance:
    return AnalysisProvenance(
        provider=provider,
        model=model,
        skill_name=skill_name,
        skill_version=skill_version,
        prompt_version=prompt_version,
        tool_trace_ids=tuple(trace_ids),
        gateway_request_ids=tuple(gateway_request_ids or ()),
        usage=ModelUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        ),
        generated_at=datetime.now(UTC),
    )
