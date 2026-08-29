#!/usr/bin/env python3
"""Measure performance sensitivity to the finite Escape transition timeout."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from pathlib import Path
import statistics
import subprocess


HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent
BINARY = HERE / "express_noc"
TOPOLOGIES = {
    "mesh": PROJECT / "results/phase1/mesh.json",
    "hybrid": PROJECT / "results/phase1/hybrid.json",
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--timeouts", type=int, nargs="+",
                        default=[0, 8, 16, 32, 64, 128])
    parser.add_argument("--results", type=Path,
                        default=HERE / "results/escape_timeout_sweep")
    args = parser.parse_args()
    args.results.mkdir(parents=True, exist_ok=True)

    cases = [(topology, rate, seed, timeout)
             for topology in TOPOLOGIES for rate in (0.40, 0.50, 0.60)
             for seed in (1, 2, 3) for timeout in args.timeouts]

    def run_one(case):
        topology, rate, seed, timeout = case
        output = args.results / (
            f"{topology}_r{rate:.2f}_s{seed}_timeout{timeout}.json")
        command = [
            str(BINARY), "--topology-file", str(TOPOLOGIES[topology]),
            "--topology", topology, "--routing", "deterministic",
            "--traffic", "uniform_random", "--rate", str(rate),
            "--seed", str(seed), "--warmup-cycles", "20000",
            "--measurement-cycles", "100000",
            "--deadlock-threshold", "50000",
            "--escape-timeout", str(timeout),
            "--source-route", "--source-route-policy", "2",
            "--output", str(output),
        ]
        completed = subprocess.run(command, text=True, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, check=False)
        if completed.returncode:
            raise RuntimeError(completed.stdout.strip())
        return json.loads(output.read_text())

    rows = []
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = [executor.submit(run_one, case) for case in cases]
        for index, future in enumerate(as_completed(futures), 1):
            rows.append(future.result())
            print(f"[{index}/{len(cases)}]", flush=True)

    groups = []
    for topology in TOPOLOGIES:
        for rate in (0.40, 0.50, 0.60):
            for timeout in args.timeouts:
                sample = [row for row in rows
                          if row["topology"] == topology and
                          row["configured_injection_rate"] == rate and
                          row["express_escape_timeout"] == timeout]
                groups.append({
                    "topology": topology,
                    "rate": rate,
                    "timeout": timeout,
                    "samples": len(sample),
                    "ni_watchdogs": sum(row["ni_watchdog_triggered"]
                                        for row in sample),
                    "throughput_mean": statistics.mean(
                        row["accepted_throughput"] for row in sample),
                    "latency_mean": statistics.mean(
                        row["average_packet_latency_cycles"] for row in sample),
                    "escape_fraction_mean": statistics.mean(
                        row["delivered_escape_fraction"] for row in sample),
                    "max_ni_busy_streak": max(
                        row["max_ni_busy_streak_before_drain"] for row in sample),
                })
    (args.results / "summary.json").write_text(
        json.dumps({"groups": groups}, indent=2) + "\n")


if __name__ == "__main__":
    main()
