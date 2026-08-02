from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from app.normalization.identifiers import normalize_email, normalize_identifier, normalize_phone
from app.schemas.canonical_entities import EntityType
from app.schemas.matching import (
    MatchDecision,
    MatchEvidence,
    MatchMethod,
    MatchStatus,
    NormalizedRecord,
)
from app.schemas.rematching import (
    KeyGroupPolicy,
    TrustedSourceIdentifierPolicy,
    VersionedKeyPolicy,
)

KeyPolicy = tuple[tuple[str, ...], ...]

DEFAULT_KEY_POLICIES: dict[EntityType, VersionedKeyPolicy] = {
    EntityType.ORGANIZATION_UNIT: VersionedKeyPolicy(
        version="organization-unit-keys-v2",
        entity_type=EntityType.ORGANIZATION_UNIT,
        groups=(KeyGroupPolicy(name="code", fields=("code",)),),
    ),
    EntityType.CLASS: VersionedKeyPolicy(
        version="class-keys-v2",
        entity_type=EntityType.CLASS,
        groups=(
            KeyGroupPolicy(
                name="year_grade_number_parent",
                fields=("school_year", "grade", "class_number", "parent_mapping_id"),
            ),
        ),
    ),
    EntityType.TEACHER: VersionedKeyPolicy(
        version="teacher-keys-v2",
        entity_type=EntityType.TEACHER,
        groups=(
            KeyGroupPolicy(name="employee_number", fields=("employee_number",)),
            KeyGroupPolicy(name="name_phone", fields=("name", "phone")),
            KeyGroupPolicy(name="name_email", fields=("name", "email")),
        ),
    ),
    EntityType.STUDENT: VersionedKeyPolicy(
        version="student-keys-v2",
        entity_type=EntityType.STUDENT,
        groups=(
            KeyGroupPolicy(name="student_number", fields=("student_number",)),
            KeyGroupPolicy(name="name_phone", fields=("name", "phone")),
            KeyGroupPolicy(name="name_email", fields=("name", "email")),
            KeyGroupPolicy(name="name_class", fields=("name", "parent_mapping_id")),
        ),
    ),
    EntityType.MEMBERSHIP: VersionedKeyPolicy(
        version="membership-keys-v2",
        entity_type=EntityType.MEMBERSHIP,
        groups=(
            KeyGroupPolicy(
                name="member_container_role",
                fields=("member_mapping_id", "container_mapping_id", "role"),
            ),
        ),
    ),
}


@dataclass
class ExactTargetIndex:
    buckets: dict[
        tuple[str, EntityType, tuple[str, ...], tuple[str, ...]],
        tuple[NormalizedRecord, ...],
    ]
    indexed_records: int
    snapshot_ids: frozenset[UUID]
    lookup_count: int = 0

    def lookup(
        self,
        source: NormalizedRecord,
        fields: tuple[str, ...],
        key: tuple[str, ...],
        *,
        target_snapshot_id: UUID | None = None,
    ) -> tuple[NormalizedRecord, ...]:
        self.lookup_count += 1
        matches = self.buckets.get(
            (source.tenant_id, source.entity_type, fields, key),
            (),
        )
        if target_snapshot_id is None:
            return matches
        return tuple(record for record in matches if record.snapshot_id == target_snapshot_id)


