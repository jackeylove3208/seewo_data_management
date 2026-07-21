from collections.abc import Sequence

from app.schemas.canonical_entities import EntityType
from app.schemas.executions import (
    OperationType,
    ReviewedProposalSnapshot,
)


class DependencyBindingError(ValueError):
    pass


_RELATION_TARGETS: dict[
    tuple[EntityType, str],
    tuple[EntityType, ...],
] = {
    (EntityType.ORGANIZATION_UNIT, "parent_source_id"): (EntityType.ORGANIZATION_UNIT,),
    (EntityType.CLASS, "parent_source_id"): (EntityType.ORGANIZATION_UNIT,),
    (EntityType.TEACHER, "department_source_id"): (EntityType.ORGANIZATION_UNIT,),
    (EntityType.STUDENT, "class_source_id"): (EntityType.CLASS,),
    (EntityType.MEMBERSHIP, "member_source_id"): (
        EntityType.TEACHER,
        EntityType.STUDENT,
    ),
    (EntityType.MEMBERSHIP, "container_source_id"): (
        EntityType.CLASS,
        EntityType.ORGANIZATION_UNIT,
    ),
}


def bind_selected_dependencies(
    proposals: Sequence[ReviewedProposalSnapshot],
) -> tuple[ReviewedProposalSnapshot, ...]:
    creates: dict[tuple[EntityType, str], ReviewedProposalSnapshot] = {}
    for proposal in proposals:
        if proposal.operation_type is not OperationType.CREATE or proposal.after is None:
            continue
        source_id = proposal.after.get("source_id")
        if not isinstance(source_id, str) or not source_id:
            raise DependencyBindingError("create proposal is missing source_id")
        alias = (proposal.entity_type, source_id)
        if alias in creates:
            raise DependencyBindingError(
                f"duplicate selected create identity: {proposal.entity_type.value}/{source_id}"
            )
        creates[alias] = proposal

    bound: list[ReviewedProposalSnapshot] = []
    for proposal in proposals:
        dependencies = set(proposal.dependencies)
        facts = proposal.after or {}
        for (entity_type, field), target_types in _RELATION_TARGETS.items():
            if proposal.entity_type is not entity_type:
                continue
            source_id = facts.get(field)
            if not isinstance(source_id, str) or not source_id:
                continue
            matches = [
                creates[(target_type, source_id)]
                for target_type in target_types
                if (target_type, source_id) in creates
            ]
            if len(matches) > 1:
                raise DependencyBindingError(
                    f"ambiguous selected dependency for {field}: {source_id}"
                )
            if matches:
                dependency_id = matches[0].proposal.proposal_id
                if dependency_id != proposal.proposal.proposal_id:
                    dependencies.add(dependency_id)
        bound.append(proposal.model_copy(update={"dependencies": frozenset(dependencies)}))
    return tuple(bound)
