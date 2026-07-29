from dataclasses import dataclass
from typing import Literal, Protocol, cast
from uuid import UUID

SourceRoleName = Literal["authoritative", "target"]
ConnectorKind = Literal["csv", "local", "remote_csv", "database", "api"]

_CONNECTOR_KINDS = frozenset({"csv", "local", "remote_csv", "database", "api"})


class _TaskWithIntent(Protocol):
    agent_intent: object


@dataclass(frozen=True, slots=True)
class AgentSourceBinding:
    role: SourceRoleName
    connector_kind: ConnectorKind
    configuration_id: str | None
    snapshot_id: UUID | None
    mapping_checkpoint_key: str
    normalization_checkpoint_key: str


def resolve_source_bindings(task: _TaskWithIntent) -> tuple[AgentSourceBinding, ...]:
    if not isinstance(task.agent_intent, dict):
        raise ValueError("Agent task intent is missing")

    bindings: list[AgentSourceBinding] = []
    for role, intent_key in (
        ("authoritative", "source"),
        ("target", "target"),
    ):
        selection = task.agent_intent.get(intent_key)
        if not isinstance(selection, dict):
            raise ValueError(f"Agent {role} selection is missing")
        kind = selection.get("kind")
        if not isinstance(kind, str) or kind not in _CONNECTOR_KINDS:
            raise ValueError(f"Agent {role} connector kind is invalid")
        configuration_id = selection.get("configuration_id")
        if configuration_id is not None and not isinstance(configuration_id, str):
            raise ValueError(f"Agent {role} configuration ID is invalid")
        mapping_kind = (
            "api"
            if kind == "api"
            else "database"
            if kind == "database"
            else "csv"
        )
        bindings.append(
            AgentSourceBinding(
                role=cast(SourceRoleName, role),
                connector_kind=cast(ConnectorKind, kind),
                configuration_id=configuration_id,
                snapshot_id=None,
                mapping_checkpoint_key=f"graph-{mapping_kind}-field-mapping-v3:{role}",
                normalization_checkpoint_key=f"graph-source-normalization-v3:{role}",
            )
        )
    return tuple(bindings)
