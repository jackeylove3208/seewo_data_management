from typing import Protocol

from app.schemas.executions import (
    GovernanceOperation,
    OperationType,
    VerificationResult,
    json_values_equal,
)


class ReadableMutationSession(Protocol):
    async def read_entity(self, identifier: str) -> dict[str, object] | None: ...


class TargetVerifier:
    async def verify(
        self,
        session: ReadableMutationSession,
        operation: GovernanceOperation,
    ) -> VerificationResult:
        if operation.operation_type is OperationType.SKIP:
            return VerificationResult(valid=True, actual=None)
        identifier = operation.target_source_identifier
        if operation.operation_type is OperationType.CREATE:
            identifier = str((operation.after or {}).get("source_id") or identifier or "")
        if not identifier:
            return VerificationResult(
                valid=False,
                actual=None,
                mismatches={"identity": {"expected": "present", "actual": None}},
            )
        actual = await session.read_entity(identifier)
        if operation.restore_absence:
            return VerificationResult(
                valid=actual is None,
                actual=actual,
                mismatches=(
                    {} if actual is None else {"identity": {"expected": None, "actual": identifier}}
                ),
            )
        expected = operation.after or {}
        fields = operation.changed_fields or frozenset(expected)
        mismatches = {
            field: {
                "expected": expected.get(field),
                "actual": actual.get(field) if actual is not None else None,
            }
            for field in sorted(fields)
            if actual is None
            or field not in actual
            or not json_values_equal(expected.get(field), actual.get(field))
        }
        return VerificationResult(
            valid=not mismatches,
            actual=actual,
            mismatches=mismatches,
        )
