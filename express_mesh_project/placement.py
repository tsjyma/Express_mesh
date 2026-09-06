"""Constrained placement algorithms for Budgeted Express-Mesh."""

from __future__ import annotations

import math
import random

from .model import ExpressEdge, GridGraph, core_metrics


def edge_latency(
    wire_length: int,
    latency_model: str,
    express_wire_per_cycle: int = 4,
) -> int:
    if latency_model == "ideal":
        return 1
    if latency_model == "length-aware":
        if express_wire_per_cycle < 1:
            raise ValueError("express_wire_per_cycle must be positive")
        return math.ceil(wire_length / express_wire_per_cycle)
    raise ValueError(f"unknown latency model: {latency_model}")


def candidate_edges(
    n: int,
    d_min: int,
    latency_model: str,
    express_wire_per_cycle: int = 4,
) -> list[ExpressEdge]:
    graph = GridGraph(n)
    result = []
    for u in range(graph.node_count):
        for v in range(u + 1, graph.node_count):
            wire_length = graph.manhattan(u, v)
            if wire_length >= d_min:
                result.append(
                    ExpressEdge(
                        u, v, wire_length,
                        edge_latency(
                            wire_length, latency_model,
                            express_wire_per_cycle,
                        ),
                    )
                )
    return result


def _filter_candidates(
    candidates: list[ExpressEdge], n: int, candidate_mode: str
) -> list[ExpressEdge]:
    if candidate_mode not in {"all", "axis", "stride4"}:
        raise ValueError(f"unknown candidate mode: {candidate_mode}")
    if candidate_mode == "all":
        return candidates
    mesh = GridGraph(n)
    return [
        edge for edge in candidates
        if (mesh.coordinate(edge.u)[0] == mesh.coordinate(edge.v)[0]
            or mesh.coordinate(edge.u)[1] == mesh.coordinate(edge.v)[1])
        and (candidate_mode != "stride4" or edge.wire_length == 4)
    ]


def validate_placement(
    n: int,
    edges: list[ExpressEdge],
    budget: int,
    max_degree: int,
    d_min: int,
) -> dict[str, int]:
    mesh = GridGraph(n)
    degree = [0] * mesh.node_count
    seen = set()
    wire_cost = 0
    for edge in edges:
        if edge.key in seen or edge.key in mesh.mesh_edges:
            raise ValueError(f"duplicate or mesh edge in placement: {edge.key}")
        if edge.wire_length != mesh.manhattan(edge.u, edge.v):
            raise ValueError(f"incorrect wire length for edge: {edge.key}")
        if edge.wire_length < d_min:
            raise ValueError(f"edge shorter than d_min: {edge.key}")
        seen.add(edge.key)
        degree[edge.u] += 1
        degree[edge.v] += 1
        wire_cost += edge.wire_length
    if wire_cost > budget:
        raise ValueError(f"wire budget exceeded: {wire_cost} > {budget}")
    if max(degree, default=0) > max_degree:
        raise ValueError(f"express degree exceeded: {max(degree)} > {max_degree}")
    return {"wire_cost": wire_cost, "max_degree": max(degree, default=0)}


def _legal(edge, selected, degree, remaining, max_degree):
    return (
        edge.key not in selected
        and edge.wire_length <= remaining
        and degree[edge.u] < max_degree
        and degree[edge.v] < max_degree
    )


def random_placement(
    n: int,
    budget: int,
    max_degree: int,
    d_min: int,
    latency_model: str,
    seed: int,
    attempts: int = 256,
    candidate_mode: str = "all",
) -> list[ExpressEdge]:
    rng = random.Random(seed)
    candidates = _filter_candidates(
        candidate_edges(n, d_min, latency_model), n, candidate_mode
    )
    best: list[ExpressEdge] = []
    best_cost = -1
    for _ in range(attempts):
        shuffled = candidates.copy()
        rng.shuffle(shuffled)
        selected: list[ExpressEdge] = []
        selected_keys = set()
        degree = [0] * (n * n)
        remaining = budget
        for edge in shuffled:
            if _legal(edge, selected_keys, degree, remaining, max_degree):
                selected.append(edge)
                selected_keys.add(edge.key)
                degree[edge.u] += 1
                degree[edge.v] += 1
                remaining -= edge.wire_length
        cost = budget - remaining
        if cost > best_cost:
            best, best_cost = selected, cost
        if cost == budget:
            break
    return sorted(best, key=lambda edge: edge.key)


def handcrafted_placement(
    n: int,
    budget: int,
    max_degree: int,
    d_min: int,
    latency_model: str,
    stride: int = 4,
) -> list[ExpressEdge]:
    mesh = GridGraph(n)
    center = (n - 1) / 2
    horizontal = []
    vertical = []
    for edge in candidate_edges(n, d_min, latency_model):
        ux, uy = mesh.coordinate(edge.u)
        vx, vy = mesh.coordinate(edge.v)
        if edge.wire_length != stride:
            continue
        center_distance = abs((ux + vx) / 2 - center) + abs((uy + vy) / 2 - center)
        item = (center_distance, edge.key, edge)
        if uy == vy:
            horizontal.append(item)
        elif ux == vx:
            vertical.append(item)
    horizontal.sort()
    vertical.sort()

    selected = []
    selected_keys = set()
    degree = [0] * (n * n)
    remaining = budget
    pools = [horizontal, vertical]
    while True:
        added = False
        for pool in pools:
            for _, _, edge in pool:
                if _legal(edge, selected_keys, degree, remaining, max_degree):
                    selected.append(edge)
                    selected_keys.add(edge.key)
                    degree[edge.u] += 1
                    degree[edge.v] += 1
                    remaining -= edge.wire_length
                    added = True
                    break
        if not added:
            break
    return sorted(selected, key=lambda edge: edge.key)


