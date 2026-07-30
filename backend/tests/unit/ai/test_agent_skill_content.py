import pytest

from app.ai.agent_prompting import COMMON_AGENT_SAFETY_CONTRACT
from app.ai.prompting import build_messages
from app.ai.skills.contracts import (
    MAX_DATABASE_SCHEMA_MAPPING_INPUT_BYTES,
    AgentRollbackAssessment,
    DatabaseColumnProfile,
    DatabaseSchemaMappingInput,
    DatabaseSchemaMappingOutput,
    DatabaseSourceSchemaProfile,
    OperationOutcome,
)
from app.ai.skills.registry import SkillRegistry

NEW_AGENT_SKILLS = {
    "converse-school-data-sync": (
        "活动任务",
        "学校锁",
        "服务端来源清单",
        "完整聊天历史",
        "历史错误",
        "不得静默截断",
        "start_confirmation",
        "safe_failure",
    ),
    "inspect-external-data-source": (
        "CSV",
        "API",
        "数据库",
        "稳定顺序",
        "连接器配置错误",
    ),
    "normalize-organization-data-batch": (
        "部门",
        "学生",
        "老师",
        "编号",
        "电话",
        "邮箱",
        "第三方",
        "希沃",
    ),
    "map-csv-organization-schema": (
        "固定六字段",
        "陌生表头",
        "source_field_ref",
        "normalizer_id",
        "第三方",
        "希沃",
        "不得生成第七个字段",
    ),
    "understand-remote-organization-source": (
        "固定六字段",
        "已物化",
        "source_field_ref",
        "normalizer_id",
        "五十",
        "URL",
        "网络",
        "提示注入",
        "不得生成第七个字段",
    ),
    "understand-organization-database-schema": (
        "PostgreSQL",
        "MySQL",
        "固定六字段",
        "source_field_ref",
        "normalizer_id",
        "SQL 类型",
        "主键",
        "generated",
        "autoincrement",
        "版本列",
        "参数化 SQL",
        "不得创造第七个业务字段",
    ),
    "reconcile-entity-batch": (
        "PostgreSQL",
        "编号",
        "电话令牌",
        "邮箱",
        "身份冲突",
        "重复",
        "未认领",
        "正确数据",
        "五十",
    ),
    "generate-governance-solutions": (
        "一至三条",
        "恰好一条",
        "target_extra",
        "target_duplicate",
        "target_missing",
        "field_difference",
        "authority_invalid",
    ),
    "resolve-human-conflict-instruction": (
        "候选",
        "掩码",
        "重述",
        "二次确认",
        "其他任务",
    ),
    "execute-approved-governance-plan": (
        "服务端编译",
        "版本",
        "幂等",
        "依赖",
        "验证",
        "部分失败",
        "不得自动回滚",
    ),
    "generate-agent-governance-report": (
        "正常完成",
        "部分成功",
        "异常输入",
        "模型错误",
        "用户终止",
        "回滚",
        "执行事实",
    ),
    "assess-agent-rollback-impact": (
        "独立",
        "学校锁",
        "验证成功",
        "前后值",
        "actual current",
        "already_restored",
        "comparison_hash",
        "人工确认",
    ),
    "execute-approved-rollback": (
        "独立回滚任务",
        "补偿操作",
        "写入前",
        "comparison_hash",
        "already_restored",
        "conflict_skipped",
        "验证",
        "第三方",
    ),
}

NEW_AGENT_SKILL_VERSIONS = {
    "converse-school-data-sync": "1.3.0",
    "assess-agent-rollback-impact": "2.1.0",
    "execute-approved-rollback": "2.1.0",
}

LEGACY_SKILLS = {
    "analyze-data-difference",
    "assess-rollback-impact",
    "generate-governance-plan",
    "generate-governance-report",
}

REQUIRED_SECTIONS = (
    "## 身份与目标",
    "## 可信输入与证据边界",
    "## 执行流程",
    "## 决策规则",
    "## 输出要求",
    "## 禁止事项",
    "## 停止条件",
)


