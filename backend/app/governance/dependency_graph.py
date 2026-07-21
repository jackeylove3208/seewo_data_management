import heapq
from collections.abc import Sequence
from typing import Protocol
from uuid import UUID


class DependencyGraphError(ValueError):
    pass


class DependencyNode(Protocol):
    @property
    def id(self) -> UUID: ...

    @property
    def dependencies(self) -> frozenset[UUID]: ...


def stable_topological_order[NodeT: DependencyNode](
    nodes: Sequence[NodeT],
) -> tuple[NodeT, ...]:
    by_id: dict[UUID, NodeT] = {}
    for node in nodes:
        if node.id in by_id:
            raise DependencyGraphError(f"duplicate operation id: {node.id}")
        by_id[node.id] = node

    dependents: dict[UUID, set[UUID]] = {node_id: set() for node_id in by_id}
    remaining_dependencies: dict[UUID, int] = {}
    for node in nodes:
        if node.id in node.dependencies:
            raise DependencyGraphError(f"operation {node.id} cannot depend on itself")
        for dependency in node.dependencies:
            if dependency not in by_id:
                raise DependencyGraphError(
                    f"operation {node.id} has missing dependency {dependency}"
                )
            dependents[dependency].add(node.id)
        remaining_dependencies[node.id] = len(node.dependencies)

    ready = [
        node_id
        for node_id, dependency_count in remaining_dependencies.items()
        if dependency_count == 0
    ]
    heapq.heapify(ready)
    ordered: list[NodeT] = []
    while ready:
        node_id = heapq.heappop(ready)
        ordered.append(by_id[node_id])
        for dependent_id in dependents[node_id]:
            remaining_dependencies[dependent_id] -= 1
            if remaining_dependencies[dependent_id] == 0:
                heapq.heappush(ready, dependent_id)

    if len(ordered) != len(nodes):
        raise DependencyGraphError("dependency graph contains a cycle")
    return tuple(ordered)
