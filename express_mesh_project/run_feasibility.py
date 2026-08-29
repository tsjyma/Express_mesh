#!/usr/bin/env python3
"""Generate constrained placements and offline feasibility metrics."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from express_mesh_project.model import (
    bit_complement_demand,
    GridGraph,
    cutstress_demand,
    hotspot_demand,
    public_metrics,
    tornado_demand,
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
    parser.add_argument(
        "--placements", nargs="+",
        choices=["mesh", "random", "handcrafted", "aspl", "bottleneck",
                 "hybrid", "hybrid_cutstress", "robust", "axis_aspl",
                 "axis_hybrid", "axis_robust", "stride_aspl",
                 "stride_hybrid", "stride_robust", "axis_random",
                 "stride_random", "bitcomp_aspl", "bitcomp_hybrid",
                 "tornado_aspl", "tornado_hybrid"],
        help="generate only the selected placement algorithms",
    )
    parser.add_argument("--skip-metrics", action="store_true",
                        help="skip expensive offline demand metrics")
    parser.add_argument(
        "--robust-uniform-weight", type=float, default=0.5,
        help="Uniform share in the mixed demand optimized by Robust Greedy",
    )
    parser.add_argument("--output-dir", type=Path,
                        default=Path("express_mesh_project/results/phase1"))
    args = parser.parse_args()

    mesh = GridGraph(args.n)
    demands = {
        "uniform": uniform_demand(mesh.node_count),
        "cutstress": cutstress_demand(args.n),
        "hotspot": hotspot_demand(args.n),
        "bit_complement": bit_complement_demand(args.n),
        "tornado": tornado_demand(args.n),
    }
    uniform = demands["uniform"]
    cutstress = demands["cutstress"]
    if not 0.0 <= args.robust_uniform_weight <= 1.0:
        parser.error("--robust-uniform-weight must be in [0, 1]")
    robust_demand = dict(uniform)
    for pair in set(robust_demand) | set(cutstress):
        robust_demand[pair] = (
            args.robust_uniform_weight * uniform.get(pair, 0.0)
            + (1.0 - args.robust_uniform_weight) * cutstress.get(pair, 0.0)
        )
    wanted = set(args.placements or [
        "mesh", "random", "handcrafted", "aspl", "bottleneck", "hybrid",
        "hybrid_cutstress",
    ])
    placements = {}
    if "mesh" in wanted:
        placements["mesh"] = []
    if "random" in wanted:
        placements["random"] = random_placement(
            args.n, args.budget, args.max_degree, args.d_min,
            args.latency_model, args.random_seed,
        )
    for name, candidate_mode in (("axis_random", "axis"),
                                 ("stride_random", "stride4")):
        if name in wanted:
            placements[name] = random_placement(
                args.n, args.budget, args.max_degree, args.d_min,
                args.latency_model, args.random_seed,
                candidate_mode=candidate_mode,
            )
    if "handcrafted" in wanted:
        placements["handcrafted"] = handcrafted_placement(
            args.n, args.budget, args.max_degree, args.d_min, args.latency_model,
        )
    if "aspl" in wanted:
        placements["aspl"] = greedy_placement(
            args.n, uniform, args.budget, args.max_degree, args.d_min,
            args.latency_model, "aspl", args.alpha,
        )
    if "bottleneck" in wanted:
        placements["bottleneck"] = greedy_placement(
            args.n, uniform, args.budget, args.max_degree, args.d_min,
            args.latency_model, "bottleneck", args.alpha,
        )
    if "hybrid" in wanted:
        placements["hybrid"] = greedy_placement(
            args.n, uniform, args.budget, args.max_degree, args.d_min,
            args.latency_model, "hybrid", args.alpha,
        )
    if "hybrid_cutstress" in wanted:
        placements["hybrid_cutstress"] = greedy_placement(
            args.n, cutstress, args.budget, args.max_degree, args.d_min,
            args.latency_model, "hybrid", args.alpha,
        )
    traffic_aware = {
        "bitcomp_aspl": (demands["bit_complement"], "aspl"),
        "bitcomp_hybrid": (demands["bit_complement"], "hybrid"),
        "tornado_aspl": (demands["tornado"], "aspl"),
        "tornado_hybrid": (demands["tornado"], "hybrid"),
    }
    for name, (demand, objective) in traffic_aware.items():
        if name in wanted:
            placements[name] = greedy_placement(
                args.n, demand, args.budget, args.max_degree, args.d_min,
                args.latency_model, objective, args.alpha,
            )
    if "robust" in wanted:
        placements["robust"] = greedy_placement(
            args.n, robust_demand, args.budget, args.max_degree, args.d_min,
            args.latency_model, "hybrid", args.alpha,
        )
    structured = {
        "axis_aspl": (uniform, "aspl", "axis"),
        "axis_hybrid": (uniform, "hybrid", "axis"),
        "axis_robust": (robust_demand, "hybrid", "axis"),
        "stride_aspl": (uniform, "aspl", "stride4"),
        "stride_hybrid": (uniform, "hybrid", "stride4"),
        "stride_robust": (robust_demand, "hybrid", "stride4"),
    }
    for name, (demand, objective, candidate_mode) in structured.items():
        if name in wanted:
            placements[name] = greedy_placement(
                args.n, demand, args.budget, args.max_degree, args.d_min,
                args.latency_model, objective, args.alpha, candidate_mode,
            )
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
