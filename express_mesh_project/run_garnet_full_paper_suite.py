#!/usr/bin/env python3
"""Build Garnet and reproduce every simulated paper result except Tables 3--5.

The experiment matrix is imported from ``run_20260831_standalone_suite.py``;
that file remains the single source of truth for placements, loads, seeds, and
ablation settings.  This runner translates each logical case to Garnet,
deduplicates behavior-identical executions, writes an atomic cache after every
run, and creates a compact archive that can be copied back from a cluster.

Tables 3--5 are intentionally not rerun.  Their checked-in Garnet results are
imported whenever the same execution is also needed by a figure or table.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from dataclasses import asdict, dataclass, field
import csv
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import statistics
import subprocess
import sys
import tarfile
import time
from typing import Iterable

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import express_mesh_project.run_20260831_standalone_suite as suite
import express_mesh_project.run_phase3_measurement_v2 as garnet


ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "express_mesh_project"
DEFAULT_OUTPUT = PROJECT / "results" / "garnet_full_paper_suite"
TABLE_3_5_RESULTS = (PROJECT / "results" / "20260906" /
                     "garnet_main_validation" / "results.json")
SECTIONS = (
    "main", "random_distribution", "cross", "routing_ablation",
    "information_ablation", "escape", "escape_off",
    "escape_deadlock_contrast", "scaling",
)
SCHEMA = 2
# Measured locally for a standard 8x8 20k+100k Garnet execution.  The runner
# replaces this estimate with observed hostSeconds in progress.json.
DEFAULT_SECONDS_8X8 = 65.0


def atomic_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n",
                         encoding="utf-8")
    temporary.replace(path)


@lru_cache(maxsize=None)
def topology_behavior(path: Path) -> dict:
    record = json.loads(path.read_text(encoding="utf-8"))
    constraints = record.get("constraints", {})
    return {
        "name": record.get("name", path.stem),
        "dimension": record["dimension"],
        "node_count": record["node_count"],
        "latency_model": record.get("latency_model", "topology"),
        "constraints": {
            "wire_cost": constraints["wire_cost"],
            "max_degree": constraints["max_degree"],
        },
        "express_links": record.get("express_links", []),
    }


def materialize_topology(path: Path, output: Path) -> tuple[Path, str]:
    behavior = topology_behavior(path)
    encoded = json.dumps(behavior, sort_keys=True,
                         separators=(",", ":")).encode()
    digest = hashlib.sha256(encoded).hexdigest()
    compact = output / "topology_inputs" / f"{digest}.json"
    if not compact.exists():
        atomic_json(compact, behavior)
    return compact.resolve(), digest


def effective_options(case: suite.Case) -> dict:
    options = suite.standard_overrides(**case.overrides)
    options.setdefault("source_route", True)
    options.setdefault("no_escape", False)
    options.setdefault("retain_mesh_candidate", False)
    options.setdefault("drain_cycles", 0)
    options.setdefault("deadlock_threshold", 50_000)
    # These switches only affect standalone watchdog bookkeeping.  Garnet
    # already gives all VCs to adaptive routing when escape is disabled and a
    # panic is recorded as a failed/completed deadlock sample by this runner.
    options.pop("correct_no_escape_vcs", None)
    options.pop("continue_after_ni_watchdog", None)
    return options


def runtime_options(case: suite.Case) -> dict:
    options = effective_options(case)
    path = Path(case.topology_file).resolve()
    options["dimension"] = int(topology_behavior(path)["dimension"])
    # Placement JSON already contains each express edge's latency.  Retain
    # these fields as experiment metadata, but do not pass them to Garnet.
    options.setdefault("express_latency_mode", "topology")
    options.setdefault("express_wire_per_cycle", 4)
    return options


@dataclass
class Execution:
    case: suite.Case
    topology_file: Path
    topology_sha256: str
    options: dict
    uses: list[dict] = field(default_factory=list)

    def identity(self) -> dict:
        simulated = dict(self.options)
        simulated.pop("express_latency_mode", None)
        simulated.pop("express_wire_per_cycle", None)
        return {
            "schema": SCHEMA,
            "topology_sha256": self.topology_sha256,
            "traffic": self.case.traffic,
            "rate": self.case.rate,
            "seed": self.case.seed,
            "warmup": self.case.warmup,
            "measurement": self.case.measurement,
            "options": simulated,
        }

    @property
    def key(self) -> str:
        payload = json.dumps(self.identity(), sort_keys=True,
                             separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()[:24]


def all_cases(catalog: dict, output: Path) -> list[suite.Case]:
    cases = []
    cases.extend(suite.main_cases(catalog))
    cases.extend(suite.random_distribution_cases(catalog))
    cases.extend(suite.cross_cases(catalog, rate=0.65))
    cases.extend(suite.cross_cases(catalog, rate=0.80))
    cases.extend(suite.routing_cases(catalog))
    cases.extend(suite.information_cases(catalog))
    cases.extend(suite.escape_cases(catalog))
    cases.extend(suite.escape_deadlock_contrast_cases(catalog))
    cases.extend(suite.scaling_cases(output))
    return cases


def build_executions(cases: Iterable[suite.Case], sections: set[str],
                     output: Path) -> list[Execution]:
    unique: dict[str, Execution] = {}
    for case in cases:
        if case.section not in sections:
            continue
        original = Path(case.topology_file).resolve()
        topology, behavior_hash = materialize_topology(original, output)
        execution = Execution(case, topology, behavior_hash,
                              runtime_options(case))
        use = {
            "section": case.section,
            "topology_label": case.topology_label,
            "topology_class": case.topology_class,
            "original_topology_file": case.topology_file,
            "logical_case_key": case.key(),
        }
        if execution.key not in unique:
            unique[execution.key] = execution
        unique[execution.key].uses.append(use)
    return sorted(unique.values(), key=lambda item: (
        item.case.section, item.case.traffic, item.case.rate,
        item.case.topology_label, item.case.seed,
    ))


def safe_label(text: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in text)


def directed_endpoints(dimension: int, topology: dict) -> list[tuple[int, int, int]]:
    endpoints = []
    for row in range(dimension):
        for col in range(dimension - 1):
            west = col + row * dimension
            endpoints.extend(((west, west + 1, -1), (west + 1, west, -1)))
    for col in range(dimension):
        for row in range(dimension - 1):
            south = col + row * dimension
            endpoints.extend(((south, south + dimension, -1),
                              (south + dimension, south, -1)))
    for index, edge in enumerate(topology.get("express_links", [])):
        u, v = int(edge["u"]), int(edge["v"])
        endpoints.extend(((u, v, index), (v, u, index)))
    return sorted(endpoints, key=lambda edge: (edge[0], edge[1]))


def attach_link_telemetry(row: dict, topology_file: Path) -> None:
    if "directed_link_utilization" in row:
        return
    stats_path = Path(row["run_dir"]) / "stats.txt"
    if not stats_path.exists():
        return
    stats = stats_path.read_text(encoding="utf-8")
    host_match = re.search(r"^hostSeconds\s+(\S+)", stats, re.M)
    if host_match:
        row["host_seconds"] = float(host_match.group(1))
    try:
        values = garnet.stat_values(
            stats, "system.ruby.network.express_mesh_int_link_utilization")
    except ValueError:
        # A panic can precede gem5's final statistics dump.  The deadlock
        # state parsed from run.log remains a valid result without this vector.
        return
    topology = json.loads(topology_file.read_text(encoding="utf-8"))
    endpoints = directed_endpoints(int(topology["dimension"]), topology)
    if len(values) != len(endpoints):
        raise RuntimeError(f"link vector has {len(values)} values but topology "
                           f"has {len(endpoints)} directed links")
    row["directed_link_sources"] = [item[0] for item in endpoints]
    row["directed_link_destinations"] = [item[1] for item in endpoints]
    row["directed_link_express_ids"] = [item[2] for item in endpoints]
    row["directed_link_utilization"] = values


def compact_run_dir(run_dir: Path, allowed_root: Path) -> None:
    resolved, root = run_dir.resolve(), allowed_root.resolve()
    if root not in resolved.parents:
        raise RuntimeError(f"refusing to compact path outside {root}: {resolved}")
    fs = resolved / "fs"
    if fs.is_dir():
        shutil.rmtree(fs)
    for name in ("config.ini", "config.json", "stats.txt"):
        path = resolved / name
        if path.is_file():
            path.unlink()


def run_execution(execution: Execution, output: Path, keep_raw: bool) -> dict:
    case, options = execution.case, execution.options
    label = f"paper_{execution.key}"
    garnet.TOPOLOGIES[label] = execution.topology_file
    spec = (label, "committed", case.traffic, case.rate, case.seed)
    policy = int(options["source_route_policy"])
    row = garnet.run_one(
        spec, case.warmup, case.measurement,
        int(options["deadlock_threshold"]),
        source_route=bool(options["source_route"]),
        source_route_policy=policy,
        no_escape=bool(options["no_escape"]), topology_dir=Path("."),
        reservation_weight=float(options["reservation_weight"]),
        vc_pressure_weight=float(options["vc_pressure_weight"]),
        express_budget=int(options["wire_budget"]),
        express_max_degree=int(options["max_degree"]),
        express_min_wire_length=int(options["min_wire_length"]),
        express_info_mode=str(options["express_info_mode"]),
        express_info_period=int(options["express_info_period"]),
        express_info_delay=int(options["express_info_delay"]),
        express_info_bits=int(options["express_info_bits"]),
        express_admission_fraction=float(options["express_admission_fraction"]),
        express_reservation_mode=str(options["express_reservation_mode"]),
        dimension=int(options["dimension"]),
        source_route_candidates=int(options["source_route_candidates"]),
        router_latency=int(options["router_latency"]),
        mesh_link_latency=int(options["mesh_link_latency"]),
        vcs_per_vnet=int(options["vcs_per_vnet"]),
        buffers_per_data_vc=int(options["buffer_depth"]),
        buffers_per_ctrl_vc=int(options["buffer_depth"]), inj_vnet=0,
        escape_timeout=int(options["escape_timeout"]),
        source_mesh_routing=str(options["source_mesh_routing"]),
        retain_mesh_candidate=bool(options["retain_mesh_candidate"]),
        packet_flits=int(options["packet_flits"]),
        drain_cycles=int(options["drain_cycles"]),
        route_cache_dir=output / "route_table_cache",
    )
    row["topology_file"] = str(execution.topology_file)
    attach_link_telemetry(row, execution.topology_file)
    row.update({
        "execution_key": execution.key,
        "topology_sha256": execution.topology_sha256,
        "uses": execution.uses,
        "paper_options": execution.options,
    })
    if not keep_raw:
        compact_run_dir(Path(row["run_dir"]), output / "gem5" / "runs")
    return row


def legacy_compatible(row: dict, execution: Execution) -> bool:
    case, options = execution.case, execution.options
    try:
        legacy_path = Path(row["topology_file"])
        if not legacy_path.exists():
            parts = legacy_path.parts
            marker = parts.index("express_mesh_project")
            legacy_path = ROOT.joinpath(*parts[marker:])
        return (
            row["traffic"] == case.traffic and
            float(row["configured_injection_rate"]) == case.rate and
            int(row["seed"]) == case.seed and
            int(row["warmup_cycles"]) == case.warmup and
            int(row["measurement_cycles"]) == case.measurement and
            hashlib.sha256(json.dumps(
                topology_behavior(legacy_path.resolve()),
                sort_keys=True, separators=(",", ":")
            ).encode()).hexdigest() == execution.topology_sha256 and
            row.get("source_route_policy") == options["source_route_policy"] and
            row.get("source_route_candidates") == options["source_route_candidates"] and
            row.get("source_mesh_routing", "xy") == options["source_mesh_routing"] and
            row.get("retain_mesh_candidate", False) == options["retain_mesh_candidate"] and
            row.get("reservation_weight") == options["reservation_weight"] and
            row.get("express_vc_weight") == options["vc_pressure_weight"] and
            row.get("express_info_mode") == options["express_info_mode"] and
            row.get("express_reservation_mode") == options["express_reservation_mode"] and
            row.get("vcs_per_vnet") == options["vcs_per_vnet"] and
            row.get("escape_timeout") == options["escape_timeout"] and
            row.get("escape_enabled") == (not options["no_escape"])
        )
    except (KeyError, OSError, TypeError, ValueError):
        return False


def import_tables_3_5(executions: Iterable[Execution], path: Path,
                      cache_dir: Path) -> int:
    if not path.exists():
        return 0
    source = json.loads(path.read_text(encoding="utf-8"))
    imported = 0
    for execution in executions:
        cache = cache_dir / f"{execution.key}.json"
        if cache.exists():
            continue
        matches = [row for row in source if legacy_compatible(row, execution)]
        if len(matches) != 1:
            continue
        row = dict(matches[0])
        # Figure 9 needs the full directed-link vector.  The checked-in table
        # rows contain only scalar link summaries because their original raw
        # stats are intentionally not versioned; rerun these four samples.
        figure_9_sample = (
            execution.case.traffic == "uniform_random" and
            execution.case.rate == 0.8 and execution.case.seed == 5 and
            any(use["section"] == "main" and
                (use["topology_label"] in {"mesh", "greedy", "sa"} or
                 use["topology_label"] == "random_p1")
                for use in execution.uses)
        )
        if figure_9_sample and "directed_link_utilization" not in row:
            continue
        attach_link_telemetry(row, execution.topology_file)
        row.update({
            "execution_key": execution.key,
            "topology_file": str(execution.topology_file),
            "topology_sha256": execution.topology_sha256,
            "uses": execution.uses,
            "paper_options": execution.options,
            "imported_from_tables_3_5": str(path.resolve()),
        })
        atomic_json(cache, row)
        imported += 1
    return imported


def expand(rows: Iterable[dict]) -> list[dict]:
    expanded = []
    for row in rows:
        for use in row["uses"]:
            item = dict(row)
            item.pop("uses", None)
            item.update(use)
            expanded.append(item)
    return sorted(expanded, key=lambda row: (
        row["section"], row["traffic"], row["configured_injection_rate"],
        row["topology_label"], row["seed"],
    ))


def aggregate_rows(rows: list[dict]) -> list[dict]:
    groups: dict[tuple, list[dict]] = {}
    for row in expand(rows):
        key = (row["section"], row["topology_label"], row["topology_class"],
               row["traffic"], row["configured_injection_rate"])
        groups.setdefault(key, []).append(row)
    result = []
    metrics = ("accepted_throughput", "average_packet_latency_cycles",
               "average_hops", "delivered_escape_fraction",
               "max_link_utilization", "link_utilization_cv", "host_seconds")
    for key, samples in sorted(groups.items()):
        record = dict(zip(("section", "topology_label", "topology_class",
                           "traffic", "configured_injection_rate"), key))
        record["samples"] = len(samples)
        record["completion_rate"] = sum(
            row.get("termination_reason") == "simulate_limit" for row in samples
        ) / len(samples)
        for metric in metrics:
            values = [float(row[metric]) for row in samples
                      if row.get(metric) is not None]
            if values:
                record[metric + "_mean"] = statistics.fmean(values)
                record[metric + "_sd"] = (statistics.stdev(values)
                                             if len(values) > 1 else 0.0)
        result.append(record)
    return result


def write_csv(path: Path, rows: list[dict]) -> None:
    columns = sorted({key for row in rows for key in row})
    with path.open("w", newline="", encoding="utf-8") as destination:
        writer = csv.DictWriter(destination, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def snapshot(output: Path, executions: list[Execution], rows: list[dict],
             failures: list[dict], start: float) -> None:
    ordered = sorted(rows, key=lambda row: row["execution_key"])
    aggregates = aggregate_rows(ordered)
    atomic_json(output / "executions.json", ordered)
    atomic_json(output / "results.json", expand(ordered))
    atomic_json(output / "aggregates.json", aggregates)
    atomic_json(output / "failures.json", failures)
    write_csv(output / "aggregates.csv", aggregates)
    seconds = [float(row["host_seconds"]) for row in rows
               if row.get("host_seconds")]
    progress = {
        "unique_executions_total": len(executions),
        "unique_executions_complete": len(rows),
        "unique_executions_failed_this_invocation": len(failures),
        "logical_rows_complete": len(expand(rows)),
        "elapsed_wall_seconds": time.time() - start,
        "median_observed_host_seconds": statistics.median(seconds) if seconds else None,
    }
    atomic_json(output / "progress.json", progress)


def write_progress(output: Path, executions: list[Execution], rows: list[dict],
                   failures: list[dict], start: float) -> None:
    seconds = [float(row["host_seconds"]) for row in rows
               if row.get("host_seconds")]
    atomic_json(output / "progress.json", {
        "unique_executions_total": len(executions),
        "unique_executions_complete": len(rows),
        "unique_executions_failed_this_invocation": len(failures),
        "elapsed_wall_seconds": time.time() - start,
        "median_observed_host_seconds":
            statistics.median(seconds) if seconds else None,
    })
    atomic_json(output / "failures.json", failures)


def make_bundle(output: Path) -> Path:
    bundle = output / "garnet_full_paper_results.tar.gz"
    include = ("manifest.json", "progress.json", "executions.json",
               "results.json", "aggregates.json", "aggregates.csv",
               "failures.json", "failure_logs",
               "topology_inputs")
    with tarfile.open(bundle, "w:gz") as archive:
        for name in include:
            path = output / name
            if path.exists():
                archive.add(path, arcname=name)
    return bundle


def build_garnet(jobs: int) -> None:
    environment = os.environ.copy()
    completed = subprocess.run(
        [sys.executable, "-c", "import sysconfig; print(sysconfig.get_config_var('LIBDIR') or '')"],
        text=True, capture_output=True, check=True,
    )
    libdir = completed.stdout.strip()
    if libdir:
        environment["LD_LIBRARY_PATH"] = libdir + ":" + environment.get("LD_LIBRARY_PATH", "")
    command = ["scons", "build/Garnet_standalone/gem5.opt",
               "NUMBER_BITS_PER_SET=256", f"-j{jobs}"]
    print("Building Garnet:", " ".join(command), flush=True)
    subprocess.run(command, cwd=ROOT, env=environment, check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--sections", nargs="+", choices=SECTIONS,
                        default=list(SECTIONS))
    parser.add_argument("--workers", type=int, default=4,
                        help="concurrent gem5 processes (4 for the target VM)")
    parser.add_argument("--build-jobs", type=int, default=3,
                        help="parallel compiler jobs; 3 is conservative for 15 GiB")
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--no-resume", action="store_true")
    parser.add_argument("--no-import-tables-3-5", action="store_true")
    parser.add_argument("--keep-raw", action="store_true")
    parser.add_argument("--plan-only", action="store_true")
    parser.add_argument("--max-executions", type=int,
                        help="debug/smoke-test limit; omit for paper data")
    parser.add_argument("--seconds-per-8x8-run", type=float,
                        default=DEFAULT_SECONDS_8X8)
    args = parser.parse_args()
    if args.workers < 1 or args.build_jobs < 1:
        parser.error("worker and build job counts must be positive")

    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    cache_dir = output / "case_cache"
    cache_dir.mkdir(exist_ok=True)
    (output / "failure_logs").mkdir(exist_ok=True)
    catalog = suite.topology_catalog(output)
    executions = build_executions(all_cases(catalog, output),
                                  set(args.sections), output)

    if not args.skip_build and not args.plan_only:
        build_garnet(args.build_jobs)
    garnet.RESULTS = output / "gem5"
    imported = 0
    if not args.no_resume and not args.no_import_tables_3_5:
        imported = import_tables_3_5(executions, TABLE_3_5_RESULTS, cache_dir)

    cached = {}
    if not args.no_resume:
        for execution in executions:
            path = cache_dir / f"{execution.key}.json"
            if path.exists():
                cached[execution.key] = json.loads(path.read_text(encoding="utf-8"))
    pending = [item for item in executions if item.key not in cached]
    if args.max_executions is not None:
        if args.max_executions < 1:
            parser.error("--max-executions must be positive")
        pending = pending[:args.max_executions]
    weighted_8x8_runs = sum((item.options["dimension"] / 8) ** 2
                            for item in pending)
    cpu_hours = weighted_8x8_runs * args.seconds_per_8x8_run / 3600
    section_logical_counts = {
        section: sum(use["section"] == section for item in executions
                     for use in item.uses)
        for section in args.sections
    }
    section_unique_counts = {
        section: sum(any(use["section"] == section for use in item.uses)
                     for item in executions)
        for section in args.sections
    }
    manifest = {
        "schema": SCHEMA,
        "created_by": Path(__file__).name,
        "sections": args.sections,
        "logical_cases": sum(len(item.uses) for item in executions),
        "unique_executions": len(executions),
        "logical_cases_by_section": section_logical_counts,
        "unique_executions_used_by_section": section_unique_counts,
        "cached_executions": len(cached),
        "imported_tables_3_5_this_invocation": imported,
        "pending_executions": len(pending),
        "debug_execution_limit": args.max_executions,
        "workers": args.workers,
        "build_jobs": args.build_jobs,
        "estimated_cpu_hours": cpu_hours,
        "estimated_ideal_wall_hours": cpu_hours / args.workers,
        "tables_3_5_excluded": True,
        "source_experiment_definition": str(Path(suite.__file__).resolve()),
        "git_commit": subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True,
            capture_output=True, check=False).stdout.strip(),
    }
    atomic_json(output / "manifest.json", manifest)
    print(json.dumps(manifest, indent=2), flush=True)
    if args.plan_only:
        return

    rows = list(cached.values())
    failures = []
    start = time.time()
    pending_iter = iter(pending)
    active = {}
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        while True:
            while len(active) < args.workers:
                try:
                    execution = next(pending_iter)
                except StopIteration:
                    break
                future = pool.submit(run_execution, execution, output,
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
                    atomic_json(cache_dir / f"{execution.key}.json", row)
                except Exception as error:
                    failure = {
                        "execution_key": execution.key,
                        "case": asdict(execution.case),
                        "options": execution.options,
                        "uses": execution.uses,
                        "error": repr(error),
                    }
                    failures.append(failure)
                    run_dirs = sorted((output / "gem5" / "runs").glob(
                        f"paper_{execution.key}*"))
                    if run_dirs and (run_dirs[-1] / "run.log").exists():
                        shutil.copy2(run_dirs[-1] / "run.log",
                                     output / "failure_logs" /
                                     f"{execution.key}.log")
                complete = len(rows)
                print(f"[{complete}/{len(executions)}] pending="
                      f"{len(executions) - complete - len(failures)} "
                      f"failures={len(failures)}", flush=True)
            finished = len(rows) + len(failures)
            if finished % 32 < len(done):
                write_progress(output, executions, rows, failures, start)
            # Per-case caches provide fine-grained recovery.  Rewriting the
            # growing multi-megabyte result set every few cases would waste
            # substantial cluster I/O, so checkpoint it only occasionally.
            if finished % 512 < len(done):
                snapshot(output, executions, rows, failures, start)

    snapshot(output, executions, rows, failures, start)
    bundle = make_bundle(output)
    print(f"Finished: {len(rows)} executions, {len(failures)} failures", flush=True)
    print(f"Copy this archive back to the workstation: {bundle}", flush=True)


if __name__ == "__main__":
    main()
