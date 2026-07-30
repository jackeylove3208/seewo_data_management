"""Model-backed supervisor for the user-facing synchronization conversation."""

from typing import Any

from pydantic import ValidationError

from app.ai.agent_analysis_service import SingleAttemptModelProvider
from app.ai.agent_prompting import build_agent_request
from app.ai.conversation_context import ensure_conversation_request_fits
from app.ai.skills.registry import SkillRegistry
from app.schemas.agent_conversation import (
    ConversationAgentContext,
    ConversationAgentDecision,
)


class ConversationModelResponseError(ValueError):
    """The provider response did not satisfy the public conversation contract."""


class ConversationSupervisorAgent:
    def __init__(
        self,
        provider: SingleAttemptModelProvider,
        skills: SkillRegistry | None = None,
        *,
        max_context_tokens: int = 65_536,
        reserved_output_tokens: int = 2_048,
    ) -> None:
        self._provider = provider
        self._skills = skills or SkillRegistry()
        self._max_context_tokens = max_context_tokens
        self._reserved_output_tokens = reserved_output_tokens

    async def reply(self, context: ConversationAgentContext) -> ConversationAgentDecision:
        skill = self._skills.load("converse-school-data-sync", "1.3.0")
        request = build_agent_request(
            skill,
            context.model_dump(mode="json"),
            ConversationAgentDecision,
        )
        ensure_conversation_request_fits(
            request,
            max_context_tokens=self._max_context_tokens,
            reserved_output_tokens=self._reserved_output_tokens,
        )
        response = await self._provider.complete_json_once(request)
        decision = _parse_decision(response.output)
        return _validate_source_references(decision, context)


def _parse_decision(output: dict[str, Any]) -> ConversationAgentDecision:
    payload = output.get("result", output)
    if not isinstance(payload, dict):
        raise ConversationModelResponseError("conversation model result must be an object")
    normalized = dict(payload)
    if "kind" not in normalized and "type" in normalized:
        normalized["kind"] = normalized.pop("type")
    # Some JSON-object providers add this non-executable clarification hint
    # despite the response schema. The public message already carries the
    # clarification, so discard only this explicitly known compatibility key.
    normalized.pop("missing_info", None)
    try:
        return ConversationAgentDecision.model_validate(normalized)
    except ValidationError as error:
        raise ConversationModelResponseError(
            "conversation model result failed validation"
        ) from error


