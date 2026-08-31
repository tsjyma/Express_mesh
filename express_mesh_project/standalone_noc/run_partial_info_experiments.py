#!/usr/bin/env python3
"""Compare topology quality under incomplete express-pressure information.

The routing algorithm is fixed to policy 4 with top-8 committed paths and XY
mesh segments.  Only the q/r observation model changes.  Random performance is
an expectation over independently generated, constraint-matched placements.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import csv
from dataclasses import dataclass, asdict
import json
from pathlib import Path
import statistics
import subprocess


HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent
BINARY = HERE / "express_noc"


@dataclass(frozen=True)
class InfoConfig:
    mode: str
    period: int = 1
    delay: int = 0
    bits: int = 0
    admission: float = 1.0


INFO_CONFIGS = {
    "instant": InfoConfig("instant"),
    "global-p1-d1-b0": InfoConfig("delayed-global", 1, 1, 0),
    "global-p2-d1-b0": InfoConfig("delayed-global", 2, 1, 0),
    "global-p4-d1-b2": InfoConfig("delayed-global", 4, 1, 2),
    "global-p8-d4-b2": InfoConfig("delayed-global", 8, 4, 2),
    "gossip-p1-d0-b0": InfoConfig("distance-gossip", 1, 0, 0),
    "gossip-p1-d1-b0": InfoConfig("distance-gossip", 1, 1, 0),
    "gossip-p2-d1-b0": InfoConfig("distance-gossip", 2, 1, 0),
    "gossip-p4-d1-b0": InfoConfig("distance-gossip", 4, 1, 0),
    "gossip-p1-d1-b3": InfoConfig("distance-gossip", 1, 1, 3),
    "gossip-p2-d1-b3": InfoConfig("distance-gossip", 2, 1, 3),
    "gossip-p1-d1-b4": InfoConfig("distance-gossip", 1, 1, 4),
    "gossip-p2-d1-b4": InfoConfig("distance-gossip", 2, 1, 4),
    "gossip-p1-d1-b4-a75": InfoConfig(
        "distance-gossip", 1, 1, 4, 0.75,
    ),
    "gossip-p1-d1-b4-a90": InfoConfig(
        "distance-gossip", 1, 1, 4, 0.90,
    ),
    "gossip-p1-d1-b4-a85": InfoConfig(
        "distance-gossip", 1, 1, 4, 0.85,
    ),
    "gossip-p1-d1-b4-a80": InfoConfig(
        "distance-gossip", 1, 1, 4, 0.80,
    ),
    "gossip-p1-d1-b4-a50": InfoConfig(
        "distance-gossip", 1, 1, 4, 0.50,
    ),
    "gossip-p1-d1-b4-a25": InfoConfig(
        "distance-gossip", 1, 1, 4, 0.25,
    ),
    "gossip-p4-d1-b2": InfoConfig("distance-gossip", 4, 1, 2),
    "gossip-p8-d1-b2": InfoConfig("distance-gossip", 8, 1, 2),
    "gossip-p16-d1-b2": InfoConfig("distance-gossip", 16, 1, 2),
    "gossip-p4-d1-b1": InfoConfig("distance-gossip", 4, 1, 1),
}


def topology_specs(traffic: str, random_seeds: list[int]):
    baseline = PROJECT / "results" / "placement_v6" / "baselines"
    search = PROJECT / "results" / "placement_v6" / "search" / traffic
    if traffic == "uniform_random":
        greedy = baseline / "uniform_random_aspl.json"
        sa = search / "sa_reheat" / "best.json"
    elif traffic == "tornado":
        greedy = baseline / "tornado_aspl.json"
        sa = search / "sa_tempering" / "best.json"
    else:
        raise ValueError(f"unsupported traffic: {traffic}")
    result = [
        ("mesh", "mesh", PROJECT / "results" / "phase1" / "mesh.json", 0),
        ("greedy", "greedy", greedy, 0),
        ("sa", "sa", sa, 0),
    ]
    for placement_seed in random_seeds:
        result.append((
            f"random_p{placement_seed}", "random",
            PROJECT / "results" / "design_space" / "random_b64_d1" /
            f"p{placement_seed}" / "random.json",
            placement_seed,
        ))
    for _, _, path, _ in result:
        if not path.exists():
            raise FileNotFoundError(path)
    return result


def mean_sd(values):
    return (
        statistics.fmean(values),
        statistics.stdev(values) if len(values) > 1 else 0.0,
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--traffics", nargs="+",
                        choices=["uniform_random", "tornado"],
                        default=["uniform_random", "tornado"])
    parser.add_argument("--rates", nargs="+", type=float,
                        default=[0.7, 0.8, 0.9])
    parser.add_argument("--traffic-seeds", nargs="+", type=int,
                        default=[1, 2, 3])
    parser.add_argument("--random-seeds", nargs="+", type=int,
                        default=list(range(1, 21)))
    parser.add_argument("--info-configs", nargs="+", choices=INFO_CONFIGS,
                        default=list(INFO_CONFIGS))
    parser.add_argument("--warmup-cycles", type=int, default=5_000)
    parser.add_argument("--measurement-cycles", type=int, default=30_000)
    parser.add_argument("--reservation-weight", type=float, default=0.375)
    parser.add_argument("--vc-pressure-weight", type=float, default=0.625)
    parser.add_argument(
        "--express-reservation-mode", choices=["instant", "registered"],
        default="instant",
    )
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if not BINARY.exists():
        subprocess.run(["make", "-C", str(HERE)], check=True)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    run_dir = args.output_dir / "runs"
    run_dir.mkdir(exist_ok=True)

    jobs = []
    for traffic in args.traffics:
        for topology in topology_specs(traffic, args.random_seeds):
            for rate in args.rates:
                for traffic_seed in args.traffic_seeds:
                    for info_name in args.info_configs:
                        jobs.append((
                            traffic, topology, rate, traffic_seed, info_name,
                        ))

    def run(job):
        traffic, topology, rate, traffic_seed, info_name = job
        label, topology_class, topology_file, placement_seed = topology
        info = INFO_CONFIGS[info_name]
        output = run_dir / (
            f"{traffic}_{label}_r{rate:.3f}_s{traffic_seed}_{info_name}_"
            f"{args.express_reservation_mode}.json"
        )
        if args.resume and output.exists():
            row = json.loads(output.read_text(encoding="utf-8"))
        else:
            command = [
                str(BINARY), "--topology-file", str(topology_file),
                "--topology", label, "--routing", "adaptive",
                "--traffic", traffic, "--rate", str(rate),
                "--seed", str(traffic_seed),
                "--warmup-cycles", str(args.warmup_cycles),
                "--measurement-cycles", str(args.measurement_cycles),
                "--source-route", "--source-route-policy", "4",
                "--source-route-candidates", "8",
                "--source-mesh-routing", "xy",
                "--reservation-weight", str(args.reservation_weight),
                "--express-vc-weight", str(args.vc_pressure_weight),
                "--express-wire-budget", "64",
                "--express-max-degree", "1",
                "--express-min-wire-length", "3",
                "--random-placement-seed", str(placement_seed),
                "--express-info-mode", info.mode,
                "--express-info-period", str(info.period),
                "--express-info-delay", str(info.delay),
                "--express-info-bits", str(info.bits),
                "--express-admission-fraction", str(info.admission),
                "--express-reservation-mode", args.express_reservation_mode,
                "--output", str(output),
            ]
            completed = subprocess.run(
                command, text=True, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, check=False,
            )
            if completed.returncode:
                raise RuntimeError(completed.stdout.strip())
            row = json.loads(output.read_text(encoding="utf-8"))
        row["topology_class"] = topology_class
        row["topology_file"] = str(topology_file)
        row["info_config"] = info_name
        return row

    rows, failures = [], []
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(run, job): job for job in jobs}
        for index, future in enumerate(as_completed(futures), 1):
            try:
                rows.append(future.result())
                if index % 25 == 0 or index == len(jobs):
                    print(f"[{index}/{len(jobs)}]", flush=True)
            except Exception as error:
                failures.append({"job": str(futures[future]),
                                 "error": str(error)})
                print(f"[FAILED] {futures[future]}: {error}", flush=True)
    rows.sort(key=lambda row: (
        row["traffic"], row["configured_injection_rate"],
        row["info_config"], row["topology_class"], row["topology"],
        row["seed"],
    ))
    (args.output_dir / "results.json").write_text(
        json.dumps(rows, indent=2, sort_keys=True) + "\n", encoding="utf-8",
    )
    (args.output_dir / "failures.json").write_text(
        json.dumps(failures, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    aggregate = []
    for traffic in args.traffics:
        for rate in args.rates:
            for info_name in args.info_configs:
                for topology_class in ["mesh", "random", "greedy", "sa"]:
                    samples = [
                        row for row in rows
                        if row["traffic"] == traffic
                        and row["configured_injection_rate"] == rate
                        and row["info_config"] == info_name
                        and row["topology_class"] == topology_class
                        and row["termination_reason"] == "simulate_limit"
                    ]
                    if not samples:
                        continue
                    item = {
                        "traffic": traffic,
                        "rate": rate,
                        "info_config": info_name,
                        **asdict(INFO_CONFIGS[info_name]),
                        "topology_class": topology_class,
                        "sample_count": len(samples),
                        "placement_count": len({
                            row["topology_file"] for row in samples
                        }),
                        "reservation_weight": args.reservation_weight,
                        "vc_pressure_weight": args.vc_pressure_weight,
                        "express_reservation_mode":
                            args.express_reservation_mode,
                    }
                    for metric in [
                        "accepted_throughput",
                        "average_packet_latency_cycles",
                        "delivered_escape_fraction",
                        "express_info_q_mae", "express_info_r_mae",
                    ]:
                        values = [float(row[metric]) for row in samples]
                        item[f"{metric}_mean"], item[f"{metric}_sd"] = \
                            mean_sd(values)
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

    hierarchy = []
    for traffic in args.traffics:
        for rate in args.rates:
            for info_name in args.info_configs:
                grouped = {
                    item["topology_class"]: item
                    for item in aggregate
                    if item["traffic"] == traffic and item["rate"] == rate
                    and item["info_config"] == info_name
                }
                if len(grouped) != 4:
                    continue
                throughput = {
                    key: value["accepted_throughput_mean"]
                    for key, value in grouped.items()
                }
                latency = {
                    key: value["average_packet_latency_cycles_mean"]
                    for key, value in grouped.items()
                }
                order = ["mesh", "random", "greedy", "sa"]
                hierarchy.append({
                    "traffic": traffic,
                    "rate": rate,
                    "info_config": info_name,
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
                        throughput["sa"] >= throughput["greedy"]
                        and latency["sa"] <= latency["greedy"]
                        and (throughput["sa"] > throughput["greedy"]
                             or latency["sa"] < latency["greedy"])
                    ),
                })
    (args.output_dir / "hierarchy.json").write_text(
        json.dumps(hierarchy, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    for item in hierarchy:
        values = item["throughput"]
        print(
            f"{item['traffic']:15s} r={item['rate']:.2f} "
            f"{item['info_config']:20s} "
            f"th={values['mesh']:.4f}<{values['random']:.4f}<"
            f"{values['greedy']:.4f}<{values['sa']:.4f} "
            f"strict={item['strict_throughput_order']} "
            f"pareto={item['sa_pareto_over_greedy']}",
        )
    if failures:
        raise SystemExit(f"{len(failures)} runs failed")


if __name__ == "__main__":
    main()
