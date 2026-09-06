#!/usr/bin/env python3
"""Reproduce the formal V8 Garnet cases in standalone and report deltas."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import csv
import json
from pathlib import Path
import statistics
import subprocess


ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "express_mesh_project"
BINARY = PROJECT / "standalone_noc" / "express_noc"
DEFAULT_OUTPUT = PROJECT / "results" / "20260831" / "garnet_calibration"
FORMAL = (
    ("v8_b64_rate08", "uniform_random",
     PROJECT / "results" / "garnet_v8_physical_info" /
     "formal_uniform" / "results.json", {"mesh", "greedy", "sa"}, {1, 2, 3}),
    ("v8_b64_rate08", "tornado",
     PROJECT / "results" / "garnet_v8_physical_info" /
     "formal_tornado" / "results.json", {"mesh", "greedy", "sa"}, {1, 2, 3}),
    # This archived Garnet sweep uses the B32 ASPL placement and gives a
    # calibration point directly adjacent to the new B32 experiment matrix.
    ("v8_b32_rate07", "uniform_random",
     PROJECT / "results" / "budget32_v8" /
     "garnet_starvation_sweep" / "results.json", {"greedy"}, None),
)


def command(record, path, binary=BINARY):
    return [
        str(binary), "--topology-file", record["topology_file"],
        "--topology", record["topology"], "--routing", "adaptive",
        "--traffic", record["traffic"], "--rate",
        str(record["configured_injection_rate"]),
        "--seed", str(record["seed"]), "--warmup-cycles",
        str(record["warmup_cycles"]), "--measurement-cycles",
        str(record["measurement_cycles"]), "--source-route",
        "--source-route-policy", "4", "--source-route-candidates", "8",
        "--source-mesh-routing", "xy", "--reservation-weight",
        str(record.get("reservation_weight", 0.6)),
        "--express-vc-weight", str(record.get("express_vc_weight", 1.0)),
        "--express-info-mode", record.get("express_info_mode", "distance-gossip"),
        "--express-info-period", str(record.get("express_info_period", 1)),
        "--express-info-delay", str(record.get("express_info_delay", 1)),
        "--express-info-bits", str(record.get("express_info_bits", 4)),
        "--express-reservation-mode",
        record.get("express_reservation_mode", "registered"),
        "--express-admission-fraction",
        str(record.get("express_admission_fraction", 1.0)),
        "--escape-timeout", "32", "--express-wire-budget",
        str(record.get("express_budget", 64)), "--express-max-degree", "1",
        "--express-min-wire-length", "3", "--output", str(path),
    ]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--binary", type=Path, default=BINARY)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    run_dir = args.output_dir / "runs"; run_dir.mkdir(parents=True, exist_ok=True)
    garnet = []
    for calibration_set, traffic, path, topologies, seeds in FORMAL:
        records = json.loads(path.read_text(encoding="utf-8"))
        for original in records:
            if (original["traffic"] != traffic or
                    original["topology"] not in topologies or
                    (seeds is not None and original["seed"] not in seeds)):
                continue
            record = dict(original)
            record["calibration_set"] = calibration_set
            garnet.append(record)

    def run(record):
        path = run_dir / (f"{record['calibration_set']}_{record['traffic']}_"
                          f"{record['topology']}_s{record['seed']}.json")
        if not (args.resume and path.exists()):
            completed = subprocess.run(command(record, path, args.binary), text=True,
                                       stdout=subprocess.PIPE,
                                       stderr=subprocess.STDOUT)
            if completed.returncode:
                raise RuntimeError(completed.stdout)
        result = json.loads(path.read_text(encoding="utf-8"))
        result["calibration_set"] = record["calibration_set"]
        return result

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        standalone = list(executor.map(run, garnet))
    groups = {}
    for engine, records in (("garnet", garnet), ("standalone", standalone)):
        for record in records:
            groups.setdefault((record["calibration_set"], record["traffic"],
                               record["topology"]), {}) \
                  .setdefault(engine, []).append(record)
    summary = []
    for (calibration_set, traffic, topology), engines in sorted(groups.items()):
        item = {"calibration_set": calibration_set,
                "traffic": traffic, "topology": topology,
                "sample_count": len(engines["garnet"])}
        for metric in ("accepted_throughput", "average_packet_latency_cycles"):
            g = statistics.fmean(float(row[metric]) for row in engines["garnet"])
            s = statistics.fmean(float(row[metric]) for row in engines["standalone"])
            short = "throughput" if metric.startswith("accepted") else "latency"
            item[f"garnet_{short}"] = g; item[f"standalone_{short}"] = s
            item[f"{short}_delta_percent"] = 100 * (s - g) / g
            item[f"{short}_within_3_percent"] = abs(item[f"{short}_delta_percent"]) <= 3
        summary.append(item)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    with (args.output_dir / "summary.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(summary[0]))
        writer.writeheader(); writer.writerows(summary)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