def test_every_new_agent_skill_is_a_complete_operating_procedure() -> None:
    registry = SkillRegistry()

    for name, required_terms in NEW_AGENT_SKILLS.items():
        instructions = registry.load(
            name,
            NEW_AGENT_SKILL_VERSIONS.get(name, "1.0.0"),
        ).instructions

        assert len(instructions) >= 1400, name
        for section in REQUIRED_SECTIONS:
            assert section in instructions, f"{name} missing {section}"
        for term in required_terms:
            assert term in instructions, f"{name} missing {term}"


def test_rollback_assessment_skill_uses_current_data_not_version_as_the_gate() -> None:
    skill = SkillRegistry().load("assess-agent-rollback-impact", "2.1.0")

    assert set(skill.allowed_tools) == {
        "read_verified_mutations",
        "read_restore_comparison_facts",
        "submit_restore_assessment",
    }
    for term in (
        "before",
        "after",
        "current",
        "safe_to_restore",
        "already_restored",
        "conflict",
        "comparison_hash",
        "版本 ID 不能作为",
        "只比较原操作影响的字段",
        "保留无关字段",
    ):
        assert term in skill.instructions


def test_remote_source_understanding_skill_has_only_bounded_read_tools() -> None:
    skill = SkillRegistry().load(
        "understand-remote-organization-source",
        "1.0.0",
    )

    assert set(skill.allowed_tools) == {
        "inspect_configured_source",
        "read_connector_page",
    }
    assert skill.input_schema == "CsvSchemaMappingInput"
    assert skill.output_schema == "CsvSchemaMappingOutput"


def test_database_schema_mapping_contract_carries_bounded_v3_metadata() -> None:
    column = DatabaseColumnProfile(
        source_field_ref="database-column:target:0",
        column_name="display_name",
        sql_type="varchar(255)",
        inferred_type="text",
        nullable=False,
        primary_key=False,
        generated=False,
        autoincrement=False,
        candidate_contract_fields=("name",),
    )
    profile = DatabaseSourceSchemaProfile(
        source_role="target",
        connector_id="seewo-data-mysql",
        dialect="mysql",
        relation_ref="database-relation:target:data",
        stable_key_ref="database-column:target:1",
        version_ref="database-column:target:2",
        columns=(column,),
    )
    output = DatabaseSchemaMappingOutput(
        schema_version="fixed-six-field-sql-mapping-v3",
        authoritative_mappings=(),
        target_mappings=(),
        unresolved_required_fields=(
            "authoritative.category",
            "authoritative.name",
            "authoritative.number",
            "authoritative.class_name",
            "authoritative.phone",
            "authoritative.email",
            "target.category",
            "target.name",
            "target.number",
            "target.class_name",
            "target.phone",
            "target.email",
        ),
    )

    assert profile.version_ref == "database-column:target:2"
    assert profile.columns[0].sql_type == "varchar(255)"
    assert output.schema_version == "fixed-six-field-sql-mapping-v3"


def test_database_schema_mapping_input_accepts_one_database_role() -> None:
    profile = DatabaseSourceSchemaProfile(
        source_role="target",
        connector_id="seewo-data-mysql",
        dialect="mysql",
        relation_ref="database-relation:target:data",
        stable_key_ref="database-column:target:0",
        version_ref="database-column:target:1",
        columns=(
            DatabaseColumnProfile(
                source_field_ref="database-column:target:0",
                column_name="id",
                sql_type="bigint",
                inferred_type="identifier",
                nullable=False,
                primary_key=True,
                generated=True,
                autoincrement=True,
            ),
        ),
    )

    contract = DatabaseSchemaMappingInput(
        task_id="00000000-0000-0000-0000-000000000001",
        run_id="00000000-0000-0000-0000-000000000002",
        phase="ingest_and_normalize",
        evidence_refs=("mapping:database:target:v3",),
        mapping_schema_version="fixed-six-field-sql-mapping-v3",
        sources=(profile,),
    )

    assert tuple(source.source_role for source in contract.sources) == ("target",)


