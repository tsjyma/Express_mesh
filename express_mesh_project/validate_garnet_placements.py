#!/usr/bin/env python3
"""Run arbitrary placement JSON files through the fixed Garnet V6 protocol."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import csv
import json
from pathlib import Path
import statistics
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import express_mesh_project.run_phase3_measurement_v2 as garnet_runner
from express_mesh_project.search_placement_advanced import (
    TRAFFIC_NAMES,
    load_placement,
)
from express_mesh_project.placement import validate_placement


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--topologies", type=Path, nargs="+", required=True)
    parser.add_argument("--labels", nargs="+", required=True)
    parser.add_argument("--traffics", nargs="+", choices=TRAFFIC_NAMES,
                        required=True)
    parser.add_argument("--rate", type=float, default=0.8)
    parser.add_argument("--seeds", type=int, nargs="+", default=[1, 2, 3])
    parser.add_argument("--warmup-cycles", type=int, default=20_000)
    parser.add_argument("--measurement-cycles", type=int, default=100_000)
    parser.add_argument("--deadlock-threshold", type=int, default=50_000)
    parser.add_argument("--reservation-weight", type=float, default=0.375)
    parser.add_argument("--vc-pressure-weight", type=float, default=0.625)
    parser.add_argument(
        "--express-info-mode",
        choices=["instant", "delayed-global", "distance-gossip"],
        default="instant",
    )
    parser.add_argument("--express-info-period", type=int, default=1)
    parser.add_argument("--express-info-delay", type=int, default=0)
    parser.add_argument("--express-info-bits", type=int, default=0)
    parser.add_argument("--express-admission-fraction", type=float, default=1.0)
    parser.add_argument(
        "--express-reservation-mode",
        choices=["instant", "registered"],
        default="instant",
    )
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--aggregate-only", action="store_true",
        help="rebuild aggregate files from output-dir/results.json",
    )
    parser.add_argument(
        "--supplement-results", type=Path, nargs="*", default=[],
        help=("on aggregate-only, replace matching topology/traffic/seed rows "
              "with rows from these retry result JSON files"),
    )
    args = parser.parse_args()
    if len(args.labels) != len(args.topologies):
        parser.error("--labels must match --topologies")
    if len(set(args.labels)) != len(args.labels):
        parser.error("labels must be unique")
    for path in args.topologies:
        validate_placement(8, load_placement(path), 64, 1, 3)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    garnet_runner.RESULTS = args.output_dir.resolve()
    for label, path in zip(args.labels, args.topologies):
        # pathlib discards the left operand when the right operand is absolute,
        # so run_one can consume an arbitrary file without copying it.
        garnet_runner.TOPOLOGIES[label] = path.resolve()

    specs = [
        (label, "adaptive", traffic, args.rate, seed)
        for label in args.labels for traffic in args.traffics
        for seed in args.seeds
    ]

    def run(spec):
        return garnet_runner.run_one(
            spec,
            args.warmup_cycles,
            args.measurement_cycles,
            args.deadlock_threshold,
            source_route=True,
            source_route_policy=4,
            no_escape=False,
            topology_dir=Path("."),
            reservation_weight=args.reservation_weight,
            vc_pressure_weight=args.vc_pressure_weight,
            express_budget=64,
            express_max_degree=1,
            express_min_wire_length=3,
            express_info_mode=args.express_info_mode,
            express_info_period=args.express_info_period,
            express_info_delay=args.express_info_delay,
            express_info_bits=args.express_info_bits,
            express_admission_fraction=args.express_admission_fraction,
            express_reservation_mode=args.express_reservation_mode,
        )

    rows, failures = [], []
    if args.aggregate_only:
        rows = json.loads((args.output_dir / "results.json").read_text(
            encoding="utf-8",
        ))
        keyed = {
            (row["topology"], row["traffic"], row["seed"]): row
            for row in rows
        }
        for path in args.supplement_results:
            supplemental = json.loads(path.read_text(encoding="utf-8"))
            for row in supplemental:
                keyed[(row["topology"], row["traffic"], row["seed"])] = row
        rows = list(keyed.values())
    else:
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            futures = {executor.submit(run, spec): spec for spec in specs}
            for index, future in enumerate(as_completed(futures), 1):
                spec = futures[future]
                try:
                    row = future.result()
                    rows.append(row)
                    print(f"[{index}/{len(specs)}] {spec}", flush=True)
                except Exception as error:
                    failures.append({"spec": spec, "error": str(error)})
                    print(f"[FAILED] {spec}: {error}", flush=True)
    rows.sort(key=lambda row: (
        row["traffic"], row["topology"], row["seed"],
    ))
    if not args.aggregate_only:
        (args.output_dir / "results.json").write_text(
            json.dumps(rows, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    for row in rows:
        if row.get("termination_reason") != "simulate_limit":
            failures.append({
                "spec": [
                    row.get("topology"), row.get("routing"),
                    row.get("traffic"), row.get("rate"), row.get("seed"),
                ],
                "error": "Garnet run did not reach the simulation limit",
                "termination_reason": row.get("termination_reason", "missing"),
            })
    (args.output_dir / "failures.json").write_text(
        json.dumps(failures, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    metric_names = [
        "accepted_throughput", "average_packet_latency_cycles",
        "average_hops", "express_traversals_per_packet",
        "delivered_escape_fraction", "max_link_utilization",
    ]
    aggregate = []
    for traffic in args.traffics:
        for label in args.labels:
            matching = [row for row in rows
                        if row["traffic"] == traffic
                        and row["topology"] == label]
            samples = [row for row in matching
                       if row.get("termination_reason") == "simulate_limit"]
            if not samples:
                continue
            item = {
                "traffic": traffic,
                "placement_label": label,
                "rate": args.rate,
                "express_info_mode": args.express_info_mode,
                "express_info_period": args.express_info_period,
                "express_info_delay": args.express_info_delay,
                "express_info_bits": args.express_info_bits,
                "express_admission_fraction": args.express_admission_fraction,
                "express_reservation_mode": args.express_reservation_mode,
                "reservation_weight": args.reservation_weight,
                "vc_pressure_weight": args.vc_pressure_weight,
                "requested_seeds": args.seeds,
                "completed_seeds": [row["seed"] for row in samples],
                "requested_sample_count": len(args.seeds),
                "sample_count": len(samples),
                "failed_sample_count": len(args.seeds) - len(samples),
            }
            for metric in metric_names:
                if metric == "express_traversals_per_packet":
                    values = [
                        float(row.get("express_traversals", 0.0)) /
                        max(float(row.get("packets_received", 0.0)), 1.0)
                        for row in samples
                    ]
                else:
                    values = [float(row.get(metric, 0.0)) for row in samples]
                item[f"{metric}_mean"] = statistics.fmean(values)
                item[f"{metric}_sd"] = (statistics.stdev(values)
                                         if len(values) > 1 else 0.0)
            aggregate.append(item)
    (args.output_dir / "aggregate.json").write_text(
        json.dumps(aggregate, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if aggregate:
        with (args.output_dir / "aggregate.csv").open(
                "w", newline="", encoding="utf-8") as output:
            writer = csv.DictWriter(output, fieldnames=list(aggregate[0]))
            writer.writeheader()
            writer.writerows(aggregate)
    for item in aggregate:
        print(
            f"{item['traffic']:16s} {item['placement_label']:24s} "
            f"th={item['accepted_throughput_mean']:.6f} "
            f"lat={item['average_packet_latency_cycles_mean']:.1f} "
            f"valid={item['sample_count']}/"
            f"{item['requested_sample_count']}",
        )

    class_aggregate = []
    for traffic in args.traffics:
        for topology_class in ("mesh", "random", "greedy", "sa"):
            samples = []
            for row in rows:
                label = row["topology"]
                row_class = (
                    "random" if label.startswith("random_") else label
                )
                if (row["traffic"] == traffic and
                        row_class == topology_class and
                        row.get("termination_reason") == "simulate_limit"):
                    samples.append(row)
            if not samples:
                continue
            item = {
                "traffic": traffic,
                "topology_class": topology_class,
                "rate": args.rate,
                "sample_count": len(samples),
                "placement_count": len({row["topology"] for row in samples}),
            }
            for metric in metric_names:
                if metric == "express_traversals_per_packet":
                    values = [
                        float(row.get("express_traversals", 0.0)) /
                        max(float(row.get("packets_received", 0.0)), 1.0)
                        for row in samples
                    ]
                else:
                    values = [float(row.get(metric, 0.0)) for row in samples]
                item[f"{metric}_mean"] = statistics.fmean(values)
                item[f"{metric}_sd"] = (
                    statistics.stdev(values) if len(values) > 1 else 0.0
                )
            class_aggregate.append(item)
    (args.output_dir / "class_aggregate.json").write_text(
        json.dumps(class_aggregate, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    hierarchy = []
    for traffic in args.traffics:
        grouped = {
            item["topology_class"]: item for item in class_aggregate
            if item["traffic"] == traffic
        }
        if len(grouped) != 4:
            continue
        order = ["mesh", "random", "greedy", "sa"]
        throughput = {
            key: grouped[key]["accepted_throughput_mean"] for key in order
        }
        latency = {
            key: grouped[key]["average_packet_latency_cycles_mean"]
            for key in order
        }
        hierarchy.append({
            "traffic": traffic,
            "rate": args.rate,
            "throughput": throughput,
            "latency": latency,
            "strict_throughput_order": all(
                throughput[left] < throughput[right]
                for left, right in zip(order, order[1:])
            ),
            "strict_latency_order": all(
                latency[left] > latency[right]
                for left, right in zip(order, order[1:])
            ),
            "sa_pareto_over_greedy": (
                throughput["sa"] >= throughput["greedy"] and
                latency["sa"] <= latency["greedy"] and
                (throughput["sa"] > throughput["greedy"] or
                 latency["sa"] < latency["greedy"])
            ),
        })
    (args.output_dir / "hierarchy.json").write_text(
        json.dumps(hierarchy, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    for item in hierarchy:
        values = item["throughput"]
        print(
            f"hierarchy {item['traffic']:16s} "
            f"{values['mesh']:.6f} < {values['random']:.6f} < "
            f"{values['greedy']:.6f} < {values['sa']:.6f}; "
            f"strict={item['strict_throughput_order']} "
            f"pareto={item['sa_pareto_over_greedy']}",
        )
    if failures:
        raise SystemExit(f"{len(failures)} Garnet runs failed")


if __name__ == "__main__":
    main()