class ExactMatcher:
    def __init__(
        self,
        key_policies: dict[EntityType, KeyPolicy] | None = None,
        *,
        rule_version: str | None = None,
        versioned_policies: Sequence[VersionedKeyPolicy] | None = None,
        source_id_trust_policies: Sequence[TrustedSourceIdentifierPolicy] = (),
    ) -> None:
        if key_policies is not None and versioned_policies is not None:
            raise ValueError("use legacy or versioned exact key policies, not both")
        if key_policies is not None and rule_version is None:
            raise ValueError("custom exact key policies require an explicit rule_version")
        self.policies = dict(DEFAULT_KEY_POLICIES)
        if key_policies is not None:
            assert rule_version is not None
            for entity_type, policies in key_policies.items():
                if not policies or any(not fields or not all(fields) for fields in policies):
                    raise ValueError("exact key policies require non-empty field tuples")
                _reject_untrusted_source_id_groups(policies)
                self.policies[entity_type] = VersionedKeyPolicy(
                    version=rule_version,
                    entity_type=entity_type,
                    groups=tuple(
                        KeyGroupPolicy(name="_".join(fields), fields=fields) for fields in policies
                    ),
                )
        if versioned_policies is not None:
            for policy in versioned_policies:
                _reject_untrusted_source_id_groups(tuple(group.fields for group in policy.groups))
                self.policies[policy.entity_type] = policy
        self.key_policies = {
            entity_type: tuple(group.fields for group in policy.groups)
            for entity_type, policy in self.policies.items()
        }
        self.rule_version = rule_version or "exact-matching-v1"
        self.source_id_trust_policies = tuple(source_id_trust_policies)

    def match(
        self,
        source: NormalizedRecord,
        targets: Sequence[NormalizedRecord] | ExactTargetIndex,
        *,
        target_snapshot_id: UUID | None = None,
    ) -> MatchDecision | None:
        target_index = (
            targets if isinstance(targets, ExactTargetIndex) else self.build_index(targets)
        )
        if not target_index.snapshot_ids:
            return None
        policy = self.policies[source.entity_type]
        if target_snapshot_id is None and len(target_index.snapshot_ids) == 1:
            target_snapshot_id = next(iter(target_index.snapshot_ids))
        elif target_snapshot_id is None and len(target_index.snapshot_ids) > 1:
            has_possible_match = any(
                target_index.buckets.get((source.tenant_id, source.entity_type, group.fields, key))
                for group in policy.groups
                if (key := _non_null_key(source, group.fields)) is not None
            )
            if has_possible_match:
                raise ValueError(
                    "target_snapshot_id is required when the exact index contains mixed snapshots"
                )
        resolved_groups: list[
            tuple[KeyGroupPolicy, tuple[str, ...], tuple[NormalizedRecord, ...], str]
        ] = []
        for group in policy.groups:
            source_key = _non_null_key(source, group.fields)
            if source_key is None:
                continue
            matches = target_index.lookup(
                source,
                group.fields,
                source_key,
                target_snapshot_id=target_snapshot_id,
            )
            if matches:
                resolved_groups.append((group, source_key, matches, policy.version))
        trusted_policy = (
            self._trusted_source_id_policy(source, target_index, target_snapshot_id)
            if target_snapshot_id is not None
            else None
        )
        if trusted_policy is not None:
            fields = (trusted_policy.field,)
            source_key = _non_null_key(source, fields)
            if source_key is not None:
                matches = target_index.lookup(
                    source,
                    fields,
                    source_key,
                    target_snapshot_id=trusted_policy.target_snapshot_id,
                )
                if matches:
                    resolved_groups.append(
                        (
                            KeyGroupPolicy(name="trusted_source_id", fields=fields),
                            source_key,
                            matches,
                            trusted_policy.version,
                        )
                    )
        if not resolved_groups:
            return None
        if any(len(matches) > 1 for _, _, matches, _ in resolved_groups):
            return _groups_conflict_decision(source, resolved_groups, policy.version)
        target_ids = {matches[0].entity_id for _, _, matches, _ in resolved_groups}
        if len(target_ids) != 1:
            return _groups_conflict_decision(source, resolved_groups, policy.version)
        group, _, matches, version = resolved_groups[0]
        return _accepted_decision(source, matches[0], group, version)

    def build_index(self, targets: Sequence[NormalizedRecord]) -> ExactTargetIndex:
        buckets: dict[
            tuple[str, EntityType, tuple[str, ...], tuple[str, ...]],
            list[NormalizedRecord],
        ] = defaultdict(list)
        for target in targets:
            fields_to_index = dict.fromkeys(
                [*self.key_policies[target.entity_type], ("source_id",)]
            )
            for fields in fields_to_index:
                key = _non_null_key(target, fields)
                if key is not None:
                    buckets[(target.tenant_id, target.entity_type, fields, key)].append(target)
        return ExactTargetIndex(
            buckets={key: tuple(records) for key, records in buckets.items()},
            indexed_records=len(targets),
            snapshot_ids=frozenset(record.snapshot_id for record in targets),
        )

    def _trusted_source_id_policy(
        self,
        source: NormalizedRecord,
        target_index: ExactTargetIndex,
        target_snapshot_id: UUID,
    ) -> TrustedSourceIdentifierPolicy | None:
        matches = [
            policy
            for policy in self.source_id_trust_policies
            if policy.can_auto_match
            and policy.tenant_id == source.tenant_id
            and policy.entity_type is source.entity_type
            and policy.source_snapshot_id == source.snapshot_id
            and policy.target_snapshot_id == target_snapshot_id
            and target_snapshot_id in target_index.snapshot_ids
        ]
        if len(matches) > 1:
            raise ValueError("multiple trusted source identifier policies apply to the pair")
        return matches[0] if matches else None


def _reject_untrusted_source_id_groups(policies: Sequence[tuple[str, ...]]) -> None:
    if any("source_id" in fields for fields in policies):
        raise ValueError(
            "source_id exact matching requires an explicit TrustedSourceIdentifierPolicy"
        )


def _field_value(record: NormalizedRecord, field: str) -> str | None:
    if field == "source_id":
        return record.source_id
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
    if not value.strip():
        return False
    if field == "phone":
        return normalize_phone(value) == value
    if field == "email":
        return normalize_email(value) == value
    if field in {"code", "employee_number", "student_number", "source_id"}:
        return normalize_identifier(value) == value
    return True


def _accepted_decision(
    source: NormalizedRecord,
    target: NormalizedRecord,
    group: KeyGroupPolicy,
    rule_version: str,
) -> MatchDecision:
    method = MatchMethod.STABLE_ID if len(group.fields) == 1 else MatchMethod.COMPOSITE_KEY
    evidence = (
        *(
            MatchEvidence(
                feature=field,
                source_value=_field_value(source, field),
                target_value=_field_value(target, field),
                score=1,
            )
            for field in group.fields
        ),
        MatchEvidence(
            feature=f"key_group:{group.name}",
            source_value=rule_version,
            target_value=target.record_key,
            score=1,
        ),
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


def _groups_conflict_decision(
    source: NormalizedRecord,
    groups: Sequence[tuple[KeyGroupPolicy, tuple[str, ...], tuple[NormalizedRecord, ...], str]],
    rule_version: str,
) -> MatchDecision:
    evidence = tuple(
        MatchEvidence(
            feature=(
                f"non_unique_key_group:{group.name}"
                if len(matches) > 1
                else f"conflicting_key_group:{group.name}"
            ),
            source_value=f"{version}:{'|'.join(source_key)}",
            target_value="|".join(record.record_key for record in matches),
            score=0,
        )
        for group, source_key, matches, version in groups
    )
    return MatchDecision(
        entity_type=source.entity_type,
        source_entity_id=source.entity_id,
        source_key=source.record_key,
        status=MatchStatus.CONFLICT,
        confidence=0,
        evidence=evidence,
        rule_version=rule_version,
    )
