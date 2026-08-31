#!/usr/bin/env python3
"""Generate Greedy baselines aware of a known mixture of traffic classes."""

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


def mixed_demand(traffics, weights):
    total = sum(weights)
    if total <= 0.0 or any(weight < 0.0 for weight in weights):
        raise ValueError("mixture weights must be nonnegative with positive sum")
    result = {}
    for traffic, weight in zip(traffics, weights):
        for pair, value in traffic_demand(traffic).items():
            result[pair] = result.get(pair, 0.0) + weight / total * value
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", required=True)
    parser.add_argument("--traffics", nargs="+", choices=TRAFFIC_NAMES,
                        required=True)
    parser.add_argument("--weights", type=float, nargs="+")
    parser.add_argument("--objectives", nargs="+",
                        choices=["aspl", "bottleneck", "hybrid"],
                        default=["aspl", "bottleneck", "hybrid"])
    parser.add_argument("--alpha", type=float, default=0.5)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    weights = args.weights or [1.0] * len(args.traffics)
    if len(weights) != len(args.traffics):
        parser.error("--weights must match --traffics")
    demand = mixed_demand(args.traffics, weights)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    summary = {"config": vars(args) | {
        "output_dir": str(args.output_dir), "weights": weights,
    }, "placements": {}}
    for objective in args.objectives:
        name = f"{args.name}_{objective}"
        print(f"generating {name}", flush=True)
        edges = greedy_placement(
            8, demand, 64, 1, 3, "ideal", objective, args.alpha,
        )
        validate_placement(8, edges, 64, 1, 3)
        graph = GridGraph(8, tuple(edges))
        metadata = {
            "algorithm": "traffic_mixture_greedy",
            "traffics": args.traffics,
            "weights": weights,
            "objective": objective,
            "alpha": args.alpha,
            "mixed_metrics": public_metrics(graph, demand),
            "per_traffic_flow": {
                traffic: flow_metrics(
                    8, edges, traffic_demand(traffic), rounds=24,
                    max_express=1,
                ).as_dict()
                for traffic in args.traffics
            },
        }
        record = placement_record(name, edges, metadata)
        summary["placements"][name] = record
        (args.output_dir / f"{name}.json").write_text(
            json.dumps(record, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(
            f"{name}: links={len(edges)} "
            f"wire={record['constraints']['wire_cost']} "
            f"ASPL={metadata['mixed_metrics']['aspl']:.4f}", flush=True,
        )
    (args.output_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
