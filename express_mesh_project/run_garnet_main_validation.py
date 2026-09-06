#!/usr/bin/env python3
"""Run the report's headline main-result points in Garnet, resumably.

The topology and traffic sampling rules mirror ``main_cases`` in
``run_20260831_standalone_suite.py``: fixed placements use holdout traffic
seeds 5--8, while Random is an expectation over ten layouts with two traffic
seeds per layout.  Each completed sample is cached immediately so a long,
low-concurrency Garnet validation can be resumed safely.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import csv
import hashlib
import json
from pathlib import Path
import statistics
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import express_mesh_project.run_phase3_measurement_v2 as garnet


ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "express_mesh_project"
DEFAULT_OUTPUT = PROJECT / "results" / "20260906" / "garnet_main_validation"
TRAFFICS = (
    "uniform_random", "tornado", "bit_complement",
    "cutstress_bidirectional",
)


def topology_catalog() -> dict[str, dict[str, Path]]:
    catalog: dict[str, dict[str, Path]] = {}
    mesh = PROJECT / "results" / "phase1" / "mesh.json"
    for traffic in TRAFFICS:
        catalog[traffic] = {
            "mesh": mesh,
            "greedy": (PROJECT / "results" / "20260831" / "placements" /
                       "b32" / f"{traffic}_aspl.json"),
            "sa": (PROJECT / "results" / "20260902" /
                   "sa_curve_unified_v2" / traffic / "best.json"),
        }
    random_root = (PROJECT / "results" / "20260831" / "placements" /
                   "random_b32")
    catalog["random"] = {
        f"random_p{seed}": random_root / f"p{seed}" / "random.json"
        for seed in range(1, 11)
    }
    for group in catalog.values():
        for path in group.values():
            if not path.exists():
                raise FileNotFoundError(path)
    return catalog


def build_cases(catalog, rates: tuple[float, ...]) -> list[dict]:
    cases = []
    for traffic in TRAFFICS:
        for rate in rates:
            for topology_class in ("mesh", "greedy", "sa"):
                label = (topology_class if topology_class == "mesh" else
                         f"{traffic}_{topology_class}")
                path = catalog[traffic][topology_class].resolve()
                for seed in (5, 6, 7, 8):
                    cases.append({
                        "label": label, "topology_class": topology_class,
                        "topology_file": path, "traffic": traffic,
                        "rate": rate, "seed": seed,
                    })
            for topology_seed in range(1, 11):
                label = f"random_p{topology_seed}"
                path = catalog["random"][label].resolve()
                for seed in (5, 6):
                    cases.append({
                        "label": label, "topology_class": "random",
                        "topology_file": path, "traffic": traffic,
                        "rate": rate, "seed": seed,
                    })
    return cases


def case_key(case: dict) -> str:
    identity = dict(case)
    path = identity.pop("topology_file")
    identity["topology_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    identity["schema"] = 1
    return hashlib.sha256(json.dumps(
        identity, sort_keys=True,
    ).encode()).hexdigest()[:20]


def run_case(case: dict, output: Path, resume: bool) -> dict:
    cache = output / "case_cache" / f"{case_key(case)}.json"
    if resume and cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    spec = (
        case["label"], "committed", case["traffic"],
        case["rate"], case["seed"],
    )
    row = garnet.run_one(
        spec, 20_000, 100_000, 50_000,
        source_route=True, source_route_policy=4, no_escape=False,
        topology_dir=Path("."), reservation_weight=0.6,
        vc_pressure_weight=1.0, express_budget=32,
        express_max_degree=1, express_min_wire_length=3,
        express_info_mode="distance-gossip", express_info_period=1,
        express_info_delay=1, express_info_bits=4,
        express_admission_fraction=1.0,
        express_reservation_mode="registered", dimension=8,
        source_route_candidates=8, router_latency=1,
        mesh_link_latency=1, vcs_per_vnet=4,
        buffers_per_data_vc=1, buffers_per_ctrl_vc=1,
        inj_vnet=0, escape_timeout=32,
    )
    row["topology_class"] = case["topology_class"]
    row["case_key"] = case_key(case)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(row, indent=2, sort_keys=True) + "\n",
                     encoding="utf-8")
    return row


def aggregate(rows: list[dict]) -> list[dict]:
    groups: dict[tuple, list[dict]] = {}
    for row in rows:
        key = (row["traffic"], row["configured_injection_rate"],
               row["topology_class"])
        groups.setdefault(key, []).append(row)
    result = []
    metrics = ("accepted_throughput", "average_packet_latency_cycles")
    for (traffic, rate, topology_class), samples in sorted(groups.items()):
        valid = [sample for sample in samples
                 if sample["termination_reason"] == "simulate_limit"]
        mapping_fractions = [
            sample["traffic_expected_destination_fraction"]
            for sample in valid
            if sample.get("traffic_expected_destination_fraction") is not None
        ]
        item = {
            "traffic": traffic, "rate": rate,
            "topology_class": topology_class,
            "sample_count": len(samples), "completed_count": len(valid),
            "active_source_min": min(
                sample["traffic_matrix_active_source_count"]
                for sample in valid) if valid else None,
            # Uniform has no single expected destination, so this diagnostic
            # is intentionally null for its groups.
            "mapping_fraction_min": (min(mapping_fractions)
                                     if mapping_fractions else None),
        }
        for metric in metrics:
            values = [float(sample[metric]) for sample in valid]
            item[f"{metric}_mean"] = (
                statistics.fmean(values) if values else None)
            item[f"{metric}_sd"] = (
                statistics.stdev(values) if len(values) > 1 else 0.0
                if values else None)
        result.append(item)
    return result


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rates", type=float, nargs="+",
                        default=[0.40, 0.70, 0.80])
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    if args.workers < 1:
        parser.error("--workers must be positive")
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    garnet.RESULTS = output / "gem5"
    catalog = topology_catalog()
    cases = build_cases(catalog, tuple(args.rates))
    for case in cases:
        garnet.TOPOLOGIES[case["label"]] = case["topology_file"]

    rows, failures = [], []
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(run_case, case, output, args.resume): case
            for case in cases
        }
        for index, future in enumerate(as_completed(futures), 1):
            case = futures[future]
            try:
                rows.append(future.result())
            except Exception as error:
                failures.append({
                    "case": {key: str(value) if isinstance(value, Path)
                             else value for key, value in case.items()},
                    "error": str(error),
                })
            if index % 10 == 0 or index == len(cases):
                print(f"[{index}/{len(cases)}] failures={len(failures)}",
                      flush=True)

    rows.sort(key=lambda row: (
        row["traffic"], row["configured_injection_rate"],
        row["topology_class"], row["topology"], row["seed"],
    ))
    summary = aggregate(rows)
    (output / "results.json").write_text(
        json.dumps(rows, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (output / "aggregate.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    (output / "failures.json").write_text(
        json.dumps(failures, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    write_csv(output / "aggregate.csv", summary)
    print(f"wrote {len(rows)} runs and {len(summary)} groups to {output}")


if __name__ == "__main__":
    main()
