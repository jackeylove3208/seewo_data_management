import base64
import json
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import and_, insert, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.differences import (
    DifferenceRecord,
    ImmutableDifferenceError,
)
from app.schemas.canonical_entities import EntityType
from app.schemas.differences import (
    DifferenceAction,
    DifferenceDraft,
    DifferenceEvidence,
    DifferenceFilters,
    DifferenceItem,
    DifferencePage,
    DifferenceStatus,
    DifferenceType,
)

__all__ = ["DifferenceRepository", "ImmutableDifferenceError"]


class DifferenceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def insert_many(
        self,
        drafts: Sequence[DifferenceDraft],
    ) -> tuple[DifferenceItem, ...]:
        prepared = tuple((draft, draft.evidence_hash()) for draft in drafts)
        if not prepared:
            return ()
        records = await self._find_existing_many(hash_value for _, hash_value in prepared)
        last_error: IntegrityError | None = None
        for _attempt in range(3):
            missing_by_key = {
                _draft_key(draft, hash_value): (draft, hash_value)
                for draft, hash_value in prepared
                if _draft_key(draft, hash_value) not in records
            }
            missing = list(missing_by_key.values())
            if not missing:
                break
            try:
                async with self.session.begin_nested():
                    inserted_records = list(
                        await self.session.scalars(
                            insert(DifferenceRecord).returning(DifferenceRecord),
                            [self._values(draft, hash_value) for draft, hash_value in missing],
                        )
                    )
                records.update((_record_key(record), record) for record in inserted_records)
            except IntegrityError as error:
                last_error = error
                records.update(
                    await self._find_existing_many(hash_value for _, hash_value in missing)
                )
        unresolved = [
            _draft_key(draft, hash_value)
            for draft, hash_value in prepared
            if _draft_key(draft, hash_value) not in records
        ]
        if unresolved:
            if last_error is not None:
                raise last_error
            raise RuntimeError("difference bulk insert did not return all requested records")
        return tuple(
            self._item(records[_draft_key(draft, hash_value)]) for draft, hash_value in prepared
        )

    async def get(self, difference_id: UUID) -> DifferenceItem | None:
        record = await self.session.get(DifferenceRecord, difference_id)
        return self._item(record) if record is not None else None

    async def for_task(self, task_id: UUID) -> tuple[DifferenceItem, ...]:
        records = await self.session.scalars(
            select(DifferenceRecord)
            .where(DifferenceRecord.task_id == task_id)
            .order_by(DifferenceRecord.created_at, DifferenceRecord.id)
        )
        return tuple(self._item(record) for record in records)

    async def list_page(
        self,
        task_id: UUID,
        filters: DifferenceFilters,
    ) -> DifferencePage:
        statement = select(DifferenceRecord).where(DifferenceRecord.task_id == task_id)
        if filters.entity_type is not None:
            statement = statement.where(DifferenceRecord.entity_type == filters.entity_type.value)
        if filters.difference_type is not None:
            statement = statement.where(
                DifferenceRecord.difference_type == filters.difference_type.value
            )
        if filters.analysis_status is not None:
            statement = statement.where(DifferenceRecord.analysis_status == filters.analysis_status)
        if filters.risk is not None:
            statement = statement.where(DifferenceRecord.risk == filters.risk)
        if filters.resolution_status is not None:
            statement = statement.where(
                DifferenceRecord.resolution_status == filters.resolution_status.value
            )
        if filters.cursor is not None:
            created_at, difference_id = _decode_cursor(filters.cursor)
            statement = statement.where(
                or_(
                    DifferenceRecord.created_at < created_at,
                    and_(
                        DifferenceRecord.created_at == created_at,
                        DifferenceRecord.id < difference_id,
                    ),
                )
            )
        records = list(
            await self.session.scalars(
                statement.order_by(
                    DifferenceRecord.created_at.desc(),
                    DifferenceRecord.id.desc(),
                ).limit(filters.limit + 1)
            )
        )
        has_more = len(records) > filters.limit
        page_records = records[: filters.limit]
        next_cursor = (
            _encode_cursor(page_records[-1].created_at, page_records[-1].id)
            if has_more and page_records
            else None
        )
        return DifferencePage(
            items=tuple(self._item(record) for record in page_records),
            next_cursor=next_cursor,
        )

    async def _find_existing_many(
        self,
        evidence_hashes: Iterable[str],
    ) -> dict[tuple[UUID, UUID, UUID, str, str], DifferenceRecord]:
        hashes = tuple(evidence_hashes)
        if not hashes:
            return {}
        found: dict[tuple[UUID, UUID, UUID, str, str], DifferenceRecord] = {}
        for offset in range(0, len(hashes), 500):
            records = await self.session.scalars(
                select(DifferenceRecord).where(
                    DifferenceRecord.evidence_hash.in_(hashes[offset : offset + 500])
                )
            )
            found.update((_record_key(record), record) for record in records)
        return found

    @staticmethod
    def _values(draft: DifferenceDraft, evidence_hash: str) -> dict[str, object]:
        evidence = draft.evidence
        return {
            "id": uuid4(),
            "task_id": draft.task_id,
            "tenant_id": draft.tenant_id,
            "source_snapshot_id": evidence.source_snapshot_id,
            "target_snapshot_id": evidence.target_snapshot_id,
            "mapping_id": evidence.mapping_id,
            "source_entity_id": evidence.source_entity_id,
            "target_entity_id": evidence.target_entity_id,
            "entity_type": draft.entity_type.value,
            "difference_type": draft.difference_type.value,
            "resolution_status": draft.status.value,
            "analysis_status": "pending",
            "risk": None,
            "proposed_action": draft.proposed_action.value,
            "evidence": evidence.model_dump(mode="json"),
            "comparison_rule_version": evidence.comparison_rule_version,
            "evidence_hash": evidence_hash,
            "version": draft.version,
            "created_at": datetime.now(UTC),
        }

    @staticmethod
    def _item(record: DifferenceRecord) -> DifferenceItem:
        return DifferenceItem(
            id=record.id,
            task_id=record.task_id,
            tenant_id=record.tenant_id,
            entity_type=EntityType(record.entity_type),
            difference_type=DifferenceType(record.difference_type),
            proposed_action=DifferenceAction(record.proposed_action),
            evidence=DifferenceEvidence.model_validate(record.evidence),
            status=DifferenceStatus(record.resolution_status),
            version=record.version,
            created_at=record.created_at,
            analysis_status=record.analysis_status,
            risk=record.risk,
        )


def _encode_cursor(created_at: datetime, difference_id: UUID) -> str:
    payload = json.dumps(
        {"created_at": created_at.isoformat(), "id": str(difference_id)},
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
        return datetime.fromisoformat(payload["created_at"]), UUID(payload["id"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise ValueError("invalid difference cursor") from error


def _draft_key(
    draft: DifferenceDraft,
    evidence_hash: str,
) -> tuple[UUID, UUID, UUID, str, str]:
    return (
        draft.task_id,
        draft.evidence.source_snapshot_id,
        draft.evidence.target_snapshot_id,
        draft.entity_type.value,
        evidence_hash,
    )


def _record_key(record: DifferenceRecord) -> tuple[UUID, UUID, UUID, str, str]:
    return (
        record.task_id,
        record.source_snapshot_id,
        record.target_snapshot_id,
        record.entity_type,
        record.evidence_hash,
    )
