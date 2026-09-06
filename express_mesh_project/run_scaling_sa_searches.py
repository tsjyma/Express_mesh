#!/usr/bin/env python3
"""Run the report-facing SA placement search for each scaling row.

The annealing schedule, curve objective, seed split, and guardrail are shared.
Only parameters that define the scaling row are changed.  This prevents a
topology optimized for the standard B32/4VC configuration from being reused
as if it were the SA result for a different hardware configuration.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "express_mesh_project"
SEARCH = PROJECT / "search_placement_advanced.py"
GREEDY = PROJECT / "generate_traffic_aware_greedy_v6.py"
CORRECTED = PROJECT / "results" / "20260903" / "scaling_corrected"
LENGTH_AWARE = PROJECT / "results" / "20260904" / "scaling_length_aware"
SOC_SCALING = PROJECT / "results" / "20260905" / "row8_v6"
RATES = tuple(round(index * 0.05, 2) for index in range(1, 17))


ROWS = {
    "row3": {
        "initial": CORRECTED / "n16_b256" / "uniform_random_aspl.json",
        "output": CORRECTED / "n16_b256_sa_row3_t64",
        "rates": (0.35, 0.40, 0.45),
        "dimension": 16,
        "wire_budget": 256,
        "min_wire_length": 6,
        "source_route_candidates": 16,
        "vcs_per_vnet": 8,
        "escape_timeout": 64,
        "iterations": 20,
        "restarts": 3,
        "traffic_seeds": (1,),
        "warmup_cycles": 2_000,
        "measurement_cycles": 8_000,
        "validation_top_n": 8,
        "validation_warmup_cycles": 10_000,
        "validation_measurement_cycles": 50_000,
        "save_finalists": 8,
        "retain_mesh_candidate": True,
        "continue_after_ni_watchdog": True,
    },
    "row4": {
        "initial": (LENGTH_AWARE / "n16_b256_d4_greedy" /
                    "uniform_random_aspl.json"),
        "output": LENGTH_AWARE / "n16_b256_sa_row4_d4",
        "rates": (0.35, 0.40, 0.45),
        "dimension": 16,
        "wire_budget": 256,
        "min_wire_length": 6,
        "source_route_candidates": 16,
        "vcs_per_vnet": 8,
        "escape_timeout": 64,
        "iterations": 20,
        "restarts": 3,
        "traffic_seeds": (1,),
        "warmup_cycles": 2_000,
        "measurement_cycles": 8_000,
        "validation_top_n": 8,
        "validation_warmup_cycles": 10_000,
        "validation_measurement_cycles": 50_000,
        "save_finalists": 8,
        "retain_mesh_candidate": True,
        "continue_after_ni_watchdog": True,
        "express_latency_mode": "length-aware",
        "express_wire_per_cycle": 4,
        "greedy_output": LENGTH_AWARE / "n16_b256_d4_greedy",
        "greedy_candidate_limit": 2048,
    },
    "row5": {
        "initial": (PROJECT / "results" / "20260831" / "placements" /
                    "b32" / "uniform_random_aspl.json"),
        "output": CORRECTED / "n8_b32_sa_row4_v8",
        "rates": RATES,
        "dimension": 8,
        "wire_budget": 32,
        "min_wire_length": 3,
        "source_route_candidates": 8,
        "vcs_per_vnet": 8,
        "escape_timeout": 32,
    },
    "row6": {
        "initial": (LENGTH_AWARE / "n8_b64_d2_greedy" /
                    "uniform_random_aspl.json"),
        "output": LENGTH_AWARE / "n8_b64_sa_row6_d2",
        "rates": RATES,
        "dimension": 8,
        "wire_budget": 64,
        "min_wire_length": 3,
        "source_route_candidates": 8,
        "vcs_per_vnet": 4,
        "escape_timeout": 32,
        "express_latency_mode": "length-aware",
        "express_wire_per_cycle": 2,
        "greedy_output": LENGTH_AWARE / "n8_b64_d2_greedy",
    },
    "row7": {
        "initial": CORRECTED / "n8_b64" / "uniform_random_aspl.json",
        "output": CORRECTED / "n8_b64_sa_row7_packet2_t64",
        "rates": RATES,
        "dimension": 8,
        "wire_budget": 64,
        "min_wire_length": 3,
        "source_route_candidates": 8,
        "vcs_per_vnet": 4,
        "escape_timeout": 64,
        "packet_flits": 2,
        "continue_after_ni_watchdog": True,
    },
    "row8": {
        "traffic": "soc_heterogeneous",
        # Every restart begins at the unmodified traffic-aware ASPL Greedy
        # topology.  No hand-repaired seed topology is admitted here.
        "initial": SOC_SCALING / "greedy" /
            "soc_heterogeneous_aspl.json",
        "output": PROJECT / "results" / "20260906" / "row8_t256",
        # The hottest endpoint is 3x the uniform-endpoint mean, so the
        # analytical configured-rate ceiling is 2/3.  These three 0.05-grid
        # points span moderate through pressure-heavy operation.
        "rates": (0.45, 0.50, 0.55),
        "dimension": 16,
        "wire_budget": 256,
        "min_wire_length": 6,
        "source_route_candidates": 16,
        "vcs_per_vnet": 8,
        "escape_timeout": 256,
        # Only discrete search-effort parameters differ from the common SA.
        "iterations": 20,
        # Four predetermined chains times four restarts preserve the original
        # total of 16 restarts while using all 24 cores during evaluation.
        "search_seeds": (42, 43, 44, 45),
        "restarts": 4,
        "reheat_period": 12,
        "initial_temperature": 0.025,
        "final_temperature": 0.0004,
        "latency_weight": 0.006,
        "worst_throughput_weight": 0.25,
        "proposal_pool": 8_192,
        "traffic_seeds": (1, 2),
        "warmup_cycles": 5_000,
        "measurement_cycles": 15_000,
        "validation_top_n": 4,
        # Keep report holdout seeds 5--8 untouched by topology selection.
        "validation_seeds": (3, 4, 9, 10, 11, 12, 13, 14, 15, 16),
        "validation_warmup_cycles": 20_000,
        "validation_measurement_cycles": 100_000,
        "max_curve_throughput_regression": 0.0025,
        "save_finalists": 4,
        "retain_mesh_candidate": True,
        "continue_after_ni_watchdog": True,
        "greedy_output": SOC_SCALING / "greedy",
        "greedy_candidate_limit": 2048,
    },
}


def command_for(row: str, workers: int, *, search_seed: int | None = None,
                output: Path | None = None) -> list[str]:
    config = ROWS[row]
    rates = config["rates"]
    traffic = config.get("traffic", "uniform_random")
    initial = config["initial"]
    initials = initial if isinstance(initial, (tuple, list)) else (initial,)
    command = [
        sys.executable, str(SEARCH),
        "--mode", "sa-reheat",
        "--initial", *(str(path) for path in initials),
        "--output-dir", str(output or config["output"]),
        "--traffics", traffic,
        "--rates", *(str(rate) for rate in rates),
        "--traffic-seeds", *(str(seed) for seed in config.get(
            "traffic_seeds", (1, 2))),
        "--seed", str(42 if search_seed is None else search_seed),
        "--iterations", str(config.get("iterations", 80)),
        "--restarts", str(config.get("restarts", 4)),
        "--reheat-period", str(config.get("reheat_period", 20)),
        "--initial-temperature", str(config.get(
            "initial_temperature", 0.025)),
        "--final-temperature", str(config.get("final_temperature", 0.0004)),
        "--warmup-cycles", str(config.get("warmup_cycles", 1_000)),
        "--measurement-cycles", str(config.get("measurement_cycles", 4_000)),
        "--workers", str(workers),
        "--latency-weight", str(config.get("latency_weight", 0.006)),
        "--worst-throughput-weight", str(config.get(
            "worst_throughput_weight", 0.25)),
        "--reservation-weight", "0.6",
        "--vc-pressure-weight", "1.0",
        "--express-info-mode", "distance-gossip",
        "--express-info-period", "1",
        "--express-info-delay", "1",
        "--express-info-bits", "4",
        "--express-reservation-mode", "registered",
        "--express-admission-fraction", "1.0",
        "--proposal-pool", str(config.get("proposal_pool", 768)),
        "--dimension", str(config["dimension"]),
        "--source-route-candidates", str(config["source_route_candidates"]),
        "--escape-timeout", str(config["escape_timeout"]),
        "--vcs-per-vnet", str(config["vcs_per_vnet"]),
        "--buffer-depth", str(config.get("buffer_depth", 1)),
        "--packet-flits", str(config.get("packet_flits", 1)),
        "--router-latency", str(config.get("router_latency", 1)),
        "--mesh-link-latency", str(config.get("mesh_link_latency", 1)),
        "--express-latency-mode", config.get("express_latency_mode", "topology"),
        "--express-wire-per-cycle", str(config.get(
            "express_wire_per_cycle", 4)),
        "--wire-budget", str(config["wire_budget"]),
        "--max-degree", "1",
        "--min-wire-length", str(config["min_wire_length"]),
        "--validation-top-n", str(config.get("validation_top_n", 10)),
        "--validation-seeds", *(str(seed) for seed in config.get(
            "validation_seeds", (3, 4))),
        "--validation-warmup-cycles", str(config.get(
            "validation_warmup_cycles", 5_000)),
        "--validation-measurement-cycles", str(config.get(
            "validation_measurement_cycles", 30_000)),
        "--incumbent-policy", "curve-guardrail",
        "--max-curve-throughput-regression", str(config.get(
            "max_curve_throughput_regression", 0.01)),
        "--save-finalists", str(config.get("save_finalists", 10)),
    ]
    if config.get("retain_mesh_candidate"):
        command.append("--retain-mesh-candidate")
    if config.get("continue_after_ni_watchdog"):
        command.append("--continue-after-ni-watchdog")
    return command


def prepare_greedy(config: dict, force: bool = False) -> None:
    output = config.get("greedy_output")
    if output is None:
        return
    configured_initial = config["initial"]
    initial = Path(configured_initial[0] if isinstance(
        configured_initial, (tuple, list)) else configured_initial)
    if initial.exists() and not force:
        return
    latency_model = config.get(
        "greedy_latency_model",
        "length-aware"
        if config.get("express_latency_mode") == "length-aware"
        else "ideal",
    )
    command = [
        sys.executable, str(GREEDY),
        "--traffics", config.get("traffic", "uniform_random"),
        "--objectives", "aspl",
        "--dimension", str(config["dimension"]),
        "--wire-budget", str(config["wire_budget"]),
        "--max-degree", "1",
        "--min-wire-length", str(config["min_wire_length"]),
        "--latency-model", latency_model,
        "--express-wire-per-cycle", str(config.get(
            "express_wire_per_cycle", 4)),
        "--candidate-limit", str(config.get("greedy_candidate_limit", 0)),
        "--output-dir", str(output),
    ]
    print(f"generate latency-aware Greedy: {initial}", flush=True)
    subprocess.run(command, check=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rows", nargs="+", choices=ROWS,
                        default=list(ROWS))
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--force-greedy", action="store_true")
    args = parser.parse_args()
    for row in args.rows:
        prepare_greedy(ROWS[row], args.force_greedy)
        config = ROWS[row]
        output = config["output"]
        search_seeds = config.get("search_seeds")
        jobs = [
            (seed, output / f"chain_s{seed}") for seed in search_seeds
        ] if search_seeds else [(None, output)]
        pending = []
        for search_seed, job_output in jobs:
            if args.resume and (job_output / "best.json").exists():
                print(f"skip completed {row}: {job_output / 'best.json'}",
                      flush=True)
                continue
            pending.append((search_seed, job_output))

        def run_job(job) -> None:
            search_seed, job_output = job
            print(f"run scaling SA for {row}: {job_output}", flush=True)
            subprocess.run(command_for(
                row, args.workers, search_seed=search_seed,
                output=job_output,
            ), check=True)

        # Multi-chain rows intentionally run their predetermined chains in
        # parallel.  ``workers`` remains the per-chain case parallelism.
        with ThreadPoolExecutor(max_workers=max(1, len(pending))) as executor:
            list(executor.map(run_job, pending))


if __name__ == "__main__":
    main()
