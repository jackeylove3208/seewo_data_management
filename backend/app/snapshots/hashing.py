import hashlib
import json
from collections.abc import Sequence

from app.schemas.canonical_entities import CanonicalEntity


def hash_canonical_entities(entities: Sequence[CanonicalEntity]) -> str:
    payload = [entity.model_dump(mode="json") for entity in entities]
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()
