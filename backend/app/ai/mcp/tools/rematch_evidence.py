from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.rematching import (
    EntityRematchCandidateEdgeRecord,
    EntityRematchJobRecord,
    EntityRematchWorkItemRecord,
)


async def read_candidate_evidence(
    session: AsyncSession,
    *,
    task_id: UUID,
    tenant_id: str,
    work_item_id: UUID,
) -> dict[str, Any] | None:
    """Return bounded, non-sensitive evidence for one rematching work item."""
    item = await session.scalar(
        select(EntityRematchWorkItemRecord)
        .join(
            EntityRematchJobRecord,
            EntityRematchJobRecord.id == EntityRematchWorkItemRecord.job_id,
        )
        .where(
            EntityRematchWorkItemRecord.id == work_item_id,
            EntityRematchWorkItemRecord.tenant_id == tenant_id,
            EntityRematchJobRecord.tenant_id == tenant_id,
            EntityRematchJobRecord.task_id == task_id,
        )
    )
    if item is None:
        return None
    job = await session.get(EntityRematchJobRecord, item.job_id)
    if job is None or job.tenant_id != tenant_id or job.task_id != task_id:
        return None
    edges = tuple(
        await session.scalars(
            select(EntityRematchCandidateEdgeRecord)
            .where(
                EntityRematchCandidateEdgeRecord.work_item_id == item.id,
                EntityRematchCandidateEdgeRecord.job_id == job.id,
                EntityRematchCandidateEdgeRecord.tenant_id == tenant_id,
            )
            .order_by(EntityRematchCandidateEdgeRecord.rank)
        )
    )
    return {
        "job_id": str(job.id),
        "work_item_id": str(item.id),
        "entity_type": item.entity_type,
        "focal_entity_id": str(item.focal_entity_id),
        "focal_role": item.focal_role,
        "source_snapshot_id": str(job.source_snapshot_id),
        "target_snapshot_id": str(job.target_snapshot_id),
        "policy_version": job.policy_version,
        "candidates": [
            {
                "candidate_entity_id": str(edge.candidate_entity_id),
                "candidate_role": edge.candidate_role,
                "rank": edge.rank,
                "vector_score": edge.vector_score,
                "lexical_score": edge.lexical_score,
                "representation_version": edge.representation_version,
                "evidence": edge.evidence,
            }
            for edge in edges
        ],
    }
