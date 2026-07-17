from collections import Counter, defaultdict
from collections.abc import Sequence
from uuid import UUID

from rapidfuzz import fuzz

from app.matching.blocking import block_key
from app.schemas.matching import BlockKey, Candidate, NormalizedRecord


class CandidateRetriever:
    def __init__(self, targets: Sequence[NormalizedRecord]) -> None:
        self._records: dict[UUID, NormalizedRecord] = {}
        posting_sets: dict[BlockKey, dict[str, set[UUID]]] = defaultdict(lambda: defaultdict(set))
        name_sets: dict[BlockKey, dict[str, set[UUID]]] = defaultdict(lambda: defaultdict(set))
        for target in targets:
            self._records[target.entity_id] = target
            target_block = block_key(target)
            for token in _tokens(_search_text(target)):
                posting_sets[target_block][token].add(target.entity_id)
            name_sets[target_block][_primary_name(target)].add(target.entity_id)
        self._postings = {
            key: {token: frozenset(ids) for token, ids in postings.items()}
            for key, postings in posting_sets.items()
        }
        self._ordered_postings = {
            key: {token: tuple(sorted(ids, key=str)) for token, ids in postings.items()}
            for key, postings in posting_sets.items()
        }
        self._name_postings = {
            key: {name: tuple(sorted(ids, key=str)) for name, ids in postings.items()}
            for key, postings in name_sets.items()
        }
        self.comparisons = 0
        self.posting_visits = 0
        self.max_returned = 0

    def lexical(self, source: NormalizedRecord, *, top_k: int = 20) -> list[Candidate]:
        if top_k < 1:
            raise ValueError("top_k must be at least 1")
        source_block = block_key(source)
        postings = self._postings.get(source_block)
        ordered_postings = self._ordered_postings.get(source_block)
        if not postings or not ordered_postings:
            return []

        pool_limit = max(top_k * 10, top_k)
        exact_name_ids = self._name_postings.get(source_block, {}).get(
            _primary_name(source),
            (),
        )
        source_tokens = _tokens(_search_text(source))
        available = sorted(
            (len(postings[token]), token) for token in source_tokens if token in postings
        )
        if not exact_name_ids and not available:
            return []
        seed_ids = list(exact_name_ids[:pool_limit])
        seen = set(seed_ids)
        self.posting_visits += len(seed_ids)
        for _, token in available:
            for entity_id in ordered_postings[token][:pool_limit]:
                self.posting_visits += 1
                if entity_id not in seen:
                    seed_ids.append(entity_id)
                    seen.add(entity_id)
                if len(seed_ids) >= pool_limit:
                    break
            if len(seed_ids) >= pool_limit:
                break
        overlap: Counter[UUID] = Counter()
        for entity_id in seed_ids:
            for token in source_tokens:
                self.posting_visits += 1
                if entity_id in postings.get(token, ()):
                    overlap[entity_id] += 1
        candidate_ids = [
            entity_id
            for entity_id, _ in sorted(
                overlap.items(),
                key=lambda item: (-item[1], str(item[0])),
            )
        ]
        self.comparisons += len(candidate_ids)
        ranked = sorted(
            (
                Candidate(
                    entity=self._records[entity_id],
                    block_key=source_block,
                    lexical_score=_lexical_score(source, self._records[entity_id]),
                )
                for entity_id in candidate_ids
            ),
            key=lambda candidate: (
                -(candidate.lexical_score or 0),
                str(candidate.entity_id),
            ),
        )[:top_k]
        self.max_returned = max(self.max_returned, len(ranked))
        return ranked


def _search_text(record: NormalizedRecord) -> str:
    values = (
        record.values.get("display_name"),
        record.values.get("name"),
        record.values.get("organization_path"),
    )
    return " ".join(value for value in values if value)


def _primary_name(record: NormalizedRecord) -> str:
    return (record.values.get("display_name") or record.values.get("name") or "").casefold()


def _tokens(value: str) -> frozenset[str]:
    compact = "".join(value.casefold().split())
    if not compact:
        return frozenset()
    characters = set(compact)
    bigrams = {compact[index : index + 2] for index in range(len(compact) - 1)}
    return frozenset(characters | bigrams)


def _lexical_score(source: NormalizedRecord, target: NormalizedRecord) -> float:
    source_name = source.values.get("display_name") or source.values.get("name") or ""
    target_name = target.values.get("display_name") or target.values.get("name") or ""
    source_path = source.values.get("organization_path") or ""
    target_path = target.values.get("organization_path") or ""
    name_score = fuzz.WRatio(source_name, target_name) / 100
    path_score = fuzz.token_set_ratio(source_path, target_path) / 100
    return round(0.7 * name_score + 0.3 * path_score, 6)
