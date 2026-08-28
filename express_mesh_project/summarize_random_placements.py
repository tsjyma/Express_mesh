#!/usr/bin/env python3
"""Aggregate q+r Random topology runs across placement and traffic seeds."""

import json
import statistics
from pathlib import Path


ROOT = Path(__file__).resolve().parent / "results" / "phase3_measurement_v2"


def main():
    rows = []
    for path in (ROOT / "runs").glob(
            "random_deterministic_uniform_random_r*_s*_p*_source_route_qr/result.json"):
        row = json.loads(path.read_text(encoding="utf-8"))
        if (row.get("result_schema_version", 0) >= 5 and
                row.get("warmup_cycles") == 20_000 and
                row.get("measurement_cycles") == 100_000):
            rows.append(row)
    # Placement seed 1 predates the explicit _p1 tag.
    for path in (ROOT / "runs").glob(
            "random_deterministic_uniform_random_r*_s*_source_route_qr/result.json"):
        if "_p" in path.parent.name:
            continue
        row = json.loads(path.read_text(encoding="utf-8"))
        if (row.get("result_schema_version", 0) >= 5 and
                row.get("warmup_cycles") == 20_000 and
                row.get("measurement_cycles") == 100_000):
            row["random_placement_seed"] = 1
            rows.append(row)

    summary = []
    for rate in (0.4, 0.5, 0.6):
        samples = [r for r in rows if r["configured_injection_rate"] == rate]
        if len(samples) != 9:
            raise RuntimeError(f"rate {rate}: expected 9 samples, found {len(samples)}")
        summary.append({
            "configured_injection_rate": rate,
            "placement_seeds": sorted({r["random_placement_seed"] for r in samples}),
            "traffic_seeds": sorted({r["seed"] for r in samples}),
            "samples": len(samples),
            "accepted_throughput_mean": statistics.mean(
                r["accepted_throughput"] for r in samples),
            "accepted_throughput_stdev": statistics.pstdev(
                r["accepted_throughput"] for r in samples),
            "average_packet_latency_cycles_mean": statistics.mean(
                r["average_packet_latency_cycles"] for r in samples),
            "max_link_utilization_mean": statistics.mean(
                r["max_link_utilization"] for r in samples),
            "delivered_escape_fraction_mean": statistics.mean(
                r["delivered_escape_fraction"] for r in samples),
            "delivered_escape_fraction_stdev": statistics.pstdev(
                r["delivered_escape_fraction"] for r in samples),
            "average_cycles_before_escape_mean": statistics.mean(
                r["average_cycles_before_escape"] for r in samples),
        })
    output = {"runs": rows, "summary": summary}
    path = ROOT / "random_placement_results.json"
    path.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(rows)} runs to {path}")


if __name__ == "__main__":
    main()
