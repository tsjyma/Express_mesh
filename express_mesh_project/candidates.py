"""Static source-route candidates for the bounded Express-Mesh experiment."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import permutations
import json
from pathlib import Path

from .model import ExpressEdge, GridGraph, Node


@dataclass(frozen=True)
class RouteCandidate:
    """A complete route with an explicit ordered directed express sequence."""

    source: Node
    destination: Node
    routers: tuple[Node, ...]
    express_ids: tuple[int, ...]
    static_latency: int
    express_positions: tuple[int, ...] = ()

    @property
    def express_count(self) -> int:
        return len(self.express_ids)


@dataclass
class RouteExecutionState:
    """Mutable progress for one packet committed to a RouteCandidate."""

    candidate: RouteCandidate
    router_index: int = 0
    express_stage: int = 0

    @property
    def current_router(self) -> Node:
        return self.candidate.routers[self.router_index]

    @property
    def completed(self) -> bool:
        return self.router_index == len(self.candidate.routers) - 1

    def next_router(self) -> Node | None:
        if self.completed:
            return None
        return self.candidate.routers[self.router_index + 1]

    def advance(self) -> tuple[Node, bool]:
        """Advance one link and report whether it was an express traversal."""
        if self.completed:
            raise ValueError("route is already complete")
        next_router = self.candidate.routers[self.router_index + 1]
        express = False
        if self.express_stage < self.candidate.express_count:
            express_id = self.candidate.express_ids[self.express_stage]
            position = self.candidate.express_positions[self.express_stage]
            if self.router_index == position:
                express = True
                self.express_stage += 1
        self.router_index += 1
        return next_router, express



def candidate_cost(
    candidate: RouteCandidate,
    q: dict[int, float] | None = None,
    r: dict[int, float] | None = None,
    use_reservations: bool = False,
) -> float:
    """Return the checklist cost for a complete, already generated route."""
    q = q or {}
    r = r or {}
    congestion = sum(q.get(edge_id, 0.0) for edge_id in candidate.express_ids)
    if use_reservations:
        congestion += sum(r.get(edge_id, 0.0) for edge_id in candidate.express_ids)
    return candidate.static_latency + congestion


def choose_candidate(
    candidates: tuple[RouteCandidate, ...],
    q: dict[int, float] | None = None,
    r: dict[int, float] | None = None,
    *,
    use_reservations: bool = False,
) -> RouteCandidate:
    """Choose one complete route; ties use static candidate ordering."""
    if not candidates:
        raise ValueError("candidate set must not be empty")
    return min(
        candidates,
        key=lambda route: (
            candidate_cost(route, q, r, use_reservations),
            route.static_latency,
            route.express_count,
            route.express_ids,
        ),
    )


def sequential_reservation_assignment(
    requests: list[tuple[tuple[RouteCandidate, ...], tuple[int, ...]]],
    q: dict[int, float] | None = None,
) -> tuple[list[RouteCandidate], dict[int, float]]:
    """Assign a same-cycle batch sequentially and update reservations immediately.

    Each request supplies candidates and the express IDs whose reservations are
    released when its packet reaches those express tails.  The returned map is
    guaranteed non-negative and can be checked after packet completion.
    """
    queue = dict(q or {})
    reservations: dict[int, float] = {}
    selected = []
    for candidates, release_ids in requests:
        route = min(
            candidates,
            key=lambda item: (
                candidate_cost(item, queue, reservations, use_reservations=True),
                item.static_latency,
                item.express_count,
                item.express_ids,
            ),
        )
        selected.append(route)
        for edge_id in route.express_ids:
            reservations[edge_id] = reservations.get(edge_id, 0.0) + 1
        for edge_id in release_ids:
            reservations[edge_id] = reservations.get(edge_id, 0.0) - 1
            if reservations[edge_id] < 0:
                raise AssertionError(f"negative reservation for express edge {edge_id}")
    return selected, reservations


def _xy_segment(graph: GridGraph, source: Node, destination: Node) -> tuple[Node, ...]:
    """Return deterministic XY vertices, including both endpoints."""
    x, y = graph.coordinate(source)
    dx, dy = graph.coordinate(destination)
    path = [source]
    while x != dx:
        x += 1 if dx > x else -1
        path.append(graph.node(x, y))
    while y != dy:
        y += 1 if dy > y else -1
        path.append(graph.node(x, y))
    return tuple(path)


def _directed_express_edges(graph: GridGraph):
    for express_id, edge in enumerate(graph.express_edges):
        yield express_id * 2, edge.u, edge.v, edge.latency
        yield express_id * 2 + 1, edge.v, edge.u, edge.latency


def _compose(graph: GridGraph, source: Node, destination: Node, directed):
    first_target = directed[0][1] if directed else destination
    routers = list(_xy_segment(graph, source, first_target))
    latency = len(routers) - 1
    express_ids = []
    express_positions = []
    for index, (express_id, entry, exit_node, express_latency) in enumerate(directed):
        if routers[-1] != entry:
            return None
        express_positions.append(len(routers) - 1)
        routers.append(exit_node)
        latency += express_latency
        express_ids.append(express_id)
        next_target = destination if index + 1 == len(directed) else directed[index + 1][1]
        segment = _xy_segment(graph, exit_node, next_target)
        routers.extend(segment[1:])
        latency += len(segment) - 1
    if routers[-1] != destination or len(set(routers)) != len(routers):
        return None
    return RouteCandidate(source, destination, tuple(routers), tuple(express_ids), latency,
                          tuple(express_positions))


def enumerate_candidates(
    graph: GridGraph, source: Node, destination: Node, k: int = 8
) -> tuple[RouteCandidate, ...]:
    """Enumerate loop-free routes using at most two directed express links."""
    if source == destination:
        raise ValueError("source and destination must differ")
    if k < 1:
        raise ValueError("k must be positive")
    directed = tuple(_directed_express_edges(graph))
    candidates = []
    for count in range(3):
        for sequence in permutations(directed, count):
            candidate = _compose(graph, source, destination, sequence)
            if candidate is not None:
                candidates.append(candidate)
    candidates.sort(key=lambda item: (item.static_latency, item.express_count, item.express_ids))
    pure_mesh = next(item for item in candidates if item.express_count == 0)
    selected = list(candidates[:k])
    if pure_mesh not in selected:
        selected[-1] = pure_mesh
    return tuple(sorted(set(selected), key=lambda item: (item.static_latency, item.express_count, item.express_ids)))


def all_candidates(graph: GridGraph, k: int = 8) -> dict[tuple[Node, Node], tuple[RouteCandidate, ...]]:
    return {
        (source, destination): enumerate_candidates(graph, source, destination, k)
        for source in range(graph.node_count)
        for destination in range(graph.node_count)
        if source != destination
    }


def candidate_diagnostics(candidates: dict[tuple[Node, Node], tuple[RouteCandidate, ...]]) -> dict[str, object]:
    counts = [len(routes) for routes in candidates.values()]
    total = len(counts)
    fractions = {
        str(count): sum(any(route.express_count == count for route in routes) for routes in candidates.values()) / total
        for count in range(3)
    }
    return {
        "pair_count": total,
        "candidate_count_min": min(counts, default=0),
        "candidate_count_max": max(counts, default=0),
        "candidate_count_mean": sum(counts) / total if total else 0.0,
        "pair_fraction_with_express_count": fractions,
    }


def candidate_bundle(candidates: dict[tuple[Node, Node], tuple[RouteCandidate, ...]]) -> dict[str, object]:
    """Return a JSON-safe representation for runtime integration and review."""
    routes = []
    for (source, destination), options in sorted(candidates.items()):
        routes.append({
            "source": source,
            "destination": destination,
            "candidates": [
                {
                    "routers": list(route.routers),
                    "express_ids": list(route.express_ids),
                    "express_positions": list(route.express_positions),
                    "static_latency": route.static_latency,
                }
                for route in options
            ],
        })
    return {"diagnostics": candidate_diagnostics(candidates), "routes": routes}


def write_candidate_bundle(
    candidates: dict[tuple[Node, Node], tuple[RouteCandidate, ...]], path: str | Path
) -> None:
    Path(path).write_text(json.dumps(candidate_bundle(candidates), indent=2) + "\n", encoding="utf-8")
