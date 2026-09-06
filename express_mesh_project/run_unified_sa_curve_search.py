#!/usr/bin/env python3
"""Run the single, report-facing SA placement algorithm for every traffic.

All workloads use exactly the same SA-reheat schedule, initial-state rule,
injection-rate sweep, simulation budget, and seed split.  Only the matched
traffic demand changes, which is the intended traffic-aware input.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from express_mesh_project.model import ExpressEdge
from express_mesh_project.search_placement_advanced import placement_record


PROJECT = ROOT / "express_mesh_project"
SEARCH = PROJECT / "search_placement_advanced.py"
DEFAULT_OUTPUT = PROJECT / "results" / "20260902" / "sa_curve_unified_v2"
BASE_TRAFFICS = (
    "uniform_random", "tornado", "bit_complement",
    "cutstress_bidirectional",
)
TRAFFICS = BASE_TRAFFICS + ("mixture",)
RATES = tuple(round(index * 0.05, 2) for index in range(1, 17))
# One percent is a practical-equivalence guardrail for the two-seed validation
# mean.  A 0.25% pointwise threshold was reasonable for a 16-point single-
# workload curve, but became a multiple-comparison veto for the 64-point
# mixture archive: every otherwise strong candidate failed at least one noisy
# point.  The same 1% rule is applied to every report-facing workload.
CURVE_REGRESSION_TOLERANCE = 0.01


def command_for(traffic: str, output: Path, workers: int,
                iterations: int) -> list[str]:
    placements = PROJECT / "results" / "20260831" / "placements"
    # Every restart begins at the matched ASPL Greedy placement.  Independent
    # SA trajectories provide diversification; weak random starts would spend
    # most of the fixed search budget merely climbing back toward Greedy.
    if traffic == "mixture":
        initials = [
            placements / "mixture_b32" / "network_mix4_aspl.json"
        ]
        # Treat mixture as a robust multi-workload objective: every workload
        # is evaluated at every point on the same report-facing rate curve.
        # This matches the cross-matrix macro-average, unlike optimizing ASPL
        # on one traffic matrix formed by superposing the four demands.
        scenario_traffics = [
            item for item in BASE_TRAFFICS for _rate in RATES
        ]
        scenario_rates = [rate for _item in BASE_TRAFFICS for rate in RATES]
    else:
        initials = [placements / "b32" / f"{traffic}_aspl.json"]
        scenario_traffics = [traffic]
        scenario_rates = list(RATES)
    return [
        sys.executable, str(SEARCH),
        "--mode", "sa-reheat",
        "--initial", *(str(path) for path in initials),
        "--output-dir", str(output / traffic),
        "--traffics", *scenario_traffics,
        "--rates", *(str(rate) for rate in scenario_rates),
        "--traffic-seeds", "1", "2",
        "--seed", "42",
        "--iterations", str(iterations),
        "--restarts", "4",
        "--reheat-period", "20",
        "--initial-temperature", "0.025",
        "--final-temperature", "0.0004",
        "--warmup-cycles", "1000",
        "--measurement-cycles", "4000",
        "--workers", str(workers),
        "--latency-weight", "0.006",
        "--worst-throughput-weight", "0.25",
        "--reservation-weight", "0.6",
        "--vc-pressure-weight", "1.0",
        "--express-info-mode", "distance-gossip",
        "--express-info-period", "1",
        "--express-info-delay", "1",
        "--express-info-bits", "4",
        "--express-reservation-mode", "registered",
        "--proposal-pool", "768",
        "--wire-budget", "32",
        "--max-degree", "1",
        "--min-wire-length", "3",
        "--validation-top-n", "10",
        "--validation-seeds", "3", "4",
        "--validation-warmup-cycles", "5000",
        "--validation-measurement-cycles", "30000",
        "--incumbent-policy", "curve-guardrail",
        "--max-curve-throughput-regression",
        str(CURVE_REGRESSION_TOLERANCE),
        "--save-finalists", "10",
    ]


def apply_curve_guardrail(path: Path) -> None:
    """Re-select a saved validation archive using the report-facing rule.

    This also makes older completed searches resumable after introducing the
    guardrail; a clean run already makes the same selection in the search
    program itself.
    """
    record = json.loads(path.read_text(encoding="utf-8"))
    metadata = record["search_metadata"]
    archive = metadata["validation_archive"]
    incumbent = next(
        item for item in archive if "incumbent" in item["origins"]
    )["validation_result"]

    def passes(item) -> bool:
        points = item["validation_result"]["by_traffic"]
        baseline = incumbent["by_traffic"]
        return len(points) == len(baseline) and all(
            cand["traffic"] == base["traffic"]
            and cand["rate"] == base["rate"]
            and cand["accepted_throughput"] >= base["accepted_throughput"]
            * (1.0 - CURVE_REGRESSION_TOLERANCE)
            for cand, base in zip(points, baseline)
        )

    eligible = [item for item in archive if passes(item)]
    selected = max(
        eligible,
        key=lambda item: item["validation_result"]["utility"],
    )
    for item in archive:
        item["selected"] = item is selected
        item["passes_curve_throughput_guardrail"] = passes(item)
    metadata["selected_result"] = selected["validation_result"]
    metadata["selection_stage"] = "independent_validation_curve_guardrail"
    metadata["incumbent_policy"] = "curve-guardrail"
    metadata["max_curve_throughput_regression"] = (
        CURVE_REGRESSION_TOLERANCE
    )
    edges = [ExpressEdge(*values) for values in selected["placement_key"]]
    guarded = placement_record(
        "unified_sa_reheat_curve_guardrail", edges, metadata,
        wire_budget=32, max_degree=1, min_wire_length=3,
    )
    path.write_text(
        json.dumps(guarded, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    search_path = path.with_name("search.json")
    if search_path.exists():
        search_record = json.loads(search_path.read_text(encoding="utf-8"))
        search_record["config"]["incumbent_policy"] = "curve-guardrail"
        search_record["config"]["max_curve_throughput_regression"] = (
            CURVE_REGRESSION_TOLERANCE
        )
        search_record["metadata"] = metadata
        search_path.write_text(
            json.dumps(search_record, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--traffics", nargs="+", choices=TRAFFICS,
                        default=list(TRAFFICS))
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--iterations", type=int, default=80)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    if args.workers < 1 or args.iterations < 1:
        parser.error("--workers and --iterations must be positive")

    for traffic in args.traffics:
        output = args.output_root / traffic
        if args.resume and (output / "best.json").exists():
            apply_curve_guardrail(output / "best.json")
            print(f"skip completed {traffic}: {output / 'best.json'}")
            continue
        command = command_for(traffic, args.output_root, args.workers,
                              args.iterations)
        print(f"run unified SA for {traffic}", flush=True)
        subprocess.run(command, check=True)
        apply_curve_guardrail(output / "best.json")


if __name__ == "__main__":
    main()
