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
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    summary = {"config": vars(args) | {"output_dir": str(args.output_dir)},
               "placements": {}}
    for traffic in args.traffics:
        demand = traffic_demand(traffic)
        for objective in args.objectives:
            name = f"{traffic}_{objective}"
            print(f"generating {name}", flush=True)
            edges = greedy_placement(
                8, demand, 64, 1, 3, "ideal", objective, args.alpha,
            )
            validate_placement(8, edges, 64, 1, 3)
            graph = GridGraph(8, tuple(edges))
            metadata = {
                "algorithm": "traffic_aware_greedy",
                "traffic": traffic,
                "objective": objective,
                "alpha": args.alpha,
                "offline_metrics": public_metrics(graph, demand),
                "flow_proxy": flow_metrics(
                    8, edges, demand, rounds=24, max_express=1,
                ).as_dict(),
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
