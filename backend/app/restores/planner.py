import hashlib
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from app.governance.dependency_graph import stable_topological_order
from app.models.executions import TargetVersionRecord
from app.repositories.executions import ExecutionRepository
from app.restores.path import VersionPathResolver
from app.schemas.executions import GovernanceOperation, OperationStatus, OperationType
from app.schemas.governance import RiskLevel
from app.schemas.reporting import RestoreConflict


class RestorePlanResult(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    source_version_id: UUID
    target_version_id: UUID
    allowed: bool
    conflicts: tuple[RestoreConflict, ...]
    operations: tuple[GovernanceOperation, ...]
    covered_execution_ids: tuple[UUID, ...]


class HistoricalRestorePlanner:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.executions = ExecutionRepository(session)
        self.paths = VersionPathResolver(session)

    async def plan(
        self,
        *,
        source_version_id: UUID,
        target_version_id: UUID,
        tenant_id: str,
    ) -> RestorePlanResult:
        source = await self._version(source_version_id, tenant_id)
        target = await self._version(target_version_id, tenant_id)
        if source.task_id != target.task_id:
            raise ValueError("restore versions belong to different tasks")
        backward, forward = await self.paths.split(source.id, target.id)
        operations: list[GovernanceOperation] = []
        conflicts: list[RestoreConflict] = []
        covered: list[UUID] = []
        for version in backward:
            edge_operations, edge_conflicts = await self._edge(version, inverse=True)
            operations.extend(edge_operations)
            conflicts.extend(edge_conflicts)
            if version.batch_id is not None:
                covered.append(version.batch_id)
        for version in forward:
            edge_operations, edge_conflicts = await self._edge(version, inverse=False)
            operations.extend(edge_operations)
            conflicts.extend(edge_conflicts)
            if version.batch_id is not None:
                covered.append(version.batch_id)
        chained: list[GovernanceOperation] = []
        previous: UUID | None = None
        for operation in operations:
            chained_operation = operation.model_copy(
                update={"dependencies": frozenset({previous}) if previous else frozenset()}
            )
            chained.append(chained_operation)
            previous = chained_operation.id
        return RestorePlanResult(
            source_version_id=source.id,
            target_version_id=target.id,
            allowed=not conflicts and bool(chained),
            conflicts=tuple(conflicts),
            operations=tuple(chained),
            covered_execution_ids=tuple(dict.fromkeys(covered)),
        )

    async def _version(self, version_id: UUID, tenant_id: str) -> TargetVersionRecord:
        version = await self.executions.get_target_version(version_id)
        if version is None or version.tenant_id != tenant_id:
            raise LookupError("target version not found")
        return version

    async def _edge(
        self,
        version: TargetVersionRecord,
        *,
        inverse: bool,
    ) -> tuple[list[GovernanceOperation], list[RestoreConflict]]:
        if version.batch_id is None:
            return [], [
                RestoreConflict(code="missing_execution", message="version has no execution")
            ]
        execution_operations = await self.executions.execution_operations(version.batch_id)
        stored = list(stable_topological_order(execution_operations))
        if inverse:
            stored.reverse()
        operations: list[GovernanceOperation] = []
        conflicts: list[RestoreConflict] = []
        for item in stored:
            attempts = await self.executions.list_attempts(item.record_id)
            latest = attempts[-1] if attempts else None
            verification = latest.verification if latest is not None else None
            if (
                latest is None
                or latest.status != OperationStatus.SUCCEEDED.value
                or not isinstance(verification, dict)
                or verification.get("valid") is not True
                or latest.target_version_id != version.id
            ):
                conflicts.append(
                    RestoreConflict(
                        code="uncertain_operation",
                        message="restore requires a verified successful operation",
                        operation_id=item.operation.id,
                    )
                )
                continue
            operations.append(_inverse(item.operation) if inverse else _replay(item.operation))
        return operations, conflicts


def _replay(operation: GovernanceOperation) -> GovernanceOperation:
    return operation.model_copy(
        update={
            "id": _restore_operation_id(operation.id, "replay"),
            "dependencies": frozenset(),
            "risk": RiskLevel.HIGH,
            "compensation_for": operation.id,
        }
    )


def _inverse(operation: GovernanceOperation) -> GovernanceOperation:
    if not operation.reversible:
        raise ValueError("irreversible operation blocks restore")
    if operation.operation_type is OperationType.CREATE:
        after = operation.after or {}
        identifier = operation.target_source_identifier or str(after.get("source_id") or "")
        return operation.model_copy(
            update={
                "id": _restore_operation_id(operation.id, "inverse"),
                "operation_type": OperationType.DISABLE,
                "target_source_identifier": identifier,
                "before": after,
                "after": after,
                "dependencies": frozenset(),
                "risk": RiskLevel.HIGH,
                "compensation_for": operation.id,
                "restore_absence": True,
            }
        )
    inverse_type = (
        OperationType.UPDATE
        if operation.operation_type is OperationType.DISABLE
        else operation.operation_type
    )
    return operation.model_copy(
        update={
            "id": _restore_operation_id(operation.id, "inverse"),
            "operation_type": inverse_type,
            "before": operation.after,
            "after": operation.before,
            "dependencies": frozenset(),
            "risk": RiskLevel.HIGH,
            "compensation_for": operation.id,
            "restore_absence": False,
        }
    )


def _restore_operation_id(operation_id: UUID, direction: str) -> UUID:
    digest = hashlib.sha256(f"historical-restore:{direction}:{operation_id}".encode()).digest()
    return UUID(bytes=digest[:16], version=4)
