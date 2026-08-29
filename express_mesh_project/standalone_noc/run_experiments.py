#!/usr/bin/env python3
"""Fast Phase-3 matrix runner for the standalone C++ NoC model."""

from __future__ import annotations

import argparse
import csv
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from pathlib import Path
import subprocess


HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent
BINARY = HERE / "express_noc"
DEFAULT_RESULTS = HERE / "results"
TOPOLOGIES = {
    "mesh": "mesh.json",
    "random": "random.json",
    "aspl": "aspl.json",
    "bottleneck": "bottleneck.json",
    "handcrafted": "handcrafted.json",
    "hybrid": "hybrid.json",
    "hybrid_cutstress": "hybrid_cutstress.json",
    "robust": "robust.json",
    "axis_aspl": "axis_aspl.json",
    "axis_hybrid": "axis_hybrid.json",
    "axis_robust": "axis_robust.json",
    "stride_aspl": "stride_aspl.json",
    "stride_hybrid": "stride_hybrid.json",
    "stride_robust": "stride_robust.json",
    "axis_random": "axis_random.json",
    "stride_random": "stride_random.json",
    "sa_best": "best.json",
    "bitcomp_aspl": "bitcomp_aspl.json",
    "bitcomp_hybrid": "bitcomp_hybrid.json",
    "tornado_aspl": "tornado_aspl.json",
    "tornado_hybrid": "tornado_hybrid.json",
    "bitcomp_direct": "bitcomp_direct.json",
    "tornado_direct": "tornado_direct.json",
}


