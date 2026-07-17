from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.schemas.canonical_entities import EntityType
from app.schemas.matching import MatchDecision, MatchStatus, NormalizedRecord


def test_matching_contracts_enforce_database_string_limits() -> None:
    common = {
        "entity_type": EntityType.TEACHER,
        "source_entity_id": uuid4(),
        "status": MatchStatus.UNMATCHED,
        "confidence": 0,
    }
    with pytest.raises(ValidationError):
        MatchDecision(source_key="x" * 513, rule_version="rule-v1", **common)
    with pytest.raises(ValidationError):
        MatchDecision(source_key="teacher:T1", rule_version="r" * 65, **common)
    with pytest.raises(ValidationError):
        NormalizedRecord(
            entity_id=uuid4(),
            snapshot_id=uuid4(),
            tenant_id="school-1",
            entity_type=EntityType.TEACHER,
            source_id="x" * 256,
            values={},
            rule_version="normalization-v1",
        )
