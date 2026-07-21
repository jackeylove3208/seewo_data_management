from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.rematching_agent import RematchingAgent
from app.models.snapshots import CanonicalEntityRecord
from app.repositories.rematching import EntityRematchRepository
from app.schemas.rematching import CandidateEdge, KeyFieldEvidence, RematchDecision


@dataclass(frozen=True)
class RematchingContext:
    task_id: UUID
    tenant_id: str
    focal_entity_id: UUID
    focal_payload: dict[str, object]
    candidate_edges: tuple[CandidateEdge, ...]


class EntityRematchingService:
    def __init__(self, agent: RematchingAgent) -> None:
        self.agent = agent

    async def prepare(
        self, session: AsyncSession, *, item_id: UUID, tenant_id: str
    ) -> RematchingContext:
        repository = EntityRematchRepository(session)
        item = await repository.get_item_for_tenant(item_id, tenant_id)
        if item is None:
            raise LookupError(f"entity rematch work item not found: {item_id}")
        job = await repository.get_for_tenant(item.job_id, tenant_id)
        if job is None:
            raise LookupError(f"entity rematch job not found: {item.job_id}")
        focal = await session.get(CanonicalEntityRecord, item.focal_entity_id)
        if focal is None:
            raise LookupError(f"focal entity not found: {item.focal_entity_id}")
        edges = await repository.candidate_edges(item.id, tenant_id)
        return RematchingContext(
            task_id=job.task_id,
            tenant_id=tenant_id,
            focal_entity_id=item.focal_entity_id,
            focal_payload={"entity_type": item.entity_type, **focal.canonical_payload},
            candidate_edges=tuple(
                CandidateEdge(
                    focal_entity_id=edge.focal_entity_id,
                    focal_role=edge.focal_role,
                    candidate_entity_id=edge.candidate_entity_id,
                    candidate_role=edge.candidate_role,
                    rank=edge.rank,
                    vector_score=edge.vector_score,
                    lexical_score=edge.lexical_score,
                    representation_version=edge.representation_version,
                    evidence=tuple(
                        KeyFieldEvidence.model_validate(value)
                        for value in edge.evidence.get("fields", ())
                    ),
                )
                for edge in edges
            ),
        )

    async def decide(self, context: RematchingContext) -> RematchDecision:
        return await self.agent.decide(
            focal_entity_id=context.focal_entity_id,
            focal_payload=context.focal_payload,
            candidate_edges=context.candidate_edges,
            tenant_id=context.tenant_id,
            task_id=context.task_id,
        )
