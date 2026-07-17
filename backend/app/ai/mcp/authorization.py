from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.repositories.differences import DifferenceRepository
from app.schemas.differences import DifferenceItem


class ToolAuthorizationError(PermissionError):
    pass


class ToolContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    operator_id: str = Field(min_length=1, max_length=255)
    tenant_id: str = Field(min_length=1, max_length=128)
    task_id: UUID
    allowed_difference_ids: frozenset[UUID] = frozenset()


async def require_difference(
    context: ToolContext,
    difference_id: UUID,
    repository: DifferenceRepository,
) -> DifferenceItem:
    if difference_id not in context.allowed_difference_ids:
        raise ToolAuthorizationError("difference not authorized")
    difference = await repository.get(difference_id)
    if (
        difference is None
        or difference.task_id != context.task_id
        or difference.tenant_id != context.tenant_id
    ):
        raise ToolAuthorizationError("difference not authorized")
    return difference
