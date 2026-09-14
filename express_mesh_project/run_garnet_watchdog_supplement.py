#!/usr/bin/env python3
"""Rerun non-proven-deadlock Garnet watchdog cases with a diagnostic threshold.

This is a supplement to the full paper suite, not a replacement for its
completed measurements.  The 16x16 Random cohort is fixed to topology seeds
1--5.  All ten length-aware Random samples are rerun because the original
topology JSONs had incorrect unit express-link latency.
"""

from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import asdict, replace
import json
from pathlib import Path
import shutil
import sys
import time

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import express_mesh_project.run_20260831_standalone_suite as suite
import express_mesh_project.run_garnet_full_paper_suite as runner
import express_mesh_project.run_phase3_measurement_v2 as garnet


DEFAULT_OUTPUT = (runner.PROJECT / "results" /
                  "garnet_watchdog_supplement_20260914")

# Exact 4.5 watchdog cases in the downloaded full-paper archive.  The
# controlled two-VC escape-off cycle (20/20 known deadlocks) is excluded.
ESCAPE_SEEDS = {
    8: {
        .55: (15,),
        .60: (1, 2, 3, 5, 6, 8, 9, 11, 12, 14, 15, 16, 17, 18, 19, 20),
        .65: (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 13, 14, 16, 17, 18, 19, 20),
        .70: (1, 2, 3, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 16, 18),
        .75: (1, 2, 3, 4, 5, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18),
        .80: (1, 2, 4, 5, 6, 7, 8, 9, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20),
    },
    16: {
        .60: (2, 3, 9, 10, 13, 16, 19, 20),
        .65: (3, 4, 5, 11, 12, 13, 15, 16, 17, 19),
        .70: (1, 7, 8, 9, 12, 13, 16, 17),
        .75: (1, 5, 6, 7, 10, 11, 13, 14, 15, 17),
        .80: (4, 6, 9, 14, 17),
    },
}


def select_case(case: suite.Case) -> bool:
    rate = round(case.rate, 2)
    if case.section == "escape":
        timeout = int(case.topology_label.removeprefix("on_t"))
        return case.seed in ESCAPE_SEEDS.get(timeout, {}).get(rate, ())
    if case.section == "escape_off":
        return (case.traffic == "uniform_random" and
                (rate, case.seed) in {(.75, 15), (.80, 10), (.80, 13)})
    if case.section == "routing_ablation":
        return (case.topology_label == "greedy:local_mesh_adaptive" and
                rate == .80 and case.seed in (6, 7))
    if case.section == "scaling":
        if case.topology_label == "row8:mesh":
            return case.seed == 18
        for row in (3, 4, 8):
            for topology_seed in range(1, 6):
                if case.topology_label == f"row{row}:random_p{topology_seed}":
                    return case.seed in (5, 6)
    return False


def selected_cases(output: Path, threshold: int) -> list[suite.Case]:
    catalog = suite.topology_catalog(output)
    candidates = (
        suite.routing_cases(catalog) +
        suite.escape_cases(catalog) +
        suite.scaling_cases(output)
    )
    chosen = []
    for case in candidates:
        if not select_case(case):
            continue
        overrides = dict(case.overrides)
        overrides["deadlock_threshold"] = threshold
        chosen.append(replace(case, overrides=overrides))
    counts = Counter(case.section for case in chosen)
    expected = {
        "escape": 125, "escape_off": 3,
        "routing_ablation": 2, "scaling": 31,
    }
    if counts != expected:
        raise RuntimeError(f"selection mismatch: {counts} != {expected}")
    return chosen


