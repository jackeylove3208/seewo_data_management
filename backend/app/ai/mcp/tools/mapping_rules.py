from typing import Any

from app.differences.field_policies import FieldComparisonPolicy
from app.schemas.differences import DifferenceItem


def read_mapping_rules(
    difference: DifferenceItem,
    policy: FieldComparisonPolicy,
) -> dict[str, Any]:
    rules = policy.config.entities[difference.entity_type]
    return {
        "comparison_rule_version": policy.version,
        "entity_type": difference.entity_type.value,
        "rules": [rule.model_dump(mode="json") for rule in rules],
    }
