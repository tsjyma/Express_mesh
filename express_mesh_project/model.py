"""Graph, traffic, and metric primitives for express-mesh placement."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import heapq
import math
from statistics import fmean, pstdev


Node = int
Edge = tuple[Node, Node]
Demand = dict[tuple[Node, Node], float]


def canonical_edge(u: Node, v: Node) -> Edge:
    if u == v:
        raise ValueError("self edges are not allowed")
    return (u, v) if u < v else (v, u)


@dataclass(frozen=True)
class ExpressEdge:
    u: Node
    v: Node
    wire_length: int
    latency: int

    @property
    def key(self) -> Edge:
        return canonical_edge(self.u, self.v)


class GridGraph:
    def __init__(self, n: int, express_edges: tuple[ExpressEdge, ...] = ()):
        if n < 2:
            raise ValueError("mesh dimension must be at least 2")
        self.n = n
        self.node_count = n * n
        self.express_edges = express_edges
        self.adjacency: list[dict[Node, int]] = [dict() for _ in range(self.node_count)]
        self.mesh_edges: set[Edge] = set()

        for y in range(n):
            for x in range(n):
                u = self.node(x, y)
                if x + 1 < n:
                    self._add_edge(u, self.node(x + 1, y), 1, mesh=True)
                if y + 1 < n:
                    self._add_edge(u, self.node(x, y + 1), 1, mesh=True)
        for edge in express_edges:
            self._add_edge(edge.u, edge.v, edge.latency, mesh=False)

    def node(self, x: int, y: int) -> Node:
        return y * self.n + x

    def coordinate(self, node: Node) -> tuple[int, int]:
        return node % self.n, node // self.n

    def manhattan(self, u: Node, v: Node) -> int:
        ux, uy = self.coordinate(u)
        vx, vy = self.coordinate(v)
        return abs(ux - vx) + abs(uy - vy)

    def _add_edge(self, u: Node, v: Node, latency: int, *, mesh: bool) -> None:
        if not 0 <= u < self.node_count or not 0 <= v < self.node_count:
            raise ValueError(f"edge endpoint outside mesh: {(u, v)}")
        if latency < 1:
            raise ValueError("edge latency must be positive")
        key = canonical_edge(u, v)
        if v in self.adjacency[u]:
            raise ValueError(f"duplicate edge: {key}")
        self.adjacency[u][v] = latency
        self.adjacency[v][u] = latency
        if mesh:
            self.mesh_edges.add(key)

    @property
    def all_edges(self) -> list[Edge]:
        return [
            (u, v)
            for u in range(self.node_count)
            for v in self.adjacency[u]
            if u < v
        ]

    def with_express(self, edges: list[ExpressEdge]) -> "GridGraph":
        return GridGraph(self.n, tuple(edges))


def uniform_demand(node_count: int) -> Demand:
    weight = 1.0 / (node_count * (node_count - 1))
    return {
        (source, dest): weight
        for source in range(node_count)
        for dest in range(node_count)
        if source != dest
    }


def cutstress_demand(n: int) -> Demand:
    pairs = []
    for y in range(n):
        for x in range(n // 2):
            source = y * n + x
            dest = y * n + (n - 1 - x)
            pairs.append((source, dest))
    weight = 1.0 / len(pairs)
    return {pair: weight for pair in pairs}


def permutation_demand(n: int, mapping) -> Demand:
    """Balanced one-to-one traffic: every router injects to one router.

    Unlike hotspot traffic, this does not create an unavoidable concentration
    at one destination NI.  It is therefore useful for measuring whether a
    topology actually matches a non-uniform communication pattern.
    """
    pairs = []
    for source in range(n * n):
        dest = mapping(source)
        if not 0 <= dest < n * n or dest == source:
            raise ValueError(f"invalid permutation mapping {source} -> {dest}")
        pairs.append((source, dest))
    if len({dest for _, dest in pairs}) != n * n:
        raise ValueError("traffic mapping is not a permutation")
    weight = 1.0 / len(pairs)
    return {pair: weight for pair in pairs}


def bit_complement_demand(n: int) -> Demand:
    """Common synthetic traffic: (x, y) -> (n-1-x, n-1-y)."""
    if n % 2:
        raise ValueError("bit-complement traffic requires an even dimension")
    return permutation_demand(n, lambda source: n * n - 1 - source)


def tornado_demand(n: int) -> Demand:
    """Common synthetic traffic: a fixed horizontal displacement."""
    if n < 4:
        raise ValueError("tornado traffic requires dimension >= 4")
    offset = n // 2 - 1
    return permutation_demand(
        n,
        lambda source: (source // n) * n + (source % n + offset) % n,
    )


def hotspot_demand(n: int, hotspot_probability: float = 0.5) -> Demand:
    if not 0.0 <= hotspot_probability <= 1.0:
        raise ValueError("hotspot probability must be in [0, 1]")
    node_count = n * n
    hotspots = {
        (n // 2 - 1) * n + (n // 2 - 1),
        (n // 2) * n + (n // 2),
    }
    demand: defaultdict[tuple[int, int], float] = defaultdict(float)
    for source in range(node_count):
        hot = [dest for dest in hotspots if dest != source]
        regular = [dest for dest in range(node_count) if dest != source]
        if hot:
            for dest in hot:
                demand[(source, dest)] += hotspot_probability / node_count / len(hot)
        for dest in regular:
            demand[(source, dest)] += (1.0 - hotspot_probability) / node_count / len(regular)
    return dict(demand)


def _shortest_paths(graph: GridGraph, source: Node):
    count = graph.node_count
    distance = [math.inf] * count
    sigma = [0] * count
    predecessors: list[list[Node]] = [[] for _ in range(count)]
    order: list[Node] = []
    distance[source] = 0
    sigma[source] = 1
    queue = [(0, source)]

    while queue:
        dist_u, u = heapq.heappop(queue)
        if dist_u != distance[u]:
            continue
        order.append(u)
        for v, latency in graph.adjacency[u].items():
            candidate = dist_u + latency
            if candidate < distance[v]:
                distance[v] = candidate
                sigma[v] = sigma[u]
                predecessors[v] = [u]
                heapq.heappush(queue, (candidate, v))
            elif candidate == distance[v]:
                sigma[v] += sigma[u]
                predecessors[v].append(u)
    return distance, sigma, predecessors, order


def core_metrics(graph: GridGraph, demand: Demand) -> dict[str, object]:
    edge_load = {edge: 0.0 for edge in graph.all_edges}
    weighted_aspl = 0.0
    diameter = 0
    weighted_shortest_path_count = 0.0

    demand_by_source: defaultdict[int, dict[int, float]] = defaultdict(dict)
    for (source, dest), value in demand.items():
        if source != dest and value:
            demand_by_source[source][dest] = value

    for source in range(graph.node_count):
        distance, sigma, predecessors, order = _shortest_paths(graph, source)
        target_weight = demand_by_source[source]
        dependency = [target_weight.get(node, 0.0) for node in range(graph.node_count)]
        for dest, value in target_weight.items():
            weighted_aspl += value * distance[dest]
            weighted_shortest_path_count += value * sigma[dest]
        diameter = max(diameter, max(distance))

        for node in reversed(order):
            if sigma[node] == 0:
                continue
            for predecessor in predecessors[node]:
                contribution = (sigma[predecessor] / sigma[node]) * dependency[node]
                edge_load[canonical_edge(predecessor, node)] += contribution
                dependency[predecessor] += contribution

    loads = sorted(edge_load.values())
    p95_index = max(0, math.ceil(0.95 * len(loads)) - 1)
    mean_load = fmean(loads)
    return {
        "aspl": weighted_aspl,
        "diameter": diameter,
        "l_max": max(loads),
        "p95_load": loads[p95_index],
        "load_cv": pstdev(loads) / mean_load if mean_load else 0.0,
        "equal_cost_path_count": weighted_shortest_path_count,
        "edge_load": edge_load,
    }


def near_shortest_diversity(
    graph: GridGraph, demand: Demand, rho: float = 1.5
) -> float:
    """Demand-weighted candidates using zero or one express edge.

    Mesh-only segments use deterministic Manhattan length. Both orientations
    of every express edge are considered. This matches the first routing MVP.
    """
    all_distances = [
        _shortest_paths(graph, source)[0]
        for source in range(graph.node_count)
    ]
    diversity = 0.0
    for (source, dest), weight in demand.items():
        shortest = all_distances[source][dest]
        candidates = 1
        for edge in graph.express_edges:
            for entry, exit_node in ((edge.u, edge.v), (edge.v, edge.u)):
                length = (
                    graph.manhattan(source, entry)
                    + edge.latency
                    + graph.manhattan(exit_node, dest)
                )
                if length <= rho * shortest:
                    candidates += 1
        diversity += weight * candidates
    return diversity


def public_metrics(graph: GridGraph, demand: Demand, rho: float = 1.5) -> dict[str, float]:
    metrics = core_metrics(graph, demand)
    metrics.pop("edge_load")
    metrics["near_shortest_diversity"] = near_shortest_diversity(graph, demand, rho)
    return metrics