def test_database_schema_mapping_input_rejects_unbounded_evidence() -> None:
    profile = DatabaseSourceSchemaProfile(
        source_role="target",
        connector_id="seewo-data-mysql",
        dialect="mysql",
        relation_ref="database-relation:target:data",
        stable_key_ref="database-column:target:0",
        version_ref="database-column:target:1",
        columns=(
            DatabaseColumnProfile(
                source_field_ref="database-column:target:0",
                column_name="id",
                sql_type="bigint",
                inferred_type="identifier",
                nullable=False,
                primary_key=True,
                generated=True,
                autoincrement=True,
            ),
        ),
    )

    with pytest.raises(ValueError, match="metadata envelope exceeds the size limit"):
        DatabaseSchemaMappingInput(
            task_id="00000000-0000-0000-0000-000000000001",
            run_id="00000000-0000-0000-0000-000000000002",
            phase="ingest_and_normalize",
            evidence_refs=("x" * MAX_DATABASE_SCHEMA_MAPPING_INPUT_BYTES,),
            mapping_schema_version="fixed-six-field-sql-mapping-v3",
            sources=(profile,),
        )


def test_database_schema_mapping_v2_still_requires_both_database_roles() -> None:
    profile = DatabaseSourceSchemaProfile(
        source_role="target",
        connector_id="seewo-data-mysql",
        dialect="mysql",
        relation_ref="database-relation:target:data",
        stable_key_ref="database-column:target:0",
        version_ref="database-column:target:1",
        columns=(
            DatabaseColumnProfile(
                source_field_ref="database-column:target:0",
                column_name="id",
                sql_type="bigint",
                inferred_type="identifier",
                nullable=False,
                primary_key=True,
                generated=True,
                autoincrement=True,
            ),
        ),
    )

    with pytest.raises(ValueError, match="v2 requires both database source roles"):
        DatabaseSchemaMappingInput(
            task_id="00000000-0000-0000-0000-000000000001",
            run_id="00000000-0000-0000-0000-000000000002",
            phase="ingest_and_normalize",
            evidence_refs=("mapping:fixed-six-field-v2",),
            sources=(profile,),
        )


def test_conversation_skill_advertises_direct_remote_csv_ingestion() -> None:
    skill = SkillRegistry().load("converse-school-data-sync", "1.3.0")

    for term in (
        "`conversation_remote_csv_enabled`",
        "直接发送一个公共 HTTPS CSV 直链",
        "后端自动登记",
        "无需先下载、上传或通过其他入口登记",
        "登记时不读取文件",
        "确认开始同步后",
        "受控任务",
        "冻结 CSV 快照",
        "本地希沃目标",
        "普通 HTML 网页",
        "无需登录",
        "Cookie",
        "自定义请求头",
        "Excel",
        "JSON",
        "压缩包",
        "手动同步",
        "`available_remote_sources` 为空",
        "模型不直接访问 URL",
        "当前部署未启用",
        "不得根据文件名或单侧来源猜测",
        "不代表",
        "`remote_link_candidates`",
        "`remote_url_start`",
        "`remote_url_end`",
        "选择链接边界",
        "后端校验",
    ):
        assert term in skill.instructions


def test_conversation_skill_selects_only_server_listed_api_connections() -> None:
    skill = SkillRegistry().load("converse-school-data-sync", "1.3.0")

    for term in (
        "`available_api_providers`",
        "`available_api_connections`",
        "`api_configuration`",
        "`api_provider_id`",
        "`source_api_connection_id`",
        "MySQL",
        "不得在对话中索要",
        "权限",
        "可见范围",
    ):
        assert term in skill.instructions


def test_rollback_assessment_contract_distinguishes_no_write_from_restore() -> None:
    operation_ids = (
        "1e81d16b-4c26-4ae1-bb3d-e43bff86f351",
        "e01bb39a-3d42-4c37-a416-b031fac14576",
        "ee0f28d0-7474-4aec-97ca-cf8292af803d",
    )

    assessment = AgentRollbackAssessment.model_validate(
        {
            "schema_version": "agent-contract-v1",
            "restorable_operation_ids": [operation_ids[0]],
            "already_restored_operation_ids": [operation_ids[1]],
            "conflict_operation_ids": [operation_ids[2]],
            "impact_zh": "一个待恢复、一个已恢复、一个发生数据冲突。",
            "requires_confirmation": True,
        }
    )

    assert tuple(str(item) for item in assessment.already_restored_operation_ids) == (
        operation_ids[1],
    )


