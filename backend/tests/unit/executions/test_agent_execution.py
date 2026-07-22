from dataclasses import dataclass
from uuid import uuid4

import pytest

from app.executions.agent_service import (
    AgentExecutionService,
    AgentRetryableTargetError,
)
from app.governance.agent_governance import AgentGovernanceOperation, AgentOperation


def operation(*, name: str, dependencies=frozenset(), op: str = "update"):
    return AgentGovernanceOperation(
        id=uuid4(),
        finding_id=uuid4(),
        operation=AgentOperation(op),
        entity_kind="student",
        target_source_identifier=name,
        before={"name": "old"},
        after={"name": name},
        dependencies=dependencies,
        risk="medium",
        target_version="sha256:" + "a" * 64,
    )


@dataclass
class FakeSession:
    failures: dict[str, int]
    applied: list[str]

    async def apply_operation(self, converted) -> None:
        identifier = converted.target_source_identifier
        remaining = self.failures.get(identifier, 0)
        if remaining:
            self.failures[identifier] = remaining - 1
            raise AgentRetryableTargetError("temporary")
        self.applied.append(identifier)

    async def read_entity(self, identifier: str):
        return {"name": identifier} if identifier in self.applied else None

    async def finalize(self):
        return "new-target-version"

    async def abort(self):
        return None


@dataclass
class FakeTarget:
    session: FakeSession

    async def begin(self, target_version: str, *, plan_id):
        assert target_version.startswith("sha256:")
        return self.session


@dataclass
class OutcomeSink:
    outcomes: dict | None = None

    def __post_init__(self) -> None:
        self.outcomes = {}

    async def record_operation_outcome(self, operation_id, **values):
        assert self.outcomes is not None
        self.outcomes[operation_id] = values


@pytest.mark.asyncio
async def test_partial_failure_continues_independent_and_blocks_dependants() -> None:
    first = operation(name="A", dependencies=frozenset())
    independent = operation(name="B", dependencies=frozenset())
    dependent = operation(name="C", dependencies=frozenset({first.id}))
    session = FakeSession(failures={"A": 4}, applied=[])
    sink = OutcomeSink()

    result = await AgentExecutionService().execute(
        plan_id=uuid4(),
        target_version=first.target_version,
        operations=(first, independent, dependent),
        target=FakeTarget(session),
        outcome_sink=sink,
    )

    assert result.by_operation[first.id].status == "failed"
    assert result.by_operation[independent.id].status == "succeeded"
    assert result.by_operation[dependent.id].status == "blocked"
    assert result.by_operation[first.id].attempts == 4
    assert session.applied == ["B"]
    assert sink.outcomes is not None
    assert sink.outcomes[first.id]["status"] == "failed"
    assert sink.outcomes[dependent.id]["error_code"] == "dependency_failed"


@pytest.mark.asyncio
async def test_authority_target_is_rejected_before_any_write() -> None:
    op = operation(name="A")
    with pytest.raises(ValueError, match="target"):
        await AgentExecutionService().execute(
            plan_id=uuid4(),
            target_version=op.target_version,
            operations=(op,),
            target=FakeTarget(FakeSession({}, [])),
            target_role="authoritative",
        )
