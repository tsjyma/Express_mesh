#!/usr/bin/env python3
"""Generate constrained placements and offline feasibility metrics."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from express_mesh_project.model import (
    GridGraph,
    cutstress_demand,
    hotspot_demand,
    public_metrics,
    uniform_demand,
)
from express_mesh_project.placement import (
    greedy_placement,
    handcrafted_placement,
    random_placement,
    validate_placement,
)


def edge_record(graph, edge):
    return {
        "u": edge.u,
        "v": edge.v,
        "u_coord": list(graph.coordinate(edge.u)),
        "v_coord": list(graph.coordinate(edge.v)),
        "wire_length": edge.wire_length,
        "latency": edge.latency,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=8)
    parser.add_argument("--budget", type=int, default=16)
    parser.add_argument("--max-degree", type=int, default=1)
    parser.add_argument("--d-min", type=int, default=3)
    parser.add_argument("--latency-model", choices=["ideal", "length-aware"], default="ideal")
    parser.add_argument("--alpha", type=float, default=0.5)
    parser.add_argument("--rho", type=float, default=1.5)
    parser.add_argument("--random-seed", type=int, default=1)
    parser.add_argument("--random-only", action="store_true",
                        help="generate only the random placement")
    parser.add_argument("--skip-metrics", action="store_true",
                        help="skip expensive offline demand metrics")
    parser.add_argument("--output-dir", type=Path,
                        default=Path("express_mesh_project/results/phase1"))
    args = parser.parse_args()

    mesh = GridGraph(args.n)
    demands = {
        "uniform": uniform_demand(mesh.node_count),
        "cutstress": cutstress_demand(args.n),
        "hotspot": hotspot_demand(args.n),
    }
    uniform = demands["uniform"]
    cutstress = demands["cutstress"]
    placements = {
        "mesh": [],
        "random": random_placement(
            args.n, args.budget, args.max_degree, args.d_min,
            args.latency_model, args.random_seed,
        ),
        "handcrafted": handcrafted_placement(
            args.n, args.budget, args.max_degree, args.d_min, args.latency_model,
        ),
        "aspl": greedy_placement(
            args.n, uniform, args.budget, args.max_degree, args.d_min,
            args.latency_model, "aspl", args.alpha,
        ),
        "bottleneck": greedy_placement(
            args.n, uniform, args.budget, args.max_degree, args.d_min,
            args.latency_model, "bottleneck", args.alpha,
        ),
        "hybrid": greedy_placement(
            args.n, uniform, args.budget, args.max_degree, args.d_min,
            args.latency_model, "hybrid", args.alpha,
        ),
        "hybrid_cutstress": greedy_placement(
            args.n, cutstress, args.budget, args.max_degree, args.d_min,
            args.latency_model, "hybrid", args.alpha,
        ),
    }
    if args.random_only:
        placements = {"random": placements["random"]}

    args.output_dir.mkdir(parents=True, exist_ok=True)
    summary = {
        "config": vars(args) | {"output_dir": str(args.output_dir)},
        "placements": {},
    }
    for name, edges in placements.items():
        constraints = validate_placement(
            args.n, edges, args.budget, args.max_degree, args.d_min
        )
        graph = GridGraph(args.n, tuple(edges))
        record = {
            "name": name,
            "dimension": args.n,
            "node_count": graph.node_count,
            "latency_model": args.latency_model,
            "constraints": constraints,
            "express_links": [edge_record(graph, edge) for edge in edges],
            "metrics": {} if args.skip_metrics else {
                traffic: public_metrics(graph, demand, args.rho)
                for traffic, demand in demands.items()
            },
        }
        summary["placements"][name] = record
        (args.output_dir / f"{name}.json").write_text(
            json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )

    (args.output_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"Wrote {len(placements)} placements to {args.output_dir}")
    for name, record in summary["placements"].items():
        if args.skip_metrics:
            print(f"{name:12s} wire={record['constraints']['wire_cost']:2d}")
            continue
        uniform_metrics = record["metrics"]["uniform"]
        cut_metrics = record["metrics"]["cutstress"]
        print(
            f"{name:12s} wire={record['constraints']['wire_cost']:2d} "
            f"uniform ASPL={uniform_metrics['aspl']:.4f} "
            f"Lmax={uniform_metrics['l_max']:.5f} "
            f"cut Lmax={cut_metrics['l_max']:.5f}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