def _validate_source_references(
    decision: ConversationAgentDecision,
    context: ConversationAgentContext,
) -> ConversationAgentDecision:
    api_decision = _validate_api_selection(decision, context)
    if api_decision is not None:
        return api_decision
    references = {value for value in context.available_source_refs}
    selected_boundary = (
        (decision.remote_url_start, decision.remote_url_end)
        if decision.remote_url_start is not None
        and decision.remote_url_end is not None
        else None
    )
    allowed_boundaries = {
        (candidate.start, candidate.end) for candidate in context.remote_link_candidates
    }
    if context.remote_link_candidates:
        if not context.conversation_remote_csv_enabled:
            return ConversationAgentDecision(
                kind="clarification",
                message_zh=(
                    "当前部署未启用对话远程 CSV 接入，不能使用远程链接作为数据来源。"
                ),
            )
        if selected_boundary is None:
            return ConversationAgentDecision(
                kind="clarification",
                message_zh="请先确认本条消息中的第三方 CSV 链接边界。",
            )
        mixed_remote_selection = bool(
            decision.remote_source_id
            or decision.source_ref
            or decision.source_configuration_id
            or decision.target_configuration_id
        )
        target_is_valid = (
            decision.target_ref is None or decision.target_ref in references
        )
        if (
            selected_boundary in allowed_boundaries
            and not mixed_remote_selection
            and target_is_valid
        ):
            return decision
        return ConversationAgentDecision(
            kind="clarification",
            message_zh="第三方 CSV 链接边界或希沃目标已变化，请重新发送链接。",
        )
    if selected_boundary is not None:
        return ConversationAgentDecision(
            kind="clarification",
            message_zh="当前消息没有可供确认的第三方 CSV 链接，请重新发送链接。",
        )
    remote_source_ids = {
        item.remote_source_id for item in context.available_remote_sources
    }
    if decision.remote_source_id is not None:
        if not context.conversation_remote_csv_enabled:
            return ConversationAgentDecision(
                kind="clarification",
                message_zh=(
                    "当前部署未启用对话远程 CSV 接入，不能使用远程链接作为数据来源。"
                ),
            )
        mixed_remote_selection = bool(
            decision.source_ref
            or decision.source_configuration_id
            or decision.target_configuration_id
        )
        target_is_valid = (
            decision.target_ref is None or decision.target_ref in references
        )
        if (
            not mixed_remote_selection
            and target_is_valid
            and decision.remote_source_id in remote_source_ids
        ):
            return decision
        return ConversationAgentDecision(
            kind="clarification",
            message_zh="远程数据来源已变化，请重新发送当前对话可用的第三方 CSV 链接。",
        )
    selected_local = {value for value in (decision.source_ref, decision.target_ref) if value}
    selected_database = {
        value
        for value in (
            decision.source_configuration_id,
            decision.target_configuration_id,
        )
        if value
    }
    if selected_local and selected_database:
        return ConversationAgentDecision(
            kind="clarification",
            message_zh=(
                "一次任务的双方数据源必须使用同一种模式，请选择 CSV 对 CSV 或 SQL 对 SQL。"
            ),
        )
    if selected_local:
        if selected_local <= references:
            return decision
        return ConversationAgentDecision(
            kind="clarification",
            message_zh="可用本地数据来源已变化，请从服务端列出的来源中重新确认。",
        )
    if selected_database:
        by_id = {item.connector_id: item for item in context.available_database_connectors}
        source = (
            by_id.get(decision.source_configuration_id)
            if decision.source_configuration_id is not None
            else None
        )
        target = (
            by_id.get(decision.target_configuration_id)
            if decision.target_configuration_id is not None
            else None
        )
        if (
            source is not None
            and target is not None
            and source.source_role == "authoritative"
            and target.source_role == "target"
            and source.connector_id != target.connector_id
        ):
            return decision
        return ConversationAgentDecision(
            kind="clarification",
            message_zh=("数据库来源配置已变化，请重新选择服务端列出的只读权威来源和可写希沃目标。"),
        )
    if not (
        decision.source_ref
        or decision.target_ref
        or decision.source_configuration_id
        or decision.target_configuration_id
    ):
        return decision
    return ConversationAgentDecision(
        kind="clarification",
        message_zh="可用本地数据来源已变化，请从服务端列出的来源中重新确认。",
    )


def _validate_api_selection(
    decision: ConversationAgentDecision,
    context: ConversationAgentContext,
) -> ConversationAgentDecision | None:
    if decision.api_provider_id is not None:
        provider_ids = {
            provider.provider_id for provider in context.available_api_providers
        }
        if (
            decision.kind == "api_configuration"
            and decision.api_provider_id in provider_ids
            and decision.source_api_connection_id is None
            and decision.source_configuration_id is None
            and decision.target_configuration_id is None
            and decision.source_ref is None
            and decision.target_ref is None
            and decision.remote_source_id is None
        ):
            return decision
        return ConversationAgentDecision(
            kind="clarification",
            message_zh="API 提供方清单已变化，请重新选择当前支持的连接类型。",
        )
    if decision.kind == "api_configuration":
        return ConversationAgentDecision(
            kind="clarification",
            message_zh="请先选择要配置的 API 提供方。",
        )
    if decision.source_api_connection_id is None:
        return None
    if (
        decision.source_configuration_id is not None
        or decision.source_ref is not None
        or decision.target_ref is not None
        or decision.remote_source_id is not None
        or decision.remote_url_start is not None
    ):
        return ConversationAgentDecision(
            kind="clarification",
            message_zh="API 权威来源只能与服务端列出的 MySQL 希沃目标配对。",
        )
    connection = next(
        (
            item
            for item in context.available_api_connections
            if item.connection_id == decision.source_api_connection_id
        ),
        None,
    )
    target = next(
        (
            item
            for item in context.available_database_connectors
            if item.connector_id == decision.target_configuration_id
        ),
        None,
    )
    eligible = (
        connection is not None
        and connection.state == "active"
        and connection.visibility_summary.get("visible") is True
        and all(
            connection.capabilities.get(f"entity.{entity.value}.read") is True
            and _positive_count(
                connection.visibility_summary.get(f"{entity.value}_count")
            )
            for entity in decision.entity_types
        )
    )
    if (
        eligible
        and target is not None
        and target.source_role == "target"
        and target.dialect == "mysql"
    ):
        return decision
    return ConversationAgentDecision(
        kind="clarification",
        message_zh="API 连接的权限或可见范围不足，请修正配置并重新测试。",
    )


def _positive_count(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0