def greedy_placement(
    n: int,
    demand,
    budget: int,
    max_degree: int,
    d_min: int,
    latency_model: str,
    objective: str,
    alpha: float = 0.5,
    candidate_mode: str = "all",
    candidate_limit: int = 0,
    express_wire_per_cycle: int = 4,
) -> list[ExpressEdge]:
    if objective not in {"aspl", "bottleneck", "hybrid"}:
        raise ValueError(f"unknown objective: {objective}")
    if not 0.0 <= alpha <= 1.0:
        raise ValueError("alpha must be in [0, 1]")
    candidates = _filter_candidates(
        candidate_edges(
            n, d_min, latency_model, express_wire_per_cycle,
        ), n, candidate_mode
    )
    if objective == "aspl" and n >= 12:
        if candidate_limit:
            from .flow_placement import direct_benefit_scores
            scores = direct_benefit_scores(n, candidates, [demand])
            candidates = sorted(
                candidates,
                key=lambda edge: (
                    -scores[edge.key], edge.wire_length, edge.key,
                ),
            )[:candidate_limit]
        return _greedy_aspl_placement(
            n, demand, candidates, budget, max_degree,
        )
    selected: list[ExpressEdge] = []
    selected_keys = set()
    degree = [0] * (n * n)
    remaining = budget
    base = core_metrics(GridGraph(n), demand)

    def objective_value(metrics):
        if objective == "aspl":
            return metrics["aspl"] / base["aspl"]
        if objective == "bottleneck":
            return metrics["l_max"] / base["l_max"]
        return (
            alpha * metrics["aspl"] / base["aspl"]
            + (1.0 - alpha) * metrics["l_max"] / base["l_max"]
        )

    current_value = 1.0
    while True:
        best = None
        best_value = current_value
        best_score = 0.0
        for edge in candidates:
            if not _legal(edge, selected_keys, degree, remaining, max_degree):
                continue
            metrics = core_metrics(GridGraph(n, tuple(selected + [edge])), demand)
            value = objective_value(metrics)
            score = (current_value - value) / edge.wire_length
            tie_key = edge.key
            if score > best_score + 1e-15 or (
                abs(score - best_score) <= 1e-15
                and best is not None
                and tie_key < best.key
            ):
                best, best_value, best_score = edge, value, score
        if best is None or best_score <= 0.0:
            break
        selected.append(best)
        selected_keys.add(best.key)
        degree[best.u] += 1
        degree[best.v] += 1
        remaining -= best.wire_length
        current_value = best_value
    return sorted(selected, key=lambda edge: edge.key)


def _greedy_aspl_placement(
    n: int,
    demand,
    candidates: list[ExpressEdge],
    budget: int,
    max_degree: int,
) -> list[ExpressEdge]:
    """Exact ASPL Greedy using an incrementally updated distance matrix.

    Adding one positive-weight undirected edge ``(u, v)`` changes every
    shortest path to the minimum of its old distance, a path using ``u->v``,
    and a path using ``v->u``.  Evaluating that identity with NumPy preserves
    the original Greedy objective and deterministic tie handling while avoiding a fresh
    all-pairs shortest-path/betweenness computation for every candidate.  The
    distinction is important for 16x16 scaling: this is an implementation
    acceleration, not a restricted candidate set or a proxy objective.
    """
    try:
        import numpy as np
    except ImportError as error:  # pragma: no cover - experiment dependency
        raise RuntimeError(
            "exact scalable ASPL Greedy requires NumPy"
        ) from error

    node_count = n * n
    nodes = np.arange(node_count, dtype=np.int32)
    x = nodes % n
    y = nodes // n
    distance = (np.abs(x[:, None] - x[None, :])
                + np.abs(y[:, None] - y[None, :])).astype(np.int16)
    source = np.fromiter((pair[0] for pair in demand), dtype=np.int32)
    dest = np.fromiter((pair[1] for pair in demand), dtype=np.int32)
    weight = np.fromiter(demand.values(), dtype=np.float64)
    base_value = float(np.dot(weight, distance[source, dest]))
    current_value = base_value

    selected: list[ExpressEdge] = []
    selected_keys = set()
    degree = [0] * node_count
    remaining = budget
    while True:
        best = None
        best_value = current_value
        best_score = 0.0
        for edge in candidates:
            if not _legal(edge, selected_keys, degree, remaining, max_degree):
                continue
            old = distance[source, dest]
            via_forward = (distance[source, edge.u] + edge.latency
                           + distance[edge.v, dest])
            via_reverse = (distance[source, edge.v] + edge.latency
                           + distance[edge.u, dest])
            value = float(np.dot(
                weight, np.minimum(old, np.minimum(via_forward, via_reverse))
            ))
            # Algebraically identical to the normalized objective used by the
            # original implementation.
            score = ((current_value - value) / base_value
                     / edge.wire_length)
            if score > best_score + 1e-15 or (
                abs(score - best_score) <= 1e-15
                and best is not None and edge.key < best.key
            ):
                best, best_value, best_score = edge, value, score
        if best is None or best_score <= 0.0:
            break

        via_forward = (distance[:, best.u, None] + best.latency
                       + distance[best.v, None, :])
        via_reverse = (distance[:, best.v, None] + best.latency
                       + distance[best.u, None, :])
        distance = np.minimum(
            distance, np.minimum(via_forward, via_reverse)
        ).astype(np.int16, copy=False)
        selected.append(best)
        selected_keys.add(best.key)
        degree[best.u] += 1
        degree[best.v] += 1
        remaining -= best.wire_length
        current_value = best_value
    return sorted(selected, key=lambda edge: edge.key)
