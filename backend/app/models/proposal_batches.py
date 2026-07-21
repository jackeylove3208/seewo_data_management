from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import JSON, ForeignKey, String, UniqueConstraint, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class ProposalBatchRecord(Base, TimestampMixin):
    __tablename__ = "proposal_batches"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    task_id: Mapped[UUID] = mapped_column(ForeignKey("reconciliation_tasks.id"), index=True)
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(128))
    preview_hash: Mapped[str] = mapped_column(String(64))
    created_by: Mapped[str] = mapped_column(String(128), index=True)
    result: Mapped[dict[str, Any]] = mapped_column(JSON().with_variant(JSONB(), "postgresql"))

    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "task_id",
            "idempotency_key",
            name="uq_proposal_batch_idempotency",
        ),
    )