def tag(spec, source_route, policy, no_escape, placement_seed):
    topology, routing, traffic, rate, seed = spec
    mode = "_source_route" if source_route else ""
    if source_route and policy:
        mode += {1: "_q", 2: "_qr", 3: "_random_candidate",
                 4: "_pressure", 5: "_dijkstra_express",
                 6: "_dijkstra_global"}[policy]
    if no_escape:
        mode += "_no_escape"
    placement = (f"_p{placement_seed}"
                 if topology in {"random", "axis_random", "stride_random"}
                 else "")
    return f"{topology}_{routing}_{traffic}_r{rate:.3f}_s{seed}{placement}{mode}"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--warmup-cycles", type=int, default=20_000)
    parser.add_argument("--measurement-cycles", type=int, default=100_000)
    parser.add_argument("--garnet-deadlock-threshold", type=int, default=50_000)
    parser.add_argument("--express-adaptive-threshold", type=float, default=0.25)
    parser.add_argument("--express-adaptive-lambda", type=float, default=1.0)
    parser.add_argument("--express-detour-ratio", type=float, default=1.5)
    parser.add_argument("--express-escape-timeout", type=int, default=32)
    parser.add_argument("--source-mesh-routing", choices=["adaptive", "dor_adaptive", "monotonic_xy", "odd_even", "phase_xy", "west_first", "xy"],
                        default="adaptive")
    parser.add_argument("--rates", type=float, nargs="+", default=[0.02, 0.05, 0.08, 0.12, 0.16])
    parser.add_argument("--seeds", type=int, nargs="+", default=[1, 2, 3])
    parser.add_argument("--topologies", nargs="+", choices=TOPOLOGIES, default=list(TOPOLOGIES))
    parser.add_argument("--routings", nargs="+", choices=["deterministic", "adaptive"], default=["deterministic", "adaptive"])
    parser.add_argument("--traffics", nargs="+", choices=["uniform_random", "cutstress", "hotspot", "bit_complement", "tornado"], default=["uniform_random", "cutstress", "hotspot"])
    parser.add_argument("--source-route", action="store_true")
    parser.add_argument("--source-route-policy", type=int, choices=range(7), default=0)
    parser.add_argument("--source-route-candidates", type=int, default=8)
    parser.add_argument("--reservation-weight", type=float, default=0.5)
    parser.add_argument("--express-vc-weight", type=float, default=1.0)
    parser.add_argument("--express-waiter-weight", type=float, default=0.0)
    parser.add_argument("--no-escape", action="store_true")
    parser.add_argument("--correct-no-escape-vcs", action="store_true")
    parser.add_argument("--continue-after-ni-watchdog", action="store_true")
    parser.add_argument("--random-placement-seed", type=int, default=1)
    parser.add_argument(
        "--topology-dir", type=Path,
        help=("Directory containing mesh.json/random.json/aspl.json/etc. "
              "Defaults to the original phase1 placement directory."),
    )
    parser.add_argument("--express-wire-budget", type=int, default=16)
    parser.add_argument("--express-max-degree", type=int, default=1)
    parser.add_argument("--express-min-wire-length", type=int, default=3)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    args = parser.parse_args()

    if not BINARY.exists():
        subprocess.run(["make", "-C", str(HERE)], check=True)
    args.results.mkdir(parents=True, exist_ok=True)
    run_root = args.results / "runs"
    run_root.mkdir(exist_ok=True)

    specs = [(t, r, f, rate, seed) for t in args.topologies
             for r in args.routings for f in args.traffics
             for rate in args.rates for seed in args.seeds]

    def run_one(spec):
        topology, routing, traffic, rate, seed = spec
        run_dir = run_root / tag(spec, args.source_route,
                                 args.source_route_policy, args.no_escape,
                                 args.random_placement_seed)
        run_dir.mkdir(parents=True, exist_ok=True)
        result_path = run_dir / "result.json"
        if args.resume and result_path.exists():
            row = json.loads(result_path.read_text())
            expected = {
                "warmup_cycles": args.warmup_cycles,
                "measurement_cycles": args.measurement_cycles,
                "garnet_deadlock_threshold": args.garnet_deadlock_threshold,
                "express_adaptive_threshold": args.express_adaptive_threshold,
                "express_adaptive_lambda": args.express_adaptive_lambda,
                "express_detour_ratio": args.express_detour_ratio,
                "express_escape_timeout": args.express_escape_timeout,
                "legacy_no_escape_vc_bug": not args.correct_no_escape_vcs,
                "continue_after_ni_watchdog": args.continue_after_ni_watchdog,
                "source_mesh_routing": args.source_mesh_routing,
                "source_route_candidates": args.source_route_candidates,
                "reservation_weight": args.reservation_weight,
                "express_vc_weight": args.express_vc_weight,
                "express_waiter_weight": args.express_waiter_weight,
                "express_wire_budget": args.express_wire_budget,
                "express_max_degree": args.express_max_degree,
                "express_min_wire_length": args.express_min_wire_length,
            }
            if all(row.get(key) == value for key, value in expected.items()):
                row["run_dir"] = str(run_dir)
                return row
        placement_dir = args.topology_dir
        if placement_dir is None:
            placement_dir = (PROJECT / "results" /
                             (f"phase1_random_seed{args.random_placement_seed}"
                              if topology == "random" and args.random_placement_seed != 1
                              else "phase1"))
        command = [
            str(BINARY), "--topology-file", str(placement_dir / TOPOLOGIES[topology]),
            "--topology", topology, "--routing", routing,
            "--traffic", traffic, "--rate", str(rate), "--seed", str(seed),
            "--warmup-cycles", str(args.warmup_cycles),
            "--measurement-cycles", str(args.measurement_cycles),
            "--deadlock-threshold", str(args.garnet_deadlock_threshold),
            "--adaptive-threshold", str(args.express_adaptive_threshold),
            "--adaptive-lambda", str(args.express_adaptive_lambda),
            "--detour-ratio", str(args.express_detour_ratio),
            "--escape-timeout", str(args.express_escape_timeout),
            "--source-mesh-routing", args.source_mesh_routing,
            "--source-route-policy", str(args.source_route_policy),
            "--source-route-candidates", str(args.source_route_candidates),
            "--reservation-weight", str(args.reservation_weight),
            "--express-vc-weight", str(args.express_vc_weight),
            "--express-waiter-weight", str(args.express_waiter_weight),
            "--random-placement-seed", str(args.random_placement_seed),
            "--express-wire-budget", str(args.express_wire_budget),
            "--express-max-degree", str(args.express_max_degree),
            "--express-min-wire-length", str(args.express_min_wire_length),
            "--output", str(result_path),
        ]
        if args.source_route:
            command.append("--source-route")
        if args.no_escape:
            command.append("--no-escape")
        if args.correct_no_escape_vcs:
            command.append("--correct-no-escape-vcs")
        if args.continue_after_ni_watchdog:
            command.append("--continue-after-ni-watchdog")
        completed = subprocess.run(command, text=True, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, check=False)
        (run_dir / "run.log").write_text(completed.stdout)
        if completed.returncode:
            raise RuntimeError(f"{run_dir.name}: {completed.stdout.strip()}")
        row = json.loads(result_path.read_text())
        row["run_dir"] = str(run_dir)
        return row

    rows, failures = [], []
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(run_one, spec): spec for spec in specs}
        for index, future in enumerate(as_completed(futures), 1):
            try:
                row = future.result()
                rows.append(row)
                print(f"[{index}/{len(specs)}] {row['run_dir']}", flush=True)
            except Exception as error:
                spec = futures[future]
                failures.append({"spec": spec, "error": str(error)})
                print(f"[FAILED] {spec}: {error}", flush=True)

    rows.sort(key=lambda r: (r["traffic"], r["topology"], r["routing"],
                             r["configured_injection_rate"], r["seed"]))
    (args.results / "main_results.json").write_text(json.dumps(rows, indent=2) + "\n")
    (args.results / "failed_results.json").write_text(json.dumps(failures, indent=2) + "\n")
    if rows:
        with (args.results / "main_results.csv").open("w", newline="") as output:
            writer = csv.DictWriter(output, fieldnames=rows[0].keys())
            writer.writeheader()
            for row in rows:
                writer.writerow({k: json.dumps(v) if isinstance(v, list) else v
                                 for k, v in row.items()})


if __name__ == "__main__":
    main()
