from app.ai.agent_prompting import COMMON_AGENT_SAFETY_CONTRACT
from app.ai.prompting import build_messages
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
    "orchestrate-school-data-sync": (
        "固定阶段",
        "学校锁",
        "终止",
        "模型失败",
        "报告",
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
    "understand-organization-database-schema": (
        "PostgreSQL",
        "MySQL",
        "固定六字段",
        "source_field_ref",
        "normalizer_id",
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
    "aggregate-risk-approvals": (
        "学生手机号",
        "冻结",
        "内容哈希",
        "同意",
        "拒绝",
        "不得代替",
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
        "版本冲突",
        "人工确认",
    ),
    "execute-approved-rollback": (
        "独立回滚任务",
        "补偿操作",
        "版本冲突",
        "幂等",
        "验证",
        "第三方",
    ),
}

LEGACY_SKILLS = {
    "analyze-data-difference",
    "assess-rollback-impact",
    "generate-governance-plan",
    "generate-governance-report",
    "resolve-ambiguous-entity",
    "resolve-entity-rematching",
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
        instructions = registry.load(name, "1.0.0").instructions

        assert len(instructions) >= 1400, name
        for section in REQUIRED_SECTIONS:
            assert section in instructions, f"{name} missing {section}"
        for term in required_terms:
            assert term in instructions, f"{name} missing {term}"


def test_every_legacy_skill_is_detailed_and_cannot_enter_new_agent_workflow() -> None:
    registry = SkillRegistry()

    for name in LEGACY_SKILLS:
        instructions = registry.load(name, "1.0.0").instructions

        assert len(instructions) >= 900, name
        assert "legacy-v1" in instructions, name
        assert "new-agent-v1" in instructions, name
        for section in REQUIRED_SECTIONS:
            assert section in instructions, f"{name} missing {section}"


def test_common_agent_contract_covers_runtime_authority_privacy_and_fail_closed_output() -> None:
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


def test_legacy_prompt_builder_injects_common_contract_and_pinned_skill_identity() -> None:
    skill = SkillRegistry().load("analyze-data-difference", "1.0.0")

    system_prompt = build_messages(skill, {"difference_id": "example"})[0].content

    assert "analyze-data-difference@1.0.0" in system_prompt
    assert COMMON_AGENT_SAFETY_CONTRACT in system_prompt