def test_rollback_execution_skill_revalidates_only_affected_current_data() -> None:
    skill = SkillRegistry().load("execute-approved-rollback", "2.1.0")

    for term in (
        "before",
        "after",
        "current",
        "写入前",
        "comparison_hash",
        "只比较原操作影响的字段",
        "保留无关字段",
        "already_restored",
        "conflict_skipped",
        "版本 ID 不能作为",
    ):
        assert term in skill.instructions


def test_rollback_skills_cover_whole_record_and_late_conflict_boundaries() -> None:
    assessment = (
        SkillRegistry()
        .load(
            "assess-agent-rollback-impact",
            "2.1.0",
        )
        .instructions
    )
    execution = (
        SkillRegistry()
        .load(
            "execute-approved-rollback",
            "2.1.0",
        )
        .instructions
    )

    for instructions in (assessment, execution):
        assert "自定义列" in instructions
        assert "完整物理" in instructions
        assert "版本派生顺序" in instructions
        assert "业务依赖" in instructions
        assert "评估和审批" in instructions
        assert "不存在的" in instructions
        assert "二次确认入口" in instructions


@pytest.mark.parametrize("status", ("already_restored", "conflict_skipped"))
def test_rollback_operation_outcome_has_explicit_no_write_statuses(status) -> None:
    outcome = OperationOutcome.model_validate(
        {
            "operation_id": "7f056be2-f193-4642-9385-9507583cd41e",
            "status": status,
            "verification_ref": (
                "verification:already-restored"
                if status == "already_restored"
                else None
            ),
            "safe_error_code": (
                "rollback_current_data_conflict"
                if status == "conflict_skipped"
                else None
            ),
        }
    )

    assert outcome.status == status


def test_every_legacy_skill_is_detailed_and_cannot_enter_new_agent_workflow() -> None:
    registry = SkillRegistry()

    for name in LEGACY_SKILLS:
        instructions = registry.load(name, "1.0.0").instructions

        assert len(instructions) >= 900, name
        assert "legacy-v1" in instructions, name
        assert "new-agent-v1" in instructions, name
        for section in REQUIRED_SECTIONS:
            assert section in instructions, f"{name} missing {section}"


def test_common_agent_contract_covers_runtime_authority_privacy_and_fail_closed_output() -> (
    None
):
    for term in (
        "OperatorContext.tenant_id",
        "服务端阶段",
        "第三方权威数据",
        "希沃目标",
        "学生手机号",
        "任务级令牌",
        "简体中文",
        "严格 JSON",
        "证据不足",
        "学校锁",
        "不得编造",
    ):
        assert term in COMMON_AGENT_SAFETY_CONTRACT


def test_agent_skills_make_missing_authority_student_class_an_opt_in_clear() -> None:
    registry = SkillRegistry()
    normalization = registry.load(
        "normalize-organization-data-batch",
        "1.0.0",
    ).instructions
    reconciliation = registry.load("reconcile-entity-batch", "1.0.0").instructions
    governance = registry.load("generate-governance-solutions", "1.0.0").instructions

    assert "第三方学生班级允许为空" in normalization
    assert "不得因此设置 `invalid=true`" in normalization
    assert "默认保留希沃班级" in reconciliation
    assert '`proposed_operation="update"`' in reconciliation
    assert "主动勾选" in governance
    assert "中风险 `opt_in`" in governance
    assert '`operation="update"`' in governance


def test_legacy_prompt_builder_injects_common_contract_and_pinned_skill_identity() -> (
    None
):
    skill = SkillRegistry().load("analyze-data-difference", "1.0.0")

    system_prompt = build_messages(skill, {"difference_id": "example"})[0].content

    assert "analyze-data-difference@1.0.0" in system_prompt
    assert COMMON_AGENT_SAFETY_CONTRACT in system_prompt
