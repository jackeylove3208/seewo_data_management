from dataclasses import dataclass
from uuid import UUID

import pytest

from app.governance.dependency_graph import DependencyGraphError, stable_topological_order


@dataclass(frozen=True)
class Node:
    id: UUID
    dependencies: frozenset[UUID] = frozenset()


def node(value: int, *dependencies: int) -> Node:
    return Node(
        id=UUID(int=value),
        dependencies=frozenset(UUID(int=dependency) for dependency in dependencies),
    )


def test_topological_order_places_dependencies_before_dependents() -> None:
    parent = node(9)
    child = node(1, 9)

    assert stable_topological_order((child, parent)) == (parent, child)


def test_topological_order_uses_uuid_tie_break_for_ready_operations() -> None:
    first = node(1)
    second = node(2)
    dependent = node(3, 2)

    assert stable_topological_order((dependent, second, first)) == (
        first,
        second,
        dependent,
    )


def test_missing_dependency_is_rejected() -> None:
    missing = UUID(int=99)

    with pytest.raises(DependencyGraphError, match=f"missing dependency {missing}"):
        stable_topological_order((node(1, 99),))


def test_self_dependency_is_rejected() -> None:
    with pytest.raises(DependencyGraphError, match="depend on itself"):
        stable_topological_order((node(1, 1),))


def test_cycle_is_rejected() -> None:
    with pytest.raises(DependencyGraphError, match="cycle"):
        stable_topological_order((node(1, 2), node(2, 1)))


def test_duplicate_operation_id_is_rejected() -> None:
    with pytest.raises(DependencyGraphError, match="duplicate operation id"):
        stable_topological_order((node(1), node(1)))
