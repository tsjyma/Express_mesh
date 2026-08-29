#!/usr/bin/env python3
"""Diagnose NI watchdogs versus true global NoC no-progress and drainability."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from pathlib import Path
import subprocess


HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent
BINARY = HERE / "express_noc"
TOPOLOGIES = {
    "mesh": PROJECT / "results/phase1/mesh.json",
    "hybrid": PROJECT / "results/phase1/hybrid.json",
}


def cases():
    for topology in TOPOLOGIES:
        for policy, mode in ((2, "escape_qr"), (3, "escape_random_candidate")):
            for rate in (0.40, 0.50, 0.60):
                for seed in (1, 2, 3):
                    yield mode, topology, policy, rate, seed, False, False
    for topology in TOPOLOGIES:
        for corrected, mode in ((False, "noescape_legacy"),
                                (True, "noescape_corrected")):
            for seed in (1, 2, 3):
                yield mode, topology, 2, 0.50, seed, True, corrected


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--warmup-cycles", type=int, default=20_000)
    parser.add_argument("--measurement-cycles", type=int, default=100_000)
    parser.add_argument("--drain-cycles", type=int, default=500_000)
    parser.add_argument("--deadlock-threshold", type=int, default=50_000)
    parser.add_argument("--results", type=Path,
                        default=HERE / "results/escape_diagnostics")
    args = parser.parse_args()
    args.results.mkdir(parents=True, exist_ok=True)

    if not BINARY.exists():
        subprocess.run(["make", "-C", str(HERE)], check=True)

    def run_one(case):
        mode, topology, policy, rate, seed, no_escape, corrected = case
        tag = f"{mode}_{topology}_r{rate:.2f}_s{seed}"
        output = args.results / f"{tag}.json"
        command = [
            str(BINARY),
            "--topology-file", str(TOPOLOGIES[topology]),
            "--topology", topology,
            "--routing", "deterministic",
            "--traffic", "uniform_random",
            "--rate", str(rate),
            "--seed", str(seed),
            "--warmup-cycles", str(args.warmup_cycles),
            "--measurement-cycles", str(args.measurement_cycles),
            "--deadlock-threshold", str(args.deadlock_threshold),
            "--drain-cycles", str(args.drain_cycles),
            "--continue-after-ni-watchdog",
            "--source-route", "--source-route-policy", str(policy),
            "--output", str(output),
        ]
        if no_escape:
            command.append("--no-escape")
        if corrected:
            command.append("--correct-no-escape-vcs")
        completed = subprocess.run(command, text=True, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, check=False)
        if completed.returncode:
            raise RuntimeError(f"{tag}: {completed.stdout.strip()}")
        row = json.loads(output.read_text())
        row["diagnostic_mode"] = mode
        return row

    rows = []
    all_cases = list(cases())
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(run_one, case): case for case in all_cases}
        for index, future in enumerate(as_completed(futures), 1):
            row = future.result()
            rows.append(row)
            print(f"[{index}/{len(all_cases)}] {row['diagnostic_mode']} "
                  f"{row['topology']} r={row['configured_injection_rate']} "
                  f"s={row['seed']} watchdog={row['ni_watchdog_triggered']} "
                  f"global={row['global_no_progress_detected']} "
                  f"drained={row['drain_completed']}", flush=True)

    rows.sort(key=lambda row: (row["diagnostic_mode"], row["topology"],
                               row["configured_injection_rate"], row["seed"]))
    (args.results / "all_results.json").write_text(
        json.dumps(rows, indent=2) + "\n")

    groups = []
    keys = sorted({(row["diagnostic_mode"], row["topology"],
                    row["configured_injection_rate"]) for row in rows})
    for mode, topology, rate in keys:
        sample = [row for row in rows
                  if (row["diagnostic_mode"], row["topology"],
                      row["configured_injection_rate"]) ==
                     (mode, topology, rate)]
        groups.append({
            "mode": mode,
            "topology": topology,
            "rate": rate,
            "samples": len(sample),
            "ni_watchdogs": sum(row["ni_watchdog_triggered"] for row in sample),
            "global_no_progress": sum(row["global_no_progress_detected"]
                                      for row in sample),
            "drained": sum(row["drain_completed"] for row in sample),
            "watchdog_move_ages": [row["watchdog_cycles_since_flit_move"]
                                    for row in sample
                                    if row["ni_watchdog_triggered"]],
            "watchdog_delivery_ages": [row["watchdog_cycles_since_delivery"]
                                        for row in sample
                                        if row["ni_watchdog_triggered"]],
            "max_ni_busy_streaks_before_drain": [
                row["max_ni_busy_streak_before_drain"] for row in sample],
            "drain_completion_cycles": [row["drain_completion_cycle"]
                                         for row in sample],
            "accepted_throughputs": [row["accepted_throughput"]
                                      for row in sample],
            "escape_fractions": [row["delivered_escape_fraction"]
                                  for row in sample],
        })
    summary = {
        "warmup_cycles": args.warmup_cycles,
        "measurement_cycles": args.measurement_cycles,
        "drain_cycles": args.drain_cycles,
        "deadlock_threshold": args.deadlock_threshold,
        "groups": groups,
    }
    (args.results / "summary.json").write_text(
        json.dumps(summary, indent=2) + "\n")


if __name__ == "__main__":
    main()
