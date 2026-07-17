import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from app.governance.dependency_graph import (
    DependencyGraphError,
    stable_topological_order,
)
from app.governance.operation_policy import (
    PlanPolicyError,
    validate_editable_fields,
    validate_operation,
)
from app.governance.risk_policy import assess_operation
from app.schemas.canonical_entities import EntityType
from app.schemas.executions import (
    GovernanceOperation,
    GovernancePlan,
    ProposalStatus,
    ProposalVersionRef,
    ReviewedProposalSnapshot,
    canonical_json,
    json_values_equal,
)


class PlanCompilationError(ValueError):
    pass


class PlanConflictError(PlanCompilationError):
    pass


_OPERATION_NAMESPACE = uuid5(
    NAMESPACE_URL,
    "organization-reconciliation/governance-operation",
)
_PLAN_NAMESPACE = uuid5(
    NAMESPACE_URL,
    "organization-reconciliation/governance-plan",
)

TargetAlias = tuple[EntityType, str]


@dataclass(frozen=True)
class _ProposalNode:
    id: UUID
    dependencies: frozenset[UUID]
    proposal: ReviewedProposalSnapshot


class GovernancePlanBuilder:
    def build(
        self,
        *,
        task_id: UUID,
        source_snapshot_id: UUID,
        target_snapshot_id: UUID,
        target_version: str,
        proposals: Sequence[ReviewedProposalSnapshot],
        version: int = 1,
    ) -> GovernancePlan:
        if not proposals:
            raise PlanCompilationError("at least one proposal must be selected")

        selected = tuple(sorted(proposals, key=_proposal_sort_key))
        self._reject_duplicates(selected)
        for proposal in selected:
            self._validate_context(
                proposal,
                task_id=task_id,
                source_snapshot_id=source_snapshot_id,
                target_snapshot_id=target_snapshot_id,
                target_version=target_version,
            )

        proposal_nodes = self._order_proposals(selected)
        proposals_with_dependents = {
            dependency
            for node in proposal_nodes
            for dependency in node.dependencies
        }
        operations: list[GovernanceOperation] = []
        operation_ids_by_proposal: dict[UUID, UUID] = {}
        for node in proposal_nodes:
            operation = self._compile_operation(
                node.proposal,
                dependencies=frozenset(
                    operation_ids_by_proposal[dependency]
                    for dependency in node.dependencies
                ),
                has_dependents=node.id in proposals_with_dependents,
            )
            operations.append(operation)
            operation_ids_by_proposal[node.id] = operation.id

        try:
            operation_tuple = stable_topological_order(operations)
        except DependencyGraphError as exc:
            raise PlanCompilationError(str(exc)) from exc
        self._reject_conflicts(operation_tuple)
        proposal_refs = tuple(proposal.proposal for proposal in selected)
        content_hash = _content_hash(
            version=version,
            task_id=task_id,
            source_snapshot_id=source_snapshot_id,
            target_snapshot_id=target_snapshot_id,
            target_version=target_version,
            proposals=proposal_refs,
            operations=operation_tuple,
        )
        plan_id = uuid5(_PLAN_NAMESPACE, f"{task_id}:{version}:{content_hash}")
        return GovernancePlan(
            id=plan_id,
            version=version,
            task_id=task_id,
            source_snapshot_id=source_snapshot_id,
            target_snapshot_id=target_snapshot_id,
            target_version=target_version,
            proposals=proposal_refs,
            operations=operation_tuple,
            content_hash=content_hash,
        )

    @staticmethod
    def _reject_duplicates(proposals: Sequence[ReviewedProposalSnapshot]) -> None:
        seen: set[UUID] = set()
        for proposal in proposals:
            proposal_id = proposal.proposal.proposal_id
            if proposal_id in seen:
                raise PlanCompilationError(f"duplicate proposal selected: {proposal_id}")
            seen.add(proposal_id)

    @staticmethod
    def _order_proposals(
        proposals: Sequence[ReviewedProposalSnapshot],
    ) -> tuple[_ProposalNode, ...]:
        selected_ids = {proposal.proposal.proposal_id for proposal in proposals}
        nodes: list[_ProposalNode] = []
        for proposal in proposals:
            proposal_id = proposal.proposal.proposal_id
            if proposal_id in proposal.dependencies:
                raise PlanCompilationError(f"proposal {proposal_id} cannot depend on itself")
            unselected = proposal.dependencies - selected_ids
            if unselected:
                dependency = min(unselected)
                raise PlanCompilationError(
                    f"proposal {proposal_id} references unselected proposal {dependency}"
                )
            nodes.append(
                _ProposalNode(
                    id=proposal_id,
                    dependencies=proposal.dependencies,
                    proposal=proposal,
                )
            )
        try:
            return stable_topological_order(nodes)
        except DependencyGraphError as exc:
            raise PlanCompilationError(str(exc)) from exc

    @staticmethod
    def _validate_context(
        proposal: ReviewedProposalSnapshot,
        *,
        task_id: UUID,
        source_snapshot_id: UUID,
        target_snapshot_id: UUID,
        target_version: str,
    ) -> None:
        if proposal.status is not ProposalStatus.PENDING_EXECUTION:
            raise PlanCompilationError("proposal must be in pending_execution status")
        if proposal.proposal.proposal_version != proposal.current_proposal_version:
            raise PlanCompilationError("proposal version is not current")
        if proposal.difference_version != proposal.current_difference_version:
            raise PlanCompilationError("difference version is not current")
        if proposal.analysis_version != proposal.current_analysis_version:
            raise PlanCompilationError("analysis version is not current")
        if proposal.task_id != task_id:
            raise PlanCompilationError("proposal belongs to a different task")
        if proposal.source_snapshot_id != source_snapshot_id:
            raise PlanCompilationError("proposal source snapshot is stale")
        if proposal.target_snapshot_id != target_snapshot_id:
            raise PlanCompilationError("proposal target snapshot is stale")
        if proposal.target_version != target_version:
            raise PlanCompilationError("proposal target version is stale")

    @staticmethod
    def _compile_operation(
        proposal: ReviewedProposalSnapshot,
        *,
        dependencies: frozenset[UUID],
        has_dependents: bool,
    ) -> GovernanceOperation:
        validate_operation(proposal.difference_type, proposal.operation_type)
        fact_fields = frozenset((proposal.before or {}).keys()) | frozenset(
            (proposal.after or {}).keys()
        )
        validate_editable_fields(
            proposal.entity_type,
            fact_fields | proposal.changed_fields,
        )
        actual_changes = _changed_fact_fields(proposal.before, proposal.after)
        if actual_changes != proposal.changed_fields:
            raise PlanPolicyError(
                "changed_fields must exactly match the operation fact changes"
            )

        assessment = assess_operation(
            operation_type=proposal.operation_type,
            before=proposal.before,
            after=proposal.after,
            changed_fields=proposal.changed_fields,
            has_dependents=has_dependents,
        )

        provisional = GovernanceOperation(
            id=UUID(int=0),
            proposal=proposal.proposal,
            proposal_source=proposal.proposal_source,
            difference_id=proposal.difference_id,
            difference_version=proposal.difference_version,
            analysis_id=proposal.analysis_id,
            analysis_version=proposal.analysis_version,
            operation_type=proposal.operation_type,
            entity_type=proposal.entity_type,
            target_entity_id=proposal.target_entity_id,
            target_source_identifier=proposal.target_source_identifier,
            before=proposal.before,
            after=proposal.after,
            changed_fields=proposal.changed_fields,
            dependencies=dependencies,
            reversible=assessment.reversible,
            risk=assessment.risk,
            compensation_for=proposal.compensation_for,
            restore_absence=proposal.restore_absence,
        )
        operation_hash = _sha256(_canonical_operation_json(provisional))
        operation_id = uuid5(
            _OPERATION_NAMESPACE,
            (
                f"{proposal.proposal.proposal_id}:"
                f"{proposal.proposal.proposal_version}:{operation_hash}"
            ),
        )
        return provisional.model_copy(update={"id": operation_id})

    @staticmethod
    def _reject_conflicts(operations: Sequence[GovernanceOperation]) -> None:
        components = _target_components(operations)
        owners: dict[tuple[TargetAlias, str], UUID] = {}
        for operation in operations:
            aliases = _target_aliases(operation)
            if not aliases:
                continue
            component = components[aliases[0]]
            for field in operation.changed_fields:
                key = (component, field)
                prior = owners.get(key)
                if prior is not None:
                    raise PlanConflictError(
                        f"operations {prior} and {operation.id} conflict on target field {field}"
                    )
                owners[key] = operation.id


