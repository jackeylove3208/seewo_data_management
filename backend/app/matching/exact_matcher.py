from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass

from app.normalization.identifiers import normalize_email, normalize_identifier, normalize_phone
from app.schemas.canonical_entities import EntityType
from app.schemas.matching import (
    MatchDecision,
    MatchEvidence,
    MatchMethod,
    MatchStatus,
    NormalizedRecord,
)

KeyPolicy = tuple[tuple[str, ...], ...]

STABLE_KEYS: dict[EntityType, KeyPolicy] = {
    EntityType.ORGANIZATION_UNIT: (("code",),),
    EntityType.CLASS: (("school_year", "grade", "class_number", "parent_mapping_id"),),
    EntityType.TEACHER: (("employee_number",), ("phone",), ("email",)),
    EntityType.STUDENT: (("student_number",),),
    EntityType.MEMBERSHIP: (("member_mapping_id", "container_mapping_id", "role"),),
}


@dataclass
class ExactTargetIndex:
    buckets: dict[
        tuple[str, EntityType, tuple[str, ...], tuple[str, ...]],
        tuple[NormalizedRecord, ...],
    ]
    indexed_records: int
    lookup_count: int = 0

    def lookup(
        self,
        source: NormalizedRecord,
        fields: tuple[str, ...],
        key: tuple[str, ...],
    ) -> tuple[NormalizedRecord, ...]:
        self.lookup_count += 1
        return self.buckets.get(
            (source.tenant_id, source.entity_type, fields, key),
            (),
        )


class ExactMatcher:
    def __init__(
        self,
        key_policies: dict[EntityType, KeyPolicy] | None = None,
        *,
        rule_version: str | None = None,
    ) -> None:
        if key_policies is not None and rule_version is None:
            raise ValueError("custom exact key policies require an explicit rule_version")
        self.key_policies = dict(STABLE_KEYS)
        if key_policies is not None:
            for entity_type, policies in key_policies.items():
                if not policies or any(not fields or not all(fields) for fields in policies):
                    raise ValueError("exact key policies require non-empty field tuples")
                self.key_policies[entity_type] = policies
        self.rule_version = rule_version or "exact-matching-v1"

    def match(
        self,
        source: NormalizedRecord,
        targets: Sequence[NormalizedRecord] | ExactTargetIndex,
    ) -> MatchDecision | None:
        target_index = (
            targets if isinstance(targets, ExactTargetIndex) else self.build_index(targets)
        )
        for fields in self.key_policies[source.entity_type]:
            source_key = _non_null_key(source, fields)
            if source_key is None:
                continue
            matches = target_index.lookup(source, fields, source_key)
            if len(matches) == 1:
                return _accepted_decision(
                    source,
                    matches[0],
                    fields,
                    self.rule_version,
                )
            if len(matches) > 1:
                return _conflict_decision(
                    source,
                    fields,
                    source_key,
                    len(matches),
                    self.rule_version,
                )
        return None

    def build_index(self, targets: Sequence[NormalizedRecord]) -> ExactTargetIndex:
        buckets: dict[
            tuple[str, EntityType, tuple[str, ...], tuple[str, ...]],
            list[NormalizedRecord],
        ] = defaultdict(list)
        for target in targets:
            for fields in self.key_policies[target.entity_type]:
                key = _non_null_key(target, fields)
                if key is not None:
                    buckets[(target.tenant_id, target.entity_type, fields, key)].append(target)
        return ExactTargetIndex(
            buckets={key: tuple(records) for key, records in buckets.items()},
            indexed_records=len(targets),
        )


def _field_value(record: NormalizedRecord, field: str) -> str | None:
    if field == "parent_mapping_id":
        return str(record.parent_mapping_id) if record.parent_mapping_id else None
    return record.values.get(field)


def _non_null_key(
    record: NormalizedRecord,
    fields: tuple[str, ...],
) -> tuple[str, ...] | None:
    values = tuple(_field_value(record, field) for field in fields)
    if any(value is None for value in values):
        return None
    narrowed = tuple(value for value in values if value is not None)
    if any(
        not _is_valid_key_value(field, value) for field, value in zip(fields, narrowed, strict=True)
    ):
        return None
    return narrowed


def _is_valid_key_value(field: str, value: str) -> bool:
    if field == "phone":
        return normalize_phone(value) == value
    if field == "email":
        return normalize_email(value) == value
    if field in {"code", "employee_number", "student_number"}:
        return normalize_identifier(value) == value
    return True


def _accepted_decision(
    source: NormalizedRecord,
    target: NormalizedRecord,
    fields: tuple[str, ...],
    rule_version: str,
) -> MatchDecision:
    method = MatchMethod.STABLE_ID if len(fields) == 1 else MatchMethod.COMPOSITE_KEY
    evidence = tuple(
        MatchEvidence(
            feature=field,
            source_value=_field_value(source, field),
            target_value=_field_value(target, field),
            score=1,
        )
        for field in fields
    )
    return MatchDecision(
        entity_type=source.entity_type,
        source_entity_id=source.entity_id,
        source_key=source.record_key,
        target_entity_id=target.entity_id,
        target_key=target.record_key,
        method=method,
        status=MatchStatus.ACCEPTED,
        confidence=1,
        evidence=evidence,
        rule_version=rule_version,
    )


def _conflict_decision(
    source: NormalizedRecord,
    fields: tuple[str, ...],
    source_key: tuple[str, ...],
    candidate_count: int,
    rule_version: str,
) -> MatchDecision:
    return MatchDecision(
        entity_type=source.entity_type,
        source_entity_id=source.entity_id,
        source_key=source.record_key,
        status=MatchStatus.CONFLICT,
        confidence=0,
        evidence=(
            MatchEvidence(
                feature=f"duplicate:{'+'.join(fields)}",
                source_value="|".join(source_key),
                target_value=f"{candidate_count} candidates",
                score=0,
            ),
        ),
        rule_version=rule_version,
    )
