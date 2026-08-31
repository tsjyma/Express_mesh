#!/usr/bin/env python3
"""Validate arbitrary placement JSON files with one fixed routing policy."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import csv
import json
from pathlib import Path
import statistics
import subprocess
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from express_mesh_project.search_placement_advanced import (
    DEFAULT_BINARY,
    TRAFFIC_NAMES,
    load_placement,
)
from express_mesh_project.placement import validate_placement


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--topologies", type=Path, nargs="+", required=True)
    parser.add_argument("--labels", nargs="+")
    parser.add_argument("--traffic", choices=TRAFFIC_NAMES, required=True)
    parser.add_argument("--rates", type=float, nargs="+", default=[0.8])
    parser.add_argument("--seeds", type=int, nargs="+", default=[1, 2, 3])
    parser.add_argument("--warmup-cycles", type=int, default=5000)
    parser.add_argument("--measurement-cycles", type=int, default=30000)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--wire-budget", type=int, default=64)
    parser.add_argument("--max-degree", type=int, default=1)
    parser.add_argument("--min-wire-length", type=int, default=3)
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
        choices=["instant", "registered"], default="instant",
    )
    parser.add_argument("--binary", type=Path, default=DEFAULT_BINARY)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--aggregate-only", action="store_true",
        help="rebuild aggregate files from output-dir/results.json",
    )
    args = parser.parse_args()
    labels = args.labels or [path.stem for path in args.topologies]
    if len(labels) != len(args.topologies):
        parser.error("--labels must match --topologies")
    if len(set(labels)) != len(labels):
        parser.error("topology labels must be unique")
    for path in args.topologies:
        validate_placement(
            8, load_placement(path), args.wire_budget, args.max_degree,
            args.min_wire_length,
        )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    run_dir = args.output_dir / "runs"
    run_dir.mkdir(exist_ok=True)

    def run(case):
        label, topology, rate, seed = case
        output = run_dir / f"{label}_{args.traffic}_r{rate:.3f}_s{seed}.json"
        command = [
            str(args.binary.resolve()), "--topology-file", str(topology.resolve()),
            "--topology", label, "--routing", "adaptive",
            "--traffic", args.traffic, "--rate", str(rate),
            "--seed", str(seed),
            "--warmup-cycles", str(args.warmup_cycles),
            "--measurement-cycles", str(args.measurement_cycles),
            "--source-route", "--source-route-policy", "4",
            "--source-route-candidates", "8", "--source-mesh-routing", "xy",
            "--reservation-weight", str(args.reservation_weight),
            "--express-vc-weight", str(args.vc_pressure_weight),
            "--express-info-mode", args.express_info_mode,
            "--express-info-period", str(args.express_info_period),
            "--express-info-delay", str(args.express_info_delay),
            "--express-info-bits", str(args.express_info_bits),
            "--express-admission-fraction",
            str(args.express_admission_fraction),
            "--express-reservation-mode", args.express_reservation_mode,
            "--express-wire-budget", str(args.wire_budget),
            "--express-max-degree", str(args.max_degree),
            "--express-min-wire-length", str(args.min_wire_length),
            "--output", str(output.resolve()),
        ]
        completed = subprocess.run(
            command, text=True, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, check=False,
        )
        if completed.returncode:
            raise RuntimeError(completed.stdout.strip())
        result = json.loads(output.read_text(encoding="utf-8"))
        result["placement_label"] = label
        result["placement_file"] = str(topology)
        return result

    cases = [
        (label, topology, rate, seed)
        for label, topology in zip(labels, args.topologies)
        for rate in args.rates for seed in args.seeds
    ]
    rows = []
    if args.aggregate_only:
        rows = json.loads((args.output_dir / "results.json").read_text(
            encoding="utf-8",
        ))
    else:
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            futures = {executor.submit(run, case): case for case in cases}
            for index, future in enumerate(as_completed(futures), 1):
                rows.append(future.result())
                print(f"[{index}/{len(cases)}] {futures[future]}", flush=True)
    rows.sort(key=lambda row: (
        row["placement_label"], row["configured_injection_rate"], row["seed"],
    ))

    metric_names = [
        "accepted_throughput", "average_packet_latency_cycles",
        "average_hops", "express_traversals_per_packet",
        "delivered_escape_fraction", "max_link_utilization",
    ]
    aggregate = []
    for label in labels:
        for rate in args.rates:
            samples = [row for row in rows
                       if row["placement_label"] == label
                       and row["configured_injection_rate"] == rate]
            item = {
                "placement_label": label,
                "placement_file": next(row["placement_file"] for row in samples),
                "traffic": args.traffic,
                "rate": rate,
                "seeds": args.seeds,
                "sample_count": len(samples),
                "reservation_weight": args.reservation_weight,
                "vc_pressure_weight": args.vc_pressure_weight,
                "express_info_mode": args.express_info_mode,
                "express_info_period": args.express_info_period,
                "express_info_delay": args.express_info_delay,
                "express_info_bits": args.express_info_bits,
                "express_admission_fraction": args.express_admission_fraction,
                "express_reservation_mode": args.express_reservation_mode,
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
    if not args.aggregate_only:
        (args.output_dir / "results.json").write_text(
            json.dumps(rows, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
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
            f"{item['placement_label']:24s} rate={item['rate']:.2f} "
            f"th={item['accepted_throughput_mean']:.6f} "
            f"lat={item['average_packet_latency_cycles_mean']:.1f}",
        )


if __name__ == "__main__":
    main()