def _proposal_sort_key(
    proposal: ReviewedProposalSnapshot,
) -> tuple[str, int]:
    return (str(proposal.proposal.proposal_id), proposal.proposal.proposal_version)


def _changed_fact_fields(
    before: Mapping[str, Any] | None,
    after: Mapping[str, Any] | None,
) -> frozenset[str]:
    before_facts = before or {}
    after_facts = after or {}
    return frozenset(
        field
        for field in before_facts.keys() | after_facts.keys()
        if field not in before_facts
        or field not in after_facts
        or not json_values_equal(before_facts[field], after_facts[field])
    )


def _target_aliases(operation: GovernanceOperation) -> tuple[TargetAlias, ...]:
    aliases: list[TargetAlias] = []
    if operation.target_entity_id is not None:
        aliases.append((operation.entity_type, f"id:{operation.target_entity_id}"))
    if operation.target_source_identifier is not None:
        aliases.append(
            (operation.entity_type, f"source:{operation.target_source_identifier}")
        )
    return tuple(aliases)


def _target_components(
    operations: Sequence[GovernanceOperation],
) -> dict[TargetAlias, TargetAlias]:
    parents: dict[TargetAlias, TargetAlias] = {}
    for operation in operations:
        aliases = _target_aliases(operation)
        for alias in aliases:
            parents.setdefault(alias, alias)
        for alias in aliases[1:]:
            _union_aliases(parents, aliases[0], alias)
    return {alias: _find_alias(parents, alias) for alias in parents}


