"""Dependency-free flow proxies for traffic-aware express-link placement.

The online routing algorithm is deliberately not implemented here.  This
module is an *offline placement oracle*: it builds a small route set whose
mesh segments use deterministic XY, fractionally assigns a known traffic
matrix to those routes, and scores the resulting directed-link congestion.
Final topologies must still be evaluated with the unchanged Garnet/standalone
policy-4 committed routing.

The progressive assignment is a path-based approximation to splittable
multicommodity flow.  It is not an exact LP solver, but unlike ASPL it models
link capacity and permits a commodity to split across several routes.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from statistics import fmean
from typing import Iterable, Mapping, Sequence

from .model import Demand, ExpressEdge, GridGraph


DirectedEdge = tuple[int, int]


@dataclass(frozen=True)
class FlowRoute:
    channels: tuple[int, ...]
    latency: int
    express_count: int
    tie_key: tuple[int, ...]


@dataclass(frozen=True)
class FlowMetrics:
    network_max_load: float
    endpoint_max_load: float
    overall_max_load: float
    p95_link_load: float
    mean_latency: float
    concurrent_flow_proxy: float
    commodity_count: int
    route_count: int

    def as_dict(self) -> dict[str, float | int]:
        return {
            "network_max_load": self.network_max_load,
            "endpoint_max_load": self.endpoint_max_load,
            "overall_max_load": self.overall_max_load,
            "p95_link_load": self.p95_link_load,
            "mean_latency": self.mean_latency,
            "concurrent_flow_proxy": self.concurrent_flow_proxy,
            "commodity_count": self.commodity_count,
            "route_count": self.route_count,
        }


def xy_nodes(graph: GridGraph, source: int, dest: int) -> list[int]:
    """Return the nodes on the same X-then-Y mesh path used by policy 4."""
    x, y = graph.coordinate(source)
    dx, dy = graph.coordinate(dest)
    nodes = [source]
    while x != dx:
        x += 1 if dx > x else -1
        nodes.append(graph.node(x, y))
    while y != dy:
        y += 1 if dy > y else -1
        nodes.append(graph.node(x, y))
    return nodes


def _append_segment(nodes: list[int], segment: Sequence[int]) -> None:
    if nodes[-1] != segment[0]:
        raise ValueError("route segments are disconnected")
    nodes.extend(segment[1:])


def _channel_ids(graph: GridGraph, express_edges: Sequence[ExpressEdge]):
    directed: list[DirectedEdge] = []
    for u, v in sorted(graph.mesh_edges):
        directed.extend(((u, v), (v, u)))
    for edge in express_edges:
        directed.extend(((edge.u, edge.v), (edge.v, edge.u)))
    return {edge: index for index, edge in enumerate(directed)}, directed


def _make_route(
    channel_id: Mapping[DirectedEdge, int],
    nodes: Sequence[int],
    latency: int,
    express_count: int,
    tie_key: tuple[int, ...],
) -> FlowRoute | None:
    # The Garnet candidate generator discards paths that revisit a router.
    if len(set(nodes)) != len(nodes):
        return None
    return FlowRoute(
        tuple(channel_id[(u, v)] for u, v in zip(nodes, nodes[1:])),
        latency,
        express_count,
        tie_key,
    )


def route_candidates(
    graph: GridGraph,
    express_edges: Sequence[ExpressEdge],
    source: int,
    dest: int,
    channel_id: Mapping[DirectedEdge, int],
    *,
    k: int = 8,
    max_express: int = 1,
) -> tuple[FlowRoute, ...]:
    """Build top-K loop-free committed routes with XY mesh segments.

    ``max_express=1`` is the fast placement-search proxy.  ``2`` matches the
    express-count scope of the actual source-route candidate generator and is
    useful for rescoring finalists.
    """
    if k < 1 or max_express not in {0, 1, 2}:
        raise ValueError("invalid route candidate configuration")
    candidates: list[FlowRoute] = []
    mesh_nodes = xy_nodes(graph, source, dest)
    mesh_route = _make_route(
        channel_id, mesh_nodes, len(mesh_nodes) - 1, 0, (),
    )
    assert mesh_route is not None
    candidates.append(mesh_route)

    directed_express = [
        (edge_id, direction, entry, exit_node, edge.latency)
        for edge_id, edge in enumerate(express_edges)
        for direction, (entry, exit_node) in enumerate(
            ((edge.u, edge.v), (edge.v, edge.u))
        )
    ]
    if max_express >= 1:
        for edge_id, direction, entry, exit_node, latency in directed_express:
            nodes = xy_nodes(graph, source, entry)
            nodes.append(exit_node)
            _append_segment(nodes, xy_nodes(graph, exit_node, dest))
            route = _make_route(
                channel_id,
                nodes,
                len(nodes) - 2 + latency,
                1,
                (2 * edge_id + direction,),
            )
            if route is not None:
                candidates.append(route)

    if max_express >= 2:
        for first in directed_express:
            first_id, first_dir, entry1, exit1, latency1 = first
            for second in directed_express:
                second_id, second_dir, entry2, exit2, latency2 = second
                if first_id == second_id:
                    continue
                nodes = xy_nodes(graph, source, entry1)
                nodes.append(exit1)
                _append_segment(nodes, xy_nodes(graph, exit1, entry2))
                nodes.append(exit2)
                _append_segment(nodes, xy_nodes(graph, exit2, dest))
                route = _make_route(
                    channel_id,
                    nodes,
                    len(nodes) - 3 + latency1 + latency2,
                    2,
                    (2 * first_id + first_dir,
                     2 * second_id + second_dir),
                )
                if route is not None:
                    candidates.append(route)

    # Match the real table's preference for latency, then fewer express hops,
    # then stable express-edge identifiers.  Deduplicate identical channels.
    candidates.sort(
        key=lambda route: (route.latency, route.express_count, route.tie_key)
    )
    unique: list[FlowRoute] = []
    seen = set()
    for route in candidates:
        if route.channels in seen:
            continue
        seen.add(route.channels)
        unique.append(route)
        if len(unique) == k:
            break
    return tuple(unique)


def flow_metrics(
    n: int,
    express_edges: Sequence[ExpressEdge],
    demand: Demand,
    *,
    k: int = 8,
    max_express: int = 1,
    rounds: int = 24,
    congestion_power: int = 6,
) -> FlowMetrics:
    """Approximate a splittable path-based multicommodity-flow assignment.

    Demand is divided into ``rounds`` quanta.  Each quantum chooses the route
    with the smallest increase in a convex p-norm link-load objective.  More
    rounds permit finer splitting; a larger power more strongly approximates
    minimization of the maximum directed-link load.
    """
    if rounds < 1 or congestion_power < 2:
        raise ValueError("invalid flow assignment parameters")
    graph = GridGraph(n, tuple(express_edges))
    channel_id, directed = _channel_ids(graph, express_edges)
    commodities = [
        (source, dest, weight,
         route_candidates(
             graph, express_edges, source, dest, channel_id,
             k=k, max_express=max_express,
         ))
        for (source, dest), weight in sorted(demand.items())
        if source != dest and weight > 0.0
    ]
    loads = [0.0] * len(directed)
    latency_sum = 0.0
    route_count = sum(len(routes) for _, _, _, routes in commodities)

    # Rotating the deterministic commodity order reduces first-mover bias.
    count = len(commodities)
    stride = max(1, count // rounds)
    for round_id in range(rounds):
        offset = (round_id * stride) % max(1, count)
        for item_id in range(count):
            source, dest, weight, routes = commodities[(item_id + offset) % count]
            del source, dest
            amount = weight / rounds
            best_route = None
            best_cost = math.inf
            for route in routes:
                incremental = 0.0
                for channel in route.channels:
                    old = loads[channel]
                    incremental += (old + amount) ** congestion_power - old ** congestion_power
                # Static latency is only a deterministic, very small tie
                # breaker; congestion capacity remains the primary objective.
                cost = incremental + 1e-15 * route.latency
                if cost < best_cost:
                    best_route, best_cost = route, cost
            assert best_route is not None
            for channel in best_route.channels:
                loads[channel] += amount
            latency_sum += amount * best_route.latency

    source_load = [0.0] * graph.node_count
    dest_load = [0.0] * graph.node_count
    for (source, dest), weight in demand.items():
        if source != dest and weight > 0.0:
            source_load[source] += weight
            dest_load[dest] += weight
    endpoint_max = max(max(source_load, default=0.0),
                       max(dest_load, default=0.0))
    network_max = max(loads, default=0.0)
    sorted_loads = sorted(loads)
    p95_index = max(0, math.ceil(0.95 * len(sorted_loads)) - 1)
    p95 = sorted_loads[p95_index] if sorted_loads else 0.0
    overall = max(network_max, endpoint_max)
    return FlowMetrics(
        network_max_load=network_max,
        endpoint_max_load=endpoint_max,
        overall_max_load=overall,
        p95_link_load=p95,
        mean_latency=latency_sum,
        concurrent_flow_proxy=(1.0 / overall if overall else math.inf),
        commodity_count=count,
        route_count=route_count,
    )


def normalized_flow_utility(
    metrics: Sequence[FlowMetrics],
    mesh_metrics: Sequence[FlowMetrics],
    *,
    worst_case_weight: float = 0.25,
    latency_weight: float = 0.002,
) -> float:
    """Combine one or more traffic-specific capacity ratios.

    A positive worst-case term discourages a multi-traffic placement from
    sacrificing one traffic for a large win on another.
    """
    if len(metrics) != len(mesh_metrics) or not metrics:
        raise ValueError("metrics and mesh baselines must have equal nonzero length")
    ratios = [
        base.overall_max_load / current.overall_max_load
        for current, base in zip(metrics, mesh_metrics)
    ]
    latency_ratios = [
        current.mean_latency / max(base.mean_latency, 1e-12)
        for current, base in zip(metrics, mesh_metrics)
    ]
    return (
        fmean(ratios)
        + worst_case_weight * min(ratios)
        - latency_weight * fmean(latency_ratios)
    )


def direct_benefit_scores(
    n: int,
    candidates: Iterable[ExpressEdge],
    demands: Sequence[Demand],
) -> dict[tuple[int, int], float]:
    """Cheap demand-aware ranking used only to propose search mutations."""
    graph = GridGraph(n)
    scores: dict[tuple[int, int], float] = {}
    for edge in candidates:
        saved = 0.0
        for demand in demands:
            traffic_saved = 0.0
            for (source, dest), weight in demand.items():
                direct = graph.manhattan(source, dest)
                via_uv = (graph.manhattan(source, edge.u) + edge.latency
                          + graph.manhattan(edge.v, dest))
                via_vu = (graph.manhattan(source, edge.v) + edge.latency
                          + graph.manhattan(edge.u, dest))
                traffic_saved += weight * max(0, direct - min(via_uv, via_vu))
            saved += traffic_saved
        scores[edge.key] = saved / max(1, edge.wire_length) / len(demands)
    return scores


def compress_demand(demand: Demand, max_commodities: int) -> Demand:
    """Deterministically approximate a large demand with weighted samples.

    Systematic quantile sampling preserves high-weight hotspot pairs without
    introducing a random search seed.  The selected weights sum to one.  It is
    intended for topology search only; finalists should be rescored with the
    full demand.
    """
    items = [item for item in sorted(demand.items()) if item[1] > 0.0]
    if max_commodities < 1:
        raise ValueError("max_commodities must be positive")
    if len(items) <= max_commodities:
        return dict(items)
    # Stratify by source so a regular all-pairs matrix cannot alias into only
    # a handful of destination columns.  Counts follow each source's offered
    # load; a source-dependent irrational phase decorrelates destination picks.
    by_source: dict[int, list[tuple[tuple[int, int], float]]] = {}
    for pair, weight in items:
        by_source.setdefault(pair[0], []).append((pair, weight))
    source_totals = {source: sum(weight for _, weight in source_items)
                     for source, source_items in by_source.items()}
    total = sum(source_totals.values())
    ideal_counts = {
        source: max(1, int(max_commodities * value / total))
        for source, value in source_totals.items()
    }
    while sum(ideal_counts.values()) > max_commodities:
        source = max(
            (item for item in ideal_counts if ideal_counts[item] > 1),
            key=lambda item: (ideal_counts[item]
                              - max_commodities * source_totals[item] / total,
                              item),
        )
        ideal_counts[source] -= 1
    while sum(ideal_counts.values()) < max_commodities:
        source = max(
            ideal_counts,
            key=lambda item: (max_commodities * source_totals[item] / total
                              - ideal_counts[item], -item),
        )
        ideal_counts[source] += 1

    selected: dict[tuple[int, int], float] = {}
    golden_fraction = (math.sqrt(5.0) - 1.0) / 2.0
    for source, source_items in sorted(by_source.items()):
        count = ideal_counts[source]
        source_total = source_totals[source]
        phase = ((source + 0.5) * golden_fraction) % 1.0
        targets = [((sample + phase) % count) * source_total / count
                   for sample in range(count)]
        targets.sort()
        cumulative = 0.0
        item_index = 0
        for target in targets:
            while (item_index + 1 < len(source_items)
                   and cumulative + source_items[item_index][1] < target):
                cumulative += source_items[item_index][1]
                item_index += 1
            pair = source_items[item_index][0]
            selected[pair] = (selected.get(pair, 0.0)
                              + source_total / count)
    return selected
