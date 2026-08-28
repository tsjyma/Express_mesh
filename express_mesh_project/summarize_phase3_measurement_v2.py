"""Aggregate the corrected Phase 3 measurement runs across traffic seeds."""

import csv
import json
import statistics
from collections import defaultdict
from pathlib import Path


RESULTS = Path(__file__).resolve().parent / "results" / "phase3_measurement_v2"
GROUP_KEYS = ("topology", "routing", "traffic", "configured_injection_rate")
METRICS = (
    "attempted_throughput", "actual_offered_throughput",
    "generated_request_throughput", "injected_throughput",
    "accepted_throughput", "delivery_fraction",
    "average_packet_latency_cycles", "average_hops", "express_traversals",
    "escape_transitions", "escape_traversals", "nonminimal_decisions",
    "max_link_utilization", "p95_link_utilization", "link_utilization_cv",
)


def main():
    rows = json.loads((RESULTS / "main_results.json").read_text())
    groups = defaultdict(list)
    for row in rows:
        groups[tuple(row[key] for key in GROUP_KEYS)].append(row)

    aggregate = []
    for key, samples in groups.items():
        row = dict(zip(GROUP_KEYS, key))
        row["seeds"] = len(samples)
        for metric in METRICS:
            values = [sample[metric] for sample in samples]
            row[f"{metric}_mean"] = statistics.mean(values)
            row[f"{metric}_stdev"] = (
                statistics.stdev(values) if len(values) > 1 else 0.0)
        received = sum(sample["packets_received"] for sample in samples)
        row["express_traversals_per_received_packet"] = (
            sum(sample["express_traversals"] for sample in samples) / received
            if received else 0.0)
        aggregate.append(row)

    aggregate.sort(key=lambda row: tuple(row[key] for key in GROUP_KEYS))
    (RESULTS / "aggregate_results.json").write_text(
        json.dumps(aggregate, indent=2) + "\n", encoding="utf-8")
    with (RESULTS / "aggregate_results.csv").open(
            "w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=aggregate[0].keys())
        writer.writeheader()
        writer.writerows(aggregate)


if __name__ == "__main__":
    main()