def _find_alias(
    parents: dict[TargetAlias, TargetAlias],
    alias: TargetAlias,
) -> TargetAlias:
    root = alias
    while parents[root] != root:
        root = parents[root]
    while parents[alias] != alias:
        parent = parents[alias]
        parents[alias] = root
        alias = parent
    return root


def _union_aliases(
    parents: dict[TargetAlias, TargetAlias],
    left: TargetAlias,
    right: TargetAlias,
) -> None:
    left_root = _find_alias(parents, left)
    right_root = _find_alias(parents, right)
    if left_root == right_root:
        return
    first, second = sorted((left_root, right_root), key=_alias_sort_key)
    parents[second] = first


def _alias_sort_key(alias: TargetAlias) -> tuple[str, str]:
    return (alias[0].value, alias[1])


def _canonical_operation_payload(operation: GovernanceOperation) -> dict[str, Any]:
    payload = operation.model_dump(mode="json", exclude={"id"})
    payload["changed_fields"] = sorted(operation.changed_fields)
    payload["dependencies"] = sorted(str(item) for item in operation.dependencies)
    return payload


def _canonical_operation_json(operation: GovernanceOperation) -> str:
    return canonical_json(_canonical_operation_payload(operation))


def _content_hash(
    *,
    version: int,
    task_id: UUID,
    source_snapshot_id: UUID,
    target_snapshot_id: UUID,
    target_version: str,
    proposals: Sequence[ProposalVersionRef],
    operations: Sequence[GovernanceOperation],
) -> str:
    payload = {
        "version": version,
        "task_id": str(task_id),
        "source_snapshot_id": str(source_snapshot_id),
        "target_snapshot_id": str(target_snapshot_id),
        "target_version": target_version,
        "proposals": [proposal.model_dump(mode="json") for proposal in proposals],
        "operations": [
            {"id": str(operation.id), **_canonical_operation_payload(operation)}
            for operation in operations
        ],
    }
    return _sha256(canonical_json(payload))


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
