#!/usr/bin/env python3
"""Build an auditable aggregate from per-run validation JSON files."""

import json
import statistics
from pathlib import Path


ROOT = Path(__file__).resolve().parent / "results" / "phase3_measurement_v2"


def main():
    rows = []
    for path in (ROOT / "runs").glob("*/result.json"):
        row = json.loads(path.read_text(encoding="utf-8"))
        if row.get("result_schema_version", 0) >= 3:
            rows.append(row)

    groups = []
    for topology in sorted({r["topology"] for r in rows}):
        rates = sorted({r["configured_injection_rate"] for r in rows
                        if r["topology"] == topology})
        for rate in rates:
            for escape_enabled in (True, False):
                samples = [r for r in rows if r["topology"] == topology and
                           r["configured_injection_rate"] == rate and
                           r.get("source_route_policy") == 2 and
                           r.get("escape_enabled", True) == escape_enabled and
                           r.get("warmup_cycles") == 20_000 and
                           r.get("measurement_cycles") == 100_000]
                if not samples:
                    continue
                completed = [r for r in samples if not r.get("no_progress") and
                             r.get("termination_reason") in {"simulate_limit", "completed"}]
                escape_samples = [r for r in completed
                                  if r.get("result_schema_version", 0) >= 5]
                groups.append({
                "topology": topology,
                "rate": rate,
                "escape_enabled": escape_enabled,
                "placement_seeds": sorted({r.get("random_placement_seed", 1)
                                            for r in samples}),
                "traffic_seeds": sorted({r["seed"] for r in samples}),
                "samples": len(samples),
                "completed": len(completed),
                "failures": len(samples) - len(completed),
                "schema_versions": sorted({r["result_schema_version"] for r in samples}),
                "throughput_mean_completed": (statistics.mean(
                    r["accepted_throughput"] for r in completed)
                    if completed else None),
                "actual_offered_throughput_mean": (statistics.mean(
                    r["actual_offered_throughput"] for r in completed)
                    if completed else None),
                "throughput_stdev_completed": (statistics.pstdev(
                    r["accepted_throughput"] for r in completed)
                    if len(completed) > 1 else 0.0),
                "latency_mean_completed": (statistics.mean(
                    r["average_packet_latency_cycles"] for r in completed)
                    if completed else None),
                "escape_metric_samples": len(escape_samples),
                "escape_fraction_mean": (statistics.mean(
                    r["delivered_escape_fraction"] for r in escape_samples)
                    if escape_samples else None),
                "avg_cycles_before_escape_mean": (statistics.mean(
                    r["average_cycles_before_escape"] for r in escape_samples)
                    if escape_samples else None),
                })
    output = {"run_count": len(rows), "groups": groups}
    path = ROOT / "validation_aggregate.json"
    path.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(rows)} runs and {len(groups)} groups to {path}")


if __name__ == "__main__":
    main()
