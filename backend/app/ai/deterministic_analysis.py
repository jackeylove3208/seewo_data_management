from app.governance.field_policy import editable_fields
from app.schemas.differences import DifferenceItem, DifferenceType
from app.schemas.governance import (
    AutoExecutableResolution,
    CauseAnalysisV2,
    CauseAnalysisV3,
    GovernanceOption,
    ManualResolution,
    ManualStep,
    ProposedFieldChange,
    RecommendedAction,
    ResolutionAction,
    RiskLevel,
)


class DeterministicAnalysis:
    def for_difference(self, difference: DifferenceItem) -> CauseAnalysisV2 | None:
        if difference.difference_type is DifferenceType.SEEWO_MISSING:
            source = difference.evidence.source_payload or {}
            changes = tuple(
                ProposedFieldChange(field=field, before=None, after=source.get(field))
                for field in sorted(editable_fields(difference.entity_type).intersection(source))
                if source.get(field) is not None
            )
            return CauseAnalysisV2(
                cause="Authoritative entity has no accepted Seewo mapping",
                evidence_summary="No compatible target entity was accepted for this source entity",
                manual_only=False,
                options=(
                    GovernanceOption(
                        option_id="create-authoritative-entity",
                        operation_type=RecommendedAction.CREATE,
                        proposed_changes=changes,
                        rationale="Create the missing entity from the authoritative snapshot",
                        evidence_refs=("source_entity",),
                        risk=RiskLevel.MEDIUM,
                        confidence=1,
                        recommended=True,
                    ),
                ),
            )
        if difference.difference_type is DifferenceType.SEEWO_REDUNDANT:
            return CauseAnalysisV2(
                cause="Target entity is unconsumed in a complete reconciliation scope",
                evidence_summary="No authoritative entity consumed this target entity",
                manual_only=True,
                manual_reason=(
                    "Disabling an unmatched target entity is high risk and requires review"
                ),
            )
        return None

    def for_difference_v3(self, difference: DifferenceItem) -> CauseAnalysisV3 | None:
        if difference.difference_type is DifferenceType.SEEWO_MISSING:
            source = difference.evidence.source_payload or {}
            changes = tuple(
                ProposedFieldChange(field=field, before=None, after=source.get(field))
                for field in sorted(editable_fields(difference.entity_type).intersection(source))
                if source.get(field) is not None
            )
            solution_id = "create-authoritative-entity"
            return CauseAnalysisV3(
                locale="zh-CN",
                issue_title="希沃缺少组织实体",
                cause_summary="第三方权威记录在希沃中没有可接受的对应实体。",
                evidence_summary="实体匹配阶段没有找到能够确认的希沃记录。",
                business_impact="相关人员或组织可能无法正常使用希沃教学服务。",
                recommended_solution_id=solution_id,
                solutions=(
                    AutoExecutableResolution(
                        solution_id=solution_id,
                        title="按权威记录新增实体",
                        rationale="使用第三方权威快照中已确认的属性生成新增方案。",
                        risk=RiskLevel.MEDIUM,
                        risk_reason="新增实体仍需在治理执行前核对层级和必填字段。",
                        confidence=1,
                        evidence_refs=("source_entity",),
                        preconditions=("权威记录版本保持不变",),
                        recommended=True,
                        action=ResolutionAction(
                            operation_type=RecommendedAction.CREATE,
                            proposed_changes=changes,
                        ),
                    ),
                ),
            )
        if difference.difference_type is DifferenceType.SEEWO_REDUNDANT:
            solution_id = "review-redundant-entity"
            return CauseAnalysisV3(
                locale="zh-CN",
                issue_title="希沃存在未匹配实体",
                cause_summary="完整对账范围内没有权威记录与该希沃实体对应。",
                evidence_summary="实体匹配结果显示该希沃记录未被任何第三方权威实体使用。",
                business_impact="直接停用可能影响仍在使用但未纳入权威数据的账号。",
                recommended_solution_id=solution_id,
                solutions=(
                    ManualResolution(
                        solution_id=solution_id,
                        title="人工核对未匹配实体",
                        rationale="停用属于高风险操作，必须先确认实体确实不再使用。",
                        risk=RiskLevel.HIGH,
                        risk_reason="错误停用会影响真实用户或组织。",
                        confidence=0.8,
                        evidence_refs=("target_entity",),
                        recommended=True,
                        manual_steps=(
                            ManualStep(
                                order=1,
                                instruction="联系学校管理员确认该实体是否仍在使用。",
                            ),
                            ManualStep(
                                order=2,
                                instruction="核对完成后通过人工编辑器生成待执行方案。",
                            ),
                        ),
                    ),
                ),
            )
        return None
