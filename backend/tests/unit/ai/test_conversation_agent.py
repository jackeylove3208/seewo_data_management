import json
from uuid import uuid4

import pytest

from app.ai.conversation_agent import (
    ConversationAgentContext,
    ConversationSupervisorAgent,
)
from app.ai.providers.base import LLMRequest, LLMResponse


class CapturingProvider:
    def __init__(self, output: dict[str, object]) -> None:
        self.output = output
        self.requests: list[LLMRequest] = []

    async def complete_json_once(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        return LLMResponse(output=self.output, provider="stub", model="stub")


class SequencedProvider:
    def __init__(self, outputs: list[dict[str, object]]) -> None:
        self.outputs = outputs
        self.requests: list[LLMRequest] = []

    async def complete_json_once(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        return LLMResponse(
            output=self.outputs[len(self.requests) - 1],
            provider="stub",
            model="stub",
        )


def _context(**overrides: object) -> ConversationAgentContext:
    values: dict[str, object] = {
        "conversation_id": uuid4(),
        "tenant_id": "school-1",
        "message": "同步七年级学生数据",
        "available_source_refs": ("third-party/roster.csv", "seewo/roster.csv"),
    }
    values.update(overrides)
    return ConversationAgentContext.model_validate(values)


@pytest.mark.asyncio
async def test_supervisor_uses_versioned_skill_and_returns_confirmation() -> None:
    provider = CapturingProvider(
        {
            "result": {
                "kind": "start_confirmation",
                "title": "七年级学生同步",
                "entity_types": ["student"],
                "source_ref": "third-party/roster.csv",
                "target_ref": "seewo/roster.csv",
                "message_zh": "已确认第三方和希沃数据来源。",
            }
        }
    )

    decision = await ConversationSupervisorAgent(provider).reply(_context())

    assert decision.kind == "start_confirmation"
    assert decision.source_ref == "third-party/roster.csv"
    assert "converse-school-data-sync@1.5.0" in provider.requests[0].messages[0].content
    assert "不可信证据" in provider.requests[0].messages[0].content


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.asyncio
async def test_supervisor_exposes_remote_csv_capability_state(enabled: bool) -> None:
    provider = CapturingProvider(
        {
            "result": {
                "kind": "clarification",
                "message_zh": "已根据当前部署能力说明可用的数据接入方式。",
            }
        }
    )

    await ConversationSupervisorAgent(provider).reply(
        _context(conversation_remote_csv_enabled=enabled)
    )

    evidence = json.loads(provider.requests[0].messages[1].content)[
        "untrusted_evidence"
    ]
    assert evidence["conversation_remote_csv_enabled"] is enabled


@pytest.mark.asyncio
async def test_supervisor_rejects_source_not_returned_by_server_discovery() -> None:
    provider = CapturingProvider(
        {
            "result": {
                "kind": "start_confirmation",
                "title": "同步",
                "entity_types": ["student"],
                "source_ref": "../../.env",
                "target_ref": "seewo/roster.csv",
                "message_zh": "已确认。",
            }
        }
    )

    decision = await ConversationSupervisorAgent(provider).reply(_context())

    assert decision.kind == "clarification"
    assert decision.source_ref is None


@pytest.mark.asyncio
async def test_supervisor_accepts_server_listed_remote_source_with_local_target() -> None:
    remote_source_id = uuid4()
    provider = CapturingProvider(
        {
            "result": {
                "kind": "start_confirmation",
                "title": "远程学生同步",
                "entity_types": ["student"],
                "remote_source_id": str(remote_source_id),
                "target_ref": "seewo/roster.csv",
                "message_zh": "已确认远程权威来源和希沃目标。",
            }
        }
    )

    decision = await ConversationSupervisorAgent(provider).reply(
        _context(
            message="请同步 [远程CSV来源:data.example.test]",
            conversation_remote_csv_enabled=True,
            available_remote_sources=(
                {
                    "remote_source_id": remote_source_id,
                    "display_origin": "data.example.test",
                },
            ),
        )
    )

    assert decision.kind == "start_confirmation"
    assert decision.remote_source_id == remote_source_id
    assert decision.target_ref == "seewo/roster.csv"


@pytest.mark.asyncio
async def test_supervisor_accepts_model_selected_remote_link_boundary() -> None:
    provider = CapturingProvider(
        {
            "result": {
                "kind": "start_confirmation",
                "title": "远程学生同步",
                "entity_types": ["student"],
                "remote_url_start": 3,
                "remote_url_end": 46,
                "target_ref": "seewo/roster.csv",
                "message_zh": "已确认链接边界和希沃目标。",
            }
        }
    )

    decision = await ConversationSupervisorAgent(provider).reply(
        _context(
            message="请同步 [待识别远程CSV链接] 的学生",
            conversation_remote_csv_enabled=True,
            remote_link_candidates=(
                {
                    "start": 3,
                    "end": 46,
                    "display_url": "https://data.example.test/roster.csv",
                    "trailing_text": "的数据",
                },
            ),
        )
    )

    assert decision.kind == "start_confirmation"
    assert decision.remote_url_start == 3
    assert decision.remote_url_end == 46


@pytest.mark.asyncio
async def test_supervisor_defaults_remote_csv_authority_to_seewo_data_mysql() -> None:
    provider = CapturingProvider(
        {
            "result": {
                "kind": "start_confirmation",
                "title": "远程学生同步",
                "entity_types": ["student"],
                "remote_url_start": 3,
                "remote_url_end": 46,
                "message_zh": "已确认远程学生 CSV。",
            }
        }
    )

    decision = await ConversationSupervisorAgent(provider).reply(
        _context(
            conversation_remote_csv_enabled=True,
            remote_link_candidates=(
                {
                    "start": 3,
                    "end": 46,
                    "display_url": "https://data.example.test/roster.csv",
                    "trailing_text": "的数据",
                },
            ),
            available_database_connectors=(
                {
                    "connector_id": "seewo-data-mysql",
                    "dialect": "mysql",
                    "source_role": "target",
                },
            ),
        )
    )

    assert decision.kind == "start_confirmation"
    assert decision.target_configuration_id == "seewo-data-mysql"


@pytest.mark.asyncio
async def test_supervisor_defaults_remote_csv_at_message_start_to_seewo_data_mysql() -> None:
    provider = CapturingProvider(
        {
            "result": {
                "kind": "start_confirmation",
                "title": "远程学生同步",
                "entity_types": ["student"],
                "remote_url_start": 0,
                "remote_url_end": 43,
                "message_zh": "已确认远程学生 CSV。",
            }
        }
    )

    decision = await ConversationSupervisorAgent(provider).reply(
        _context(
            conversation_remote_csv_enabled=True,
            remote_link_candidates=(
                {
                    "start": 0,
                    "end": 43,
                    "display_url": "https://data.example.test/roster.csv",
                    "trailing_text": "",
                },
            ),
            available_database_connectors=(
                {
                    "connector_id": "seewo-data-mysql",
                    "dialect": "mysql",
                    "source_role": "target",
                },
            ),
        )
    )

    assert decision.kind == "start_confirmation"
    assert decision.target_configuration_id == "seewo-data-mysql"


@pytest.mark.asyncio
async def test_supervisor_rejects_unlisted_remote_link_boundary() -> None:
    provider = CapturingProvider(
        {
            "result": {
                "kind": "start_confirmation",
                "title": "越界远程同步",
                "entity_types": ["student"],
                "remote_url_start": 4,
                "remote_url_end": 47,
                "target_ref": "seewo/roster.csv",
                "message_zh": "已确认。",
            }
        }
    )

    decision = await ConversationSupervisorAgent(provider).reply(
        _context(
            conversation_remote_csv_enabled=True,
            remote_link_candidates=(
                {
                    "start": 3,
                    "end": 46,
                    "display_url": "https://data.example.test/roster.csv",
                    "trailing_text": "的数据",
                },
            ),
        )
    )

    assert decision.kind == "clarification"
    assert decision.remote_url_start is None
    assert decision.remote_url_end is None


@pytest.mark.asyncio
async def test_supervisor_requires_model_to_select_one_remote_link_boundary() -> None:
    provider = CapturingProvider(
        {
            "result": {
                "kind": "start_confirmation",
                "title": "缺少链接边界",
                "entity_types": ["student"],
                "target_ref": "seewo/roster.csv",
                "message_zh": "已确认。",
            }
        }
    )

    decision = await ConversationSupervisorAgent(provider).reply(
        _context(
            conversation_remote_csv_enabled=True,
            remote_link_candidates=(
                {
                    "start": 3,
                    "end": 46,
                    "display_url": "https://data.example.test/roster.csv",
                    "trailing_text": "的数据",
                },
            ),
        )
    )

    assert decision.kind == "clarification"
    assert "链接边界" in decision.message_zh


@pytest.mark.asyncio
async def test_supervisor_rejects_remote_source_when_capability_is_disabled() -> None:
    remote_source_id = uuid4()
    provider = CapturingProvider(
        {
            "result": {
                "kind": "start_confirmation",
                "title": "不应启动的远程同步",
                "entity_types": ["student"],
                "remote_source_id": str(remote_source_id),
                "target_ref": "seewo/roster.csv",
                "message_zh": "已确认。",
            }
        }
    )

    decision = await ConversationSupervisorAgent(provider).reply(
        _context(
            conversation_remote_csv_enabled=False,
            available_remote_sources=(
                {
                    "remote_source_id": remote_source_id,
                    "display_origin": "data.example.test",
                },
            ),
        )
    )

    assert decision.kind == "clarification"
    assert decision.remote_source_id is None
    assert decision.message_zh == (
        "当前部署未启用对话远程 CSV 接入，不能使用远程链接作为数据来源。"
    )


@pytest.mark.asyncio
async def test_supervisor_rejects_remote_source_not_listed_for_conversation() -> None:
    provider = CapturingProvider(
        {
            "result": {
                "kind": "start_confirmation",
                "title": "越权远程同步",
                "entity_types": ["student"],
                "remote_source_id": str(uuid4()),
                "target_ref": "seewo/roster.csv",
                "message_zh": "已确认。",
            }
        }
    )

    decision = await ConversationSupervisorAgent(provider).reply(_context())

    assert decision.kind == "clarification"
    assert decision.remote_source_id is None


@pytest.mark.asyncio
async def test_supervisor_accepts_server_listed_postgresql_to_mysql_pair() -> None:
    provider = CapturingProvider(
        {
            "result": {
                "kind": "start_confirmation",
                "title": "SQL 全校同步",
                "entity_types": ["department", "student", "teacher"],
                "source_configuration_id": "authority-postgres",
                "target_configuration_id": "seewo-mysql",
                "message_zh": "已确认 PostgreSQL 权威来源和 MySQL 希沃目标。",
            }
        }
    )

    decision = await ConversationSupervisorAgent(provider).reply(
        _context(
            available_source_refs=(),
            available_database_connectors=(
                {
                    "connector_id": "authority-postgres",
                    "dialect": "postgresql",
                    "source_role": "authoritative",
                },
                {
                    "connector_id": "seewo-mysql",
                    "dialect": "mysql",
                    "source_role": "target",
                },
            ),
        )
    )

    assert decision.kind == "start_confirmation"
    assert decision.source_configuration_id == "authority-postgres"
    assert decision.target_configuration_id == "seewo-mysql"


@pytest.mark.asyncio
async def test_supervisor_accepts_eligible_api_authority_with_mysql_target() -> None:
    connection_id = uuid4()
    provider = CapturingProvider(
        {
            "result": {
                "kind": "start_confirmation",
                "title": "钉钉教师同步",
                "entity_types": ["teacher"],
                "source_api_connection_id": str(connection_id),
                "target_configuration_id": "seewo-mysql",
                "message_zh": "已确认钉钉只读权威来源和 MySQL 希沃目标。",
            }
        }
    )

    decision = await ConversationSupervisorAgent(provider).reply(
        _context(
            available_source_refs=(),
            available_api_connections=(
                {
                    "connection_id": connection_id,
                    "provider_id": "dingtalk",
                    "display_name": "学校钉钉",
                    "state": "active",
                    "capabilities": {"entity.teacher.read": True},
                    "visibility_summary": {
                        "visible": True,
                        "teacher_count": 5,
                    },
                },
            ),
            available_database_connectors=(
                {
                    "connector_id": "seewo-mysql",
                    "dialect": "mysql",
                    "source_role": "target",
                },
            ),
        )
    )

    assert decision.kind == "start_confirmation"
    assert decision.source_api_connection_id == connection_id
    assert decision.target_configuration_id == "seewo-mysql"


@pytest.mark.asyncio
async def test_supervisor_defaults_unspecified_target_to_seewo_data_mysql() -> None:
    connection_id = uuid4()
    provider = CapturingProvider(
        {
            "result": {
                "kind": "start_confirmation",
                "title": "钉钉学生同步",
                "entity_types": ["student"],
                "source_api_connection_id": str(connection_id),
                "message_zh": "已确认钉钉学生同步。",
            }
        }
    )

    decision = await ConversationSupervisorAgent(provider).reply(
        _context(
            available_source_refs=(),
            available_api_connections=(
                {
                    "connection_id": connection_id,
                    "provider_id": "dingtalk",
                    "display_name": "学校钉钉",
                    "state": "active",
                    "capabilities": {"entity.student.read": True},
                    "visibility_summary": {
                        "visible": True,
                        "student_count": 3,
                    },
                },
            ),
            available_database_connectors=(
                {
                    "connector_id": "seewo-mysql",
                    "dialect": "mysql",
                    "source_role": "target",
                },
                {
                    "connector_id": "seewo-data-mysql",
                    "dialect": "mysql",
                    "source_role": "target",
                },
            ),
        )
    )

    assert decision.kind == "start_confirmation"
    assert decision.target_configuration_id == "seewo-data-mysql"


@pytest.mark.asyncio
async def test_supervisor_does_not_replace_explicit_target_from_current_intent() -> None:
    provider = CapturingProvider(
        {
            "result": {
                "kind": "start_confirmation",
                "title": "学生同步",
                "entity_types": ["student"],
                "source_configuration_id": "dingtalk-students",
                "message_zh": "沿用已选目标。",
            }
        }
    )

    decision = await ConversationSupervisorAgent(provider).reply(
        _context(
            current_intent={
                "target": {
                    "kind": "database",
                    "configuration_id": "seewo-archive-mysql",
                }
            },
            available_database_connectors=(
                {
                    "connector_id": "dingtalk-students",
                    "dialect": "mysql",
                    "source_role": "authoritative",
                },
                {
                    "connector_id": "seewo-data-mysql",
                    "dialect": "mysql",
                    "source_role": "target",
                },
                {
                    "connector_id": "seewo-archive-mysql",
                    "dialect": "mysql",
                    "source_role": "target",
                },
            ),
        )
    )

    assert decision.target_configuration_id is None


@pytest.mark.asyncio
async def test_supervisor_defaults_local_csv_authority_to_seewo_data_mysql() -> None:
    provider = CapturingProvider(
        {
            "result": {
                "kind": "start_confirmation",
                "title": "CSV 学生同步",
                "entity_types": ["student"],
                "source_ref": "third-party/roster.csv",
                "message_zh": "已确认 CSV 学生同步。",
            }
        }
    )

    decision = await ConversationSupervisorAgent(provider).reply(
        _context(
            available_database_connectors=(
                {
                    "connector_id": "seewo-data-mysql",
                    "dialect": "mysql",
                    "source_role": "target",
                },
            ),
        )
    )

    assert decision.kind == "start_confirmation"
    assert decision.source_ref == "third-party/roster.csv"
    assert decision.target_configuration_id == "seewo-data-mysql"


@pytest.mark.asyncio
async def test_supervisor_retries_invalid_model_output_with_validation_feedback() -> None:
    provider = SequencedProvider(
        [
            {"result": {"kind": "start_confirmation"}},
            {
                "result": {
                    "kind": "clarification",
                    "message_zh": "请告诉我需要同步的数据来源。",
                }
            },
        ]
    )

    decision = await ConversationSupervisorAgent(provider).reply(
        _context(available_source_refs=())
    )

    assert decision.kind == "clarification"
    assert len(provider.requests) == 2
    assert provider.requests[1] != provider.requests[0]
    repair_feedback = json.loads(provider.requests[1].messages[-1].content)
    assert "上一份 JSON 未通过服务端合同" in repair_feedback["instruction"]
    assert repair_feedback["validation_errors"]


@pytest.mark.asyncio
async def test_supervisor_reports_unknown_mysql_target_without_blaming_api_permissions() -> None:
    connection_id = uuid4()
    provider = CapturingProvider(
        {
            "result": {
                "kind": "start_confirmation",
                "title": "钉钉学生同步",
                "entity_types": ["student"],
                "source_api_connection_id": str(connection_id),
                "target_configuration_id": "seewo_data",
                "message_zh": "已确认。",
            }
        }
    )

    decision = await ConversationSupervisorAgent(provider).reply(
        _context(
            available_source_refs=(),
            available_api_connections=(
                {
                    "connection_id": connection_id,
                    "provider_id": "dingtalk",
                    "display_name": "学校钉钉",
                    "state": "active",
                    "capabilities": {"entity.student.read": True},
                    "visibility_summary": {
                        "visible": True,
                        "student_count": 3,
                    },
                },
            ),
            available_database_connectors=(
                {
                    "connector_id": "seewo-data-mysql",
                    "dialect": "mysql",
                    "source_role": "target",
                },
            ),
        )
    )

    assert decision.kind == "clarification"
    assert "MySQL 目标连接" in decision.message_zh
    assert "seewo-data-mysql" in decision.message_zh
    assert "权限或可见范围" not in decision.message_zh


@pytest.mark.asyncio
async def test_supervisor_can_request_safe_api_configuration_card() -> None:
    provider = CapturingProvider(
        {
            "result": {
                "kind": "api_configuration",
                "api_provider_id": "dingtalk",
                "message_zh": "需要先安全配置钉钉应用凭据。",
            }
        }
    )

    decision = await ConversationSupervisorAgent(provider).reply(
        _context(
            available_source_refs=(),
            available_api_providers=(
                {
                    "provider_id": "dingtalk",
                    "supported_entities": ["department", "student", "teacher"],
                    "required_secret_fields": ["app_key", "app_secret"],
                },
            ),
        )
    )

    assert decision.kind == "api_configuration"
    assert decision.api_provider_id == "dingtalk"


@pytest.mark.asyncio
async def test_supervisor_rejects_api_connection_without_selected_visibility() -> None:
    connection_id = uuid4()
    provider = CapturingProvider(
        {
            "result": {
                "kind": "start_confirmation",
                "title": "不可启动的钉钉学生同步",
                "entity_types": ["student"],
                "source_api_connection_id": str(connection_id),
                "target_configuration_id": "seewo-mysql",
                "message_zh": "已确认。",
            }
        }
    )

    decision = await ConversationSupervisorAgent(provider).reply(
        _context(
            available_source_refs=(),
            available_api_connections=(
                {
                    "connection_id": connection_id,
                    "provider_id": "dingtalk",
                    "display_name": "学校钉钉",
                    "state": "active",
                    "capabilities": {
                        "entity.teacher.read": True,
                        "entity.student.read": False,
                    },
                    "visibility_summary": {
                        "visible": True,
                        "teacher_count": 5,
                        "student_count": 0,
                    },
                },
            ),
            available_database_connectors=(
                {
                    "connector_id": "seewo-mysql",
                    "dialect": "mysql",
                    "source_role": "target",
                },
            ),
        )
    )

    assert decision.kind == "clarification"
    assert decision.source_api_connection_id is None
    assert "权限或可见范围" in decision.message_zh


@pytest.mark.asyncio
async def test_supervisor_accepts_csv_authority_with_mysql_target() -> None:
    provider = CapturingProvider(
        {
            "result": {
                "kind": "start_confirmation",
                "title": "混合来源",
                "entity_types": ["student"],
                "source_ref": "third-party/roster.csv",
                "target_configuration_id": "seewo-mysql",
                "message_zh": "已确认。",
            }
        }
    )

    decision = await ConversationSupervisorAgent(provider).reply(
        _context(
            available_database_connectors=(
                {
                    "connector_id": "seewo-mysql",
                    "dialect": "mysql",
                    "source_role": "target",
                },
            )
        )
    )

    assert decision.kind == "start_confirmation"
    assert decision.source_ref == "third-party/roster.csv"
    assert decision.target_configuration_id == "seewo-mysql"


@pytest.mark.asyncio
async def test_supervisor_accepts_flat_json_object_provider_response() -> None:
    provider = CapturingProvider(
        {
            "type": "clarification",
            "message_zh": "我是学校数据同步助手，可以帮助核对和治理组织数据。",
        }
    )

    decision = await ConversationSupervisorAgent(provider).reply(
        _context(message="你是谁", available_source_refs=())
    )

    assert decision.kind == "clarification"
    assert decision.message_zh.startswith("我是学校数据同步助手")


@pytest.mark.asyncio
async def test_supervisor_ignores_known_non_executable_missing_info_hint() -> None:
    provider = CapturingProvider(
        {
            "result": {
                "kind": "clarification",
                "message_zh": "我是学校数据同步助手，请告诉我需要同步哪些实体。",
                "missing_info": ["entity_types"],
            }
        }
    )

    decision = await ConversationSupervisorAgent(provider).reply(
        _context(message="你是谁", available_source_refs=())
    )

    assert decision.kind == "clarification"


@pytest.mark.asyncio
async def test_supervisor_sends_complete_ordered_history() -> None:
    provider = CapturingProvider(
        {
            "result": {
                "kind": "clarification",
                "message_zh": "我会沿用前文继续确认同步范围。",
            }
        }
    )
    context = _context(
        message="继续",
        history=(
            {
                "role": "user",
                "kind": "normal",
                "text": "我要同步学生",
            },
            {
                "role": "assistant",
                "kind": "normal",
                "text": "请选择第三方和希沃数据来源",
            },
            {
                "role": "user",
                "kind": "normal",
                "text": "继续",
            },
        ),
    )

    await ConversationSupervisorAgent(provider).reply(context)

    evidence = json.loads(provider.requests[0].messages[1].content)["untrusted_evidence"]
    assert evidence["history"] == [
        {
            "role": "user",
            "kind": "normal",
            "text": "我要同步学生",
        },
        {
            "role": "assistant",
            "kind": "normal",
            "text": "请选择第三方和希沃数据来源",
        },
        {
            "role": "user",
            "kind": "normal",
            "text": "继续",
        },
    ]


@pytest.mark.asyncio
async def test_supervisor_rejects_complete_history_over_budget() -> None:
    provider = CapturingProvider(
        {
            "result": {
                "kind": "clarification",
                "message_zh": "不应调用模型。",
            }
        }
    )
    context = _context(
        history=(
            {
                "role": "user",
                "kind": "normal",
                "text": "很长的历史消息" * 200,
            },
        ),
    )

    with pytest.raises(RuntimeError, match="conversation context exceeds configured budget"):
        await ConversationSupervisorAgent(
            provider,
            max_context_tokens=100,
            reserved_output_tokens=20,
        ).reply(context)

    assert provider.requests == []