def run(args: argparse.Namespace) -> None:
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    cache_dir = output / "case_cache"
    cache_dir.mkdir(exist_ok=True)
    (output / "failure_logs").mkdir(exist_ok=True)
    cases = selected_cases(output, args.deadlock_threshold)
    executions = runner.build_executions(
        cases, {case.section for case in cases}, output)
    if len(executions) != len(cases):
        raise RuntimeError(
            f"expected {len(cases)} distinct runs, got {len(executions)}")
    cached = {
        execution.key: json.loads((cache_dir / f"{execution.key}.json").read_text())
        for execution in executions
        if (cache_dir / f"{execution.key}.json").is_file()
    }
    pending = [execution for execution in executions
               if execution.key not in cached]
    if args.max_executions is not None:
        pending = pending[:args.max_executions]
    manifest = {
        "created_by": Path(__file__).name,
        "deadlock_threshold": args.deadlock_threshold,
        "workers": args.workers,
        "build_jobs": args.build_jobs,
        "logical_cases": len(cases),
        "unique_executions": len(executions),
        "cases_by_section": dict(Counter(c.section for c in cases)),
        "random_16x16_topology_seeds": [1, 2, 3, 4, 5],
        "random_16x16_traffic_seeds": [5, 6],
        "known_two_vc_deadlock_contrast_excluded": True,
        "cached_executions": len(cached),
        "pending_executions": len(pending),
        "supplement_note": (
            "4.5 reruns only former watchdog samples; 4.6 reruns the complete "
            "five-topology Random cohort for each 16x16 row, plus SoC Mesh "
            "seed 18. A completed run at this larger threshold is a fixed-"
            "window diagnostic, not proof of steady-state throughput."
        ),
    }
    runner.atomic_json(output / "manifest.json", manifest)
    print(json.dumps(manifest, indent=2), flush=True)
    if args.plan_only:
        return

    if not args.skip_build:
        runner.build_garnet(args.build_jobs)
    garnet.RESULTS = output / "gem5"
    rows = list(cached.values())
    failures: list[dict] = []
    start = time.time()
    waiting = iter(pending)
    active = {}
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        while True:
            while len(active) < args.workers:
                execution = next(waiting, None)
                if execution is None:
                    break
                future = pool.submit(runner.run_execution, execution, output,
                                     args.keep_raw)
                active[future] = execution
            if not active:
                break
            done, _ = wait(active, return_when=FIRST_COMPLETED)
            for future in done:
                execution = active.pop(future)
                try:
                    row = future.result()
                    rows.append(row)
                    runner.atomic_json(
                        cache_dir / f"{execution.key}.json", row)
                except Exception as error:
                    failures.append({
                        "execution_key": execution.key,
                        "case": asdict(execution.case),
                        "error": repr(error),
                    })
                    run_dirs = sorted((output / "gem5" / "runs").glob(
                        f"paper_{execution.key}*"))
                    if run_dirs and (run_dirs[-1] / "run.log").is_file():
                        shutil.copy2(
                            run_dirs[-1] / "run.log",
                            output / "failure_logs" / f"{execution.key}.log")
                print(
                    f"[{len(rows)}/{len(executions)}] "
                    f"remaining={len(executions)-len(rows)-len(failures)} "
                    f"failures={len(failures)}", flush=True)
            if (len(rows) + len(failures)) % 8 < len(done):
                runner.write_progress(
                    output, executions, rows, failures, start)

    runner.snapshot(output, executions, rows, failures, start)
    bundle = runner.make_bundle(output)
    supplement_bundle = output / "garnet_watchdog_supplement_results.tar.gz"
    bundle.replace(supplement_bundle)
    print(f"Completed {len(rows)}/{len(executions)} executions; "
          f"{len(failures)} command failures.", flush=True)
    print(f"Copy this archive back: {supplement_bundle}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--build-jobs", type=int, default=2)
    parser.add_argument("--deadlock-threshold", type=int, default=200_000)
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--plan-only", action="store_true")
    parser.add_argument("--keep-raw", action="store_true")
    parser.add_argument("--max-executions", type=int,
                        help="smoke-test limit; omit for the complete supplement")
    args = parser.parse_args()
    if args.workers < 1 or args.build_jobs < 1:
        parser.error("workers and build-jobs must be positive")
    if args.deadlock_threshold <= 140_000:
        parser.error("threshold must exceed the longest 140k-cycle run")
    if args.max_executions is not None and args.max_executions < 1:
        parser.error("max-executions must be positive")
    run(args)


if __name__ == "__main__":
    main()
