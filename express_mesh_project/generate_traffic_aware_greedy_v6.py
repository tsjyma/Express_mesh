#!/usr/bin/env python3
"""Generate ASPL/Bottleneck/Hybrid Greedy baselines for every known traffic."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from express_mesh_project.flow_placement import flow_metrics
from express_mesh_project.model import GridGraph, public_metrics
from express_mesh_project.placement import greedy_placement, validate_placement
from express_mesh_project.search_placement_advanced import (
    TRAFFIC_NAMES,
    placement_record,
    traffic_demand,
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--traffics", nargs="+", choices=TRAFFIC_NAMES,
                        default=list(TRAFFIC_NAMES))
    parser.add_argument("--objectives", nargs="+",
                        choices=["aspl", "bottleneck", "hybrid"],
                        default=["aspl", "bottleneck", "hybrid"])
    parser.add_argument("--alpha", type=float, default=0.5)
    parser.add_argument("--dimension", type=int, default=8)
    parser.add_argument("--wire-budget", type=int, default=64)
    parser.add_argument("--max-degree", type=int, default=1)
    parser.add_argument("--min-wire-length", type=int, default=3)
    parser.add_argument(
        "--candidate-limit", type=int, default=0,
        help=("for scalable large meshes, prefilter to the top N links by "
              "single-link traffic-weighted distance benefit; zero uses all"),
    )
    parser.add_argument(
        "--candidate-mode", choices=["all", "axis", "stride4"],
        default="all",
    )
    parser.add_argument(
        "--latency-model", choices=["ideal", "length-aware"],
        default="ideal",
    )
    parser.add_argument("--express-wire-per-cycle", type=int, default=4)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    summary = {"config": vars(args) | {"output_dir": str(args.output_dir)},
               "placements": {}}
    for traffic in args.traffics:
        demand = traffic_demand(traffic,args.dimension)
        for objective in args.objectives:
            name = f"{traffic}_{objective}"
            print(f"generating {name}", flush=True)
            edges = greedy_placement(
                args.dimension, demand,
                args.wire_budget, args.max_degree, args.min_wire_length,
                args.latency_model, objective, args.alpha,
                candidate_mode=args.candidate_mode,
                candidate_limit=args.candidate_limit,
                express_wire_per_cycle=args.express_wire_per_cycle,
            )
            validate_placement(
                args.dimension,edges,args.wire_budget,args.max_degree,
                args.min_wire_length,
            )
            graph = GridGraph(args.dimension, tuple(edges))
            metadata = {
                "algorithm": "traffic_aware_greedy",
                "traffic": traffic,
                "objective": objective,
                "alpha": args.alpha,
                "latency_model": args.latency_model,
                "express_wire_per_cycle": args.express_wire_per_cycle,
                "offline_metrics": public_metrics(graph, demand),
                "flow_proxy": flow_metrics(
                    args.dimension, edges, demand, rounds=24, max_express=1,
                ).as_dict(),
            }
            record = placement_record(
                name,edges,metadata,dimension=args.dimension,
                wire_budget=args.wire_budget,
                max_degree=args.max_degree,
                min_wire_length=args.min_wire_length,
                latency_model=args.latency_model,
                express_wire_per_cycle=args.express_wire_per_cycle,
            )
            summary["placements"][name] = record
            (args.output_dir / f"{name}.json").write_text(
                json.dumps(record, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            print(
                f"{name}: links={len(edges)} "
                f"wire={record['constraints']['wire_cost']} "
                f"ASPL={metadata['offline_metrics']['aspl']:.4f} "
                f"flow={metadata['flow_proxy']['concurrent_flow_proxy']:.4f}",
                flush=True,
            )
    (args.output_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
