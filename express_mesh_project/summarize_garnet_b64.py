#!/usr/bin/env python3
"""Aggregate the final budget-64 Garnet validation runs."""

from __future__ import annotations

import csv
from collections import defaultdict
import json
from pathlib import Path
import statistics


ROOT = Path(__file__).resolve().parent / "results" / "garnet_b64_final"
TOPOLOGIES = ("mesh", "stride_random", "stride_aspl")


def main():
    runs = []
    for path in ROOT.glob("*/main_results.json"):
        runs.extend(json.loads(path.read_text(encoding="utf-8")))
    if len(runs) != 36:
        raise RuntimeError(f"expected 36 runs, found {len(runs)}")
    bad = [r for r in runs if r["termination_reason"] != "simulate_limit"
           or r["no_progress"]]
    if bad:
        raise RuntimeError(f"{len(bad)} runs terminated abnormally")

    groups = defaultdict(list)
    for row in runs:
        groups[(row["traffic"], row["configured_injection_rate"],
                row["topology"])].append(row)
    summary = []
    for (traffic, rate, topology), samples in sorted(groups.items()):
        throughput = [r["accepted_throughput"] for r in samples]
        latency = [r["average_packet_latency_cycles"] for r in samples]
        express = [
            r["express_traversals"] / r["packets_received"]
            if r["packets_received"] else 0.0 for r in samples
        ]
        summary.append({
            "traffic": traffic,
            "configured_injection_rate": rate,
            "topology": topology,
            "seeds": sorted(r["seed"] for r in samples),
            "accepted_throughput_mean": statistics.fmean(throughput),
            "accepted_throughput_sd": statistics.pstdev(throughput),
            "average_packet_latency_cycles_mean": statistics.fmean(latency),
            "average_packet_latency_cycles_sd": statistics.pstdev(latency),
            "express_traversals_per_packet_mean": statistics.fmean(express),
            "delivered_escape_fraction_mean": statistics.fmean(
                r["delivered_escape_fraction"] for r in samples),
            "max_link_utilization_mean": statistics.fmean(
                r["max_link_utilization"] for r in samples),
        })

    lookup = {(r["traffic"], r["configured_injection_rate"], r["topology"]): r
              for r in summary}
    comparisons = []
    for traffic, rate in sorted({(r["traffic"], r["configured_injection_rate"])
                                 for r in summary}):
        values = {topology: lookup[traffic, rate, topology]
                  ["accepted_throughput_mean"] for topology in TOPOLOGIES}
        comparisons.append({
            "traffic": traffic,
            "configured_injection_rate": rate,
            "stride_random_vs_mesh_percent":
                100.0 * (values["stride_random"] / values["mesh"] - 1.0),
            "stride_aspl_vs_mesh_percent":
                100.0 * (values["stride_aspl"] / values["mesh"] - 1.0),
            "stride_aspl_vs_stride_random_percent":
                100.0 * (values["stride_aspl"] / values["stride_random"] - 1.0),
        })

    output = {"run_count": len(runs), "abnormal_run_count": len(bad),
              "summary": summary, "comparisons": comparisons}
    path = ROOT / "aggregate.json"
    path.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    with (ROOT / "aggregate.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=summary[0].keys())
        writer.writeheader()
        writer.writerows(summary)
    print(f"Wrote {len(summary)} aggregate points to {path}")


if __name__ == "__main__":
    main()
