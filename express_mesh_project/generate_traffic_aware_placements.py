#!/usr/bin/env python3
"""Generate simple demand-direct express-link baselines.

This is deliberately separate from placement.py: it is an incremental
experiment, not a replacement for any existing placement component.  It
selects legal source/destination pairs by saved mesh hops per unit wire.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from express_mesh_project.model import (
    ExpressEdge,
    GridGraph,
    bit_complement_demand,
    tornado_demand,
)
from express_mesh_project.placement import edge_latency, validate_placement


def direct_pair_placement(n, demand, budget, max_degree, d_min, latency_model):
    graph = GridGraph(n)
    pair_weight = {}
    for (source, dest), weight in demand.items():
        key = (source, dest) if source < dest else (dest, source)
        pair_weight[key] = pair_weight.get(key, 0.0) + weight

    ranked = []
    for (u, v), weight in pair_weight.items():
        wire = graph.manhattan(u, v)
        if wire < d_min:
            continue
        latency = edge_latency(wire, latency_model)
        saved = wire - latency
        ranked.append((-(weight * saved / wire), -saved, (u, v),
                       ExpressEdge(u, v, wire, latency)))
    ranked.sort()

    degree = [0] * (n * n)
    remaining = budget
    selected = []
    for _, _, _, edge in ranked:
        if (edge.wire_length <= remaining and
                degree[edge.u] < max_degree and degree[edge.v] < max_degree):
            selected.append(edge)
            degree[edge.u] += 1
            degree[edge.v] += 1
            remaining -= edge.wire_length
    return sorted(selected, key=lambda edge: edge.key)


def record(name, n, edges, constraints, latency_model):
    graph = GridGraph(n)
    return {
        "name": name,
        "dimension": n,
        "node_count": n * n,
        "latency_model": latency_model,
        "algorithm": "demand_direct_saved_hops_per_wire",
        "constraints": constraints,
        "express_links": [
            {
                "u": edge.u,
                "v": edge.v,
                "u_coord": list(graph.coordinate(edge.u)),
                "v_coord": list(graph.coordinate(edge.v)),
                "wire_length": edge.wire_length,
                "latency": edge.latency,
            }
            for edge in edges
        ],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=8)
    parser.add_argument("--budget", type=int, default=64)
    parser.add_argument("--max-degree", type=int, default=1)
    parser.add_argument("--d-min", type=int, default=3)
    parser.add_argument("--latency-model", choices=["ideal", "length-aware"],
                        default="ideal")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    demands = {
        "bitcomp_direct": bit_complement_demand(args.n),
        "tornado_direct": tornado_demand(args.n),
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for name, demand in demands.items():
        edges = direct_pair_placement(
            args.n, demand, args.budget, args.max_degree, args.d_min,
            args.latency_model,
        )
        constraints = validate_placement(
            args.n, edges, args.budget, args.max_degree, args.d_min,
        )
        output = record(name, args.n, edges, constraints, args.latency_model)
        (args.output_dir / f"{name}.json").write_text(
            json.dumps(output, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(f"{name}: links={len(edges)} wire={constraints['wire_cost']}")


if __name__ == "__main__":
    main()
