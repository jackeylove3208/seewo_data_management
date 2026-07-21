from collections.abc import Mapping, Sequence
from uuid import UUID

from app.schemas.canonical_entities import EntityType
from app.schemas.rematching import (
    MatchingQualityCounts,
    MatchingQualityGate,
    MatchingQualityResult,
)

QUALITY_POLICY_VERSION = "matching-quality-v1"

_ENTITY_LABELS = {
    EntityType.ORGANIZATION_UNIT: "组织单位",
    EntityType.CLASS: "班级",
    EntityType.TEACHER: "教师",
    EntityType.STUDENT: "学生",
    EntityType.MEMBERSHIP: "成员关系",
}

_PARENT_TYPES = {
    EntityType.CLASS: EntityType.ORGANIZATION_UNIT,
    EntityType.TEACHER: EntityType.ORGANIZATION_UNIT,
    EntityType.STUDENT: EntityType.CLASS,
}


class MatchingQualityPolicy:
    def __init__(
        self,
        *,
        version: str = QUALITY_POLICY_VERSION,
        unresolved_threshold: float = 0.2,
        minimum_population: int = 10,
    ) -> None:
        if not version.strip():
            raise ValueError("quality policy version cannot be blank")
        if not 0 <= unresolved_threshold <= 1:
            raise ValueError("unresolved threshold must be between 0 and 1")
        if minimum_population < 1:
            raise ValueError("minimum population must be positive")
        self.version = version
        self.unresolved_threshold = unresolved_threshold
        self.minimum_population = minimum_population

    def evaluate(
        self,
        *,
        task_id: UUID,
        mapping_versions: Sequence[str],
        counts_by_type: Mapping[EntityType, MatchingQualityCounts],
    ) -> MatchingQualityResult:
        failures: list[MatchingQualityGate] = []
        for entity_type, counts in counts_by_type.items():
            failures.extend(self._volume_failures(entity_type, counts))
        failures.extend(self._parent_failures(counts_by_type))
        return MatchingQualityResult(
            task_id=task_id,
            policy_version=self.version,
            mapping_versions=tuple(mapping_versions),
            counts=dict(counts_by_type),
            passed=not failures,
            failures=tuple(failures),
        )

    def _volume_failures(
        self,
        entity_type: EntityType,
        counts: MatchingQualityCounts,
    ) -> list[MatchingQualityGate]:
        if counts.total < self.minimum_population:
            return []
        label = _ENTITY_LABELS[entity_type]
        failures: list[MatchingQualityGate] = []
        unresolved_ratio = round(counts.remaining_unresolved / counts.total, 6)
        if unresolved_ratio > self.unresolved_threshold:
            failures.append(
                self._failure(
                    entity_type,
                    reason=(
                        f"{label}未解析比例为 {unresolved_ratio:.1%}，超过安全阈值 "
                        f"{self.unresolved_threshold:.1%}。"
                    ),
                    observed_value=unresolved_ratio,
                )
            )
        target_total = counts.accepted + counts.unconsumed_target
        redundant_ratio = (
            round(counts.predicted_redundant / target_total, 6) if target_total else 0.0
        )
        if target_total >= self.minimum_population and redundant_ratio > self.unresolved_threshold:
            failures.append(
                self._failure(
                    entity_type,
                    reason=(
                        f"{label}预测停用比例为 {redundant_ratio:.1%}，超过安全阈值 "
                        f"{self.unresolved_threshold:.1%}。"
                    ),
                    observed_value=redundant_ratio,
                )
            )
        return failures

    def _parent_failures(
        self,
        counts_by_type: Mapping[EntityType, MatchingQualityCounts],
    ) -> list[MatchingQualityGate]:
        failures: list[MatchingQualityGate] = []
        for child_type, parent_type in _PARENT_TYPES.items():
            child = counts_by_type.get(child_type)
            parent = counts_by_type.get(parent_type)
            if child is None or parent is None or child.total == 0 or parent.accepted > 0:
                continue
            failures.append(
                self._failure(
                    child_type,
                    reason=f"{_ENTITY_LABELS[parent_type]}没有可用的已确认映射，无法安全处理{_ENTITY_LABELS[child_type]}。",
                    observed_value=0,
                )
            )
        return failures

    def _failure(
        self,
        entity_type: EntityType,
        *,
        reason: str,
        observed_value: float,
    ) -> MatchingQualityGate:
        return MatchingQualityGate(
            affected_entity_types=(entity_type,),
            reason=reason,
            observed_value=observed_value,
            threshold=self.unresolved_threshold,
            recovery_actions=("确认上级实体映射", "重试 AI 二次匹配"),
        )
