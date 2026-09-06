#!/usr/bin/env python3
"""Run every Garnet sample required by report Figures 3--9 and Table 6.

The experiment definitions deliberately reuse the production definitions in
``run_20260831_standalone_suite.py``.  The resulting logical data sets are:

* ``main``: all coarse and knee-refined rate points for Figures 3--6;
* ``random_distribution``: 200 Random placements x two traffic seeds for
  Figure 7;
* ``cross``: every traffic-aware Greedy/SA placement on every traffic at
  R=0.65 and R=0.80, including Mesh and Random baselines, for Table 6.

Figure 8 is derived from the Uniform ``main`` telemetry.  Figure 9 uses the
per-directed-link vectors embedded in every cached result, so it needs no
additional Garnet executions.  Executions shared by sections are deduplicated.

The runner is resumable by default and writes each completed execution to
``case_cache`` immediately.  Unless ``--keep-raw`` is supplied, large gem5
configuration/stat files are removed after all required statistics have been
copied into the compact cache.  This reduces expected disk use from roughly
95 GiB to well below 1 GiB.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import shutil
import statistics
import sys
from typing import Iterable

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import express_mesh_project.run_20260831_standalone_suite as suite
import express_mesh_project.run_phase3_measurement_v2 as garnet


ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "express_mesh_project"
DEFAULT_OUTPUT = (PROJECT / "results" / "20260906" /
                  "garnet_figures_3_9_table6")
DEFAULT_IMPORT = (PROJECT / "results" / "20260906" /
                  "garnet_main_validation" / "results.json")
SECTIONS = ("main", "random_distribution", "cross")
CROSS_RATES = (0.65, 0.80)
FIXED_SEEDS = (5, 6, 7, 8)
RANDOM_MAIN_SEEDS = (5, 6)
RANDOM_DISTRIBUTION_SEEDS = (7, 8)
EMPIRICAL_SECONDS_PER_RUN = 42.74
RESULT_SCHEMA = 1


@dataclass
class Execution:
    topology_file: Path
    topology_label: str
    traffic: str
    rate: float
    seed: int
    deadlock_threshold: int = 50_000
    uses: list[dict] = field(default_factory=list)

    def identity(self) -> dict:
        return {
            "schema": RESULT_SCHEMA,
            "topology_sha256": file_sha256(self.topology_file),
            "traffic": self.traffic,
            "rate": self.rate,
            "seed": self.seed,
            "warmup_cycles": 20_000,
            "measurement_cycles": 100_000,
            "config": standard_config() | {
                "deadlock_threshold": self.deadlock_threshold,
            },
        }

    def key(self) -> str:
        payload = json.dumps(self.identity(), sort_keys=True,
                             separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()[:24]


@lru_cache(maxsize=None)
def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def standard_config() -> dict:
    return {
        "source_route": True,
        "source_route_policy": 4,
        "source_route_candidates": 8,
        "reservation_weight": 0.6,
        "vc_pressure_weight": 1.0,
        "express_budget": 32,
        "express_max_degree": 1,
        "express_min_wire_length": 3,
        "express_info_mode": "distance-gossip",
        "express_info_period": 1,
        "express_info_delay": 1,
        "express_info_bits": 4,
        "express_admission_fraction": 1.0,
        "express_reservation_mode": "registered",
        "dimension": 8,
        "router_latency": 1,
        "mesh_link_latency": 1,
        "vcs_per_vnet": 4,
        "buffers_per_data_vc": 1,
        "buffers_per_ctrl_vc": 1,
        "inj_vnet": 0,
        "escape_timeout": 32,
        "deadlock_threshold": 50_000,
    }


def safe_label(text: str) -> str:
    return "".join(character if character.isalnum() or character == "_"
                   else "_" for character in text)


def build_executions(catalog: dict, wanted_sections: set[str]) -> list[Execution]:
    executions: dict[str, Execution] = {}

    def add(path: Path, topology_label: str, traffic: str, rate: float,
            seed: int, use: dict, deadlock_threshold: int = 50_000) -> None:
        candidate = Execution(path.resolve(), safe_label(topology_label),
                              traffic, float(rate), int(seed),
                              deadlock_threshold)
        key = candidate.key()
        if key not in executions:
            executions[key] = candidate
        elif executions[key].topology_file != candidate.topology_file:
            # Content-identical files are behaviorally interchangeable.  Keep
            # the first stable path but retain every logical role below.
            pass
        executions[key].uses.append(use)

    if "main" in wanted_sections:
        for traffic in suite.TRAFFICS:
            for rate in suite.MAIN_RATE_SWEEPS[traffic]:
                for topology_class in ("mesh", "greedy", "sa"):
                    label = ("mesh" if topology_class == "mesh" else
                             f"{traffic}_{topology_class}")
                    for seed in FIXED_SEEDS:
                        add(catalog[traffic][topology_class], label, traffic,
                            rate, seed, {
                                "section": "main",
                                "topology_class": topology_class,
                                "topology_label": topology_class,
                            })
                for topology_seed in range(1, 11):
                    label = f"random_p{topology_seed}"
                    for seed in RANDOM_MAIN_SEEDS:
                        add(catalog["random"][f"p{topology_seed}"], label,
                            traffic, rate, seed, {
                                "section": "main",
                                "topology_class": "random",
                                "topology_label": label,
                                "random_topology_seed": topology_seed,
                            })

    if "random_distribution" in wanted_sections:
        for topology_seed in range(1, 201):
            label = f"random_p{topology_seed}"
            for seed in RANDOM_DISTRIBUTION_SEEDS:
                add(catalog["random"][f"p{topology_seed}"], label,
                    "uniform_random", 0.8, seed, {
                        "section": "random_distribution",
                        "topology_class": "random",
                        "topology_label": label,
                        "random_topology_seed": topology_seed,
                    }, deadlock_threshold=200_000)

    if "cross" in wanted_sections:
        trained_traffics = (*suite.TRAFFICS, "mixture")
        for rate in CROSS_RATES:
            for trained in trained_traffics:
                for algorithm in ("greedy", "sa"):
                    label = f"{trained}_{algorithm}"
                    for traffic in suite.TRAFFICS:
                        for seed in FIXED_SEEDS:
                            add(catalog[trained][algorithm], label, traffic,
                                rate, seed, {
                                    "section": "cross",
                                    "topology_class": algorithm,
                                    "topology_label": label,
                                    "trained_traffic": trained,
                                    "placement_algorithm": algorithm,
                                })
            for traffic in suite.TRAFFICS:
                for seed in FIXED_SEEDS:
                    add(catalog[traffic]["mesh"], "mesh", traffic, rate,
                        seed, {
                            "section": "cross",
                            "topology_class": "mesh",
                            "topology_label": "mesh",
                            "trained_traffic": "baseline",
                            "placement_algorithm": "mesh",
                        })
                for topology_seed in range(1, 11):
                    label = f"random_p{topology_seed}"
                    for seed in RANDOM_MAIN_SEEDS:
                        add(catalog["random"][f"p{topology_seed}"], label,
                            traffic, rate, seed, {
                                "section": "cross",
                                "topology_class": "random",
                                "topology_label": label,
                                "trained_traffic": "baseline",
                                "placement_algorithm": "random",
                                "random_topology_seed": topology_seed,
                            })

    return sorted(executions.values(), key=lambda item: (
        item.traffic, item.rate, item.topology_label, item.seed,
    ))


def directed_endpoints(dimension: int, topology: dict) -> list[tuple[int, int, int]]:
    """Reproduce Garnet's source/destination-sorted NetworkLink order."""
    endpoints: list[tuple[int, int, int]] = []
    for row in range(dimension):
        for col in range(dimension - 1):
            west = col + row * dimension
            east = west + 1
            endpoints.extend(((west, east, -1), (east, west, -1)))
    for col in range(dimension):
        for row in range(dimension - 1):
            south = col + row * dimension
            north = south + dimension
            endpoints.extend(((south, north, -1), (north, south, -1)))
    for index, edge in enumerate(topology.get("express_links", [])):
        u, v = int(edge["u"]), int(edge["v"])
        endpoints.extend(((u, v, index), (v, u, index)))
    return sorted(endpoints, key=lambda link: (link[0], link[1]))


def attach_link_telemetry(row: dict) -> None:
    if "directed_link_utilization" in row:
        return
    stats_path = Path(row["run_dir"]) / "stats.txt"
    if not stats_path.exists():
        raise FileNotFoundError(f"missing link telemetry source: {stats_path}")
    stats = stats_path.read_text(encoding="utf-8")
    row["host_seconds"] = garnet.stat_values(stats, "hostSeconds")[0]
    values = garnet.stat_values(
        stats, "system.ruby.network.express_mesh_int_link_utilization")
    topology = json.loads(
        Path(row["topology_file"]).read_text(encoding="utf-8"))
    endpoints = directed_endpoints(int(topology["dimension"]), topology)
    if len(endpoints) != len(values):
        raise RuntimeError(
            f"link-vector mismatch: {len(endpoints)} endpoints versus "
            f"{len(values)} values for {row['topology_file']}")
    row["directed_link_sources"] = [source for source, _, _ in endpoints]
    row["directed_link_destinations"] = [destination
                                         for _, destination, _ in endpoints]
    row["directed_link_express_ids"] = [express_id
                                        for _, _, express_id in endpoints]
    row["directed_link_utilization"] = values


def legacy_row_valid(row: dict, execution: Execution) -> bool:
    expected = standard_config()
    names = {
        "source_route": "source_route",
        "source_route_policy": "source_route_policy",
        "source_route_candidates": "source_route_candidates",
        "reservation_weight": "reservation_weight",
        "vc_pressure_weight": "express_vc_weight",
        "express_budget": "express_budget",
        "express_max_degree": "express_max_degree",
        "express_min_wire_length": "express_min_wire_length",
        "express_info_mode": "express_info_mode",
        "express_info_period": "express_info_period",
        "express_info_delay": "express_info_delay",
        "express_info_bits": "express_info_bits",
        "express_admission_fraction": "express_admission_fraction",
        "express_reservation_mode": "express_reservation_mode",
        "dimension": "dimension",
        "router_latency": "router_latency",
        "mesh_link_latency": "mesh_link_latency",
        "vcs_per_vnet": "vcs_per_vnet",
        "buffers_per_data_vc": "buffers_per_data_vc",
        "buffers_per_ctrl_vc": "buffers_per_ctrl_vc",
        "inj_vnet": "inj_vnet",
        "escape_timeout": "escape_timeout",
        "deadlock_threshold": "garnet_deadlock_threshold",
    }
    try:
        if (row["traffic"] != execution.traffic or
                float(row["configured_injection_rate"]) != execution.rate or
                int(row["seed"]) != execution.seed or
                row["warmup_cycles"] != 20_000 or
                row["measurement_cycles"] != 100_000 or
                row["termination_reason"] != "simulate_limit" or
                file_sha256(Path(row["topology_file"])) !=
                file_sha256(execution.topology_file)):
            return False
        expected["deadlock_threshold"] = execution.deadlock_threshold
        return all(row.get(row_name) == expected[expected_name]
                   for expected_name, row_name in names.items())
    except (KeyError, FileNotFoundError, TypeError, ValueError):
        return False


def import_legacy(executions: Iterable[Execution], import_path: Path,
                  cache_dir: Path) -> int:
    if not import_path.exists():
        return 0
    legacy = json.loads(import_path.read_text(encoding="utf-8"))
    imported = 0
    for execution in executions:
        cache = cache_dir / f"{execution.key()}.json"
        if cache.exists():
            continue
        matches = [row for row in legacy if legacy_row_valid(row, execution)]
        if len(matches) != 1:
            continue
        row = dict(matches[0])
        attach_link_telemetry(row)
        row["execution_key"] = execution.key()
        row["uses"] = execution.uses
        row["imported_from"] = str(import_path.resolve())
        cache.write_text(json.dumps(row, indent=2, sort_keys=True) + "\n",
                         encoding="utf-8")
        imported += 1
    return imported


def compact_run_dir(run_dir: Path, allowed_root: Path) -> None:
    resolved = run_dir.resolve()
    root = allowed_root.resolve()
    if root not in resolved.parents:
        raise RuntimeError(f"refusing to compact path outside {root}: {resolved}")
    for name in ("fs",):
        path = resolved / name
        if path.is_dir():
            shutil.rmtree(path)
    for name in ("config.ini", "config.json", "stats.txt"):
        path = resolved / name
        if path.is_file():
            path.unlink()


def run_execution(execution: Execution, output: Path,
                  keep_raw: bool, resume: bool) -> dict:
    cache = output / "case_cache" / f"{execution.key()}.json"
    if resume and cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    garnet.TOPOLOGIES[execution.topology_label] = execution.topology_file
    spec = (execution.topology_label, "committed", execution.traffic,
            execution.rate, execution.seed)
    config = standard_config()
    row = garnet.run_one(
        spec, 20_000, 100_000, execution.deadlock_threshold,
        source_route=config["source_route"],
        source_route_policy=config["source_route_policy"], no_escape=False,
        topology_dir=Path("."),
        reservation_weight=config["reservation_weight"],
        vc_pressure_weight=config["vc_pressure_weight"],
        express_budget=config["express_budget"],
        express_max_degree=config["express_max_degree"],
        express_min_wire_length=config["express_min_wire_length"],
        express_info_mode=config["express_info_mode"],
        express_info_period=config["express_info_period"],
        express_info_delay=config["express_info_delay"],
        express_info_bits=config["express_info_bits"],
        express_admission_fraction=config["express_admission_fraction"],
        express_reservation_mode=config["express_reservation_mode"],
        dimension=config["dimension"],
        source_route_candidates=config["source_route_candidates"],
        router_latency=config["router_latency"],
        mesh_link_latency=config["mesh_link_latency"],
        vcs_per_vnet=config["vcs_per_vnet"],
        buffers_per_data_vc=config["buffers_per_data_vc"],
        buffers_per_ctrl_vc=config["buffers_per_ctrl_vc"],
        inj_vnet=config["inj_vnet"], escape_timeout=config["escape_timeout"],
    )
    attach_link_telemetry(row)
    row["execution_key"] = execution.key()
    row["uses"] = execution.uses
    cache.write_text(json.dumps(row, indent=2, sort_keys=True) + "\n",
                     encoding="utf-8")
    if not keep_raw:
        compact_run_dir(Path(row["run_dir"]), output / "gem5" / "runs")
    return row


def expanded_rows(rows: Iterable[dict]) -> list[dict]:
    expanded = []
    for row in rows:
        for use in row["uses"]:
            item = {key: value for key, value in row.items() if key != "uses"}
            item.update(use)
            expanded.append(item)
    return sorted(expanded, key=lambda row: (
        row["section"], row["traffic"], row["configured_injection_rate"],
        row["topology_label"], row["seed"],
    ))


def write_snapshot(output: Path, rows: list[dict], failures: list[dict]) -> None:
    ordered = sorted(rows, key=lambda row: row["execution_key"])
    (output / "executions.json").write_text(
        json.dumps(ordered, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    (output / "results.json").write_text(
        json.dumps(expanded_rows(ordered), indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    (output / "failures.json").write_text(
        json.dumps(failures, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")


def existing_rows(executions: Iterable[Execution], cache_dir: Path) -> list[dict]:
    rows = []
    for execution in executions:
        path = cache_dir / f"{execution.key()}.json"
        if path.exists():
            rows.append(json.loads(path.read_text(encoding="utf-8")))
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sections", nargs="+", choices=SECTIONS,
                        default=list(SECTIONS))
    parser.add_argument("--workers", type=int, default=2,
                        help="concurrent gem5 processes; 1--2 is safest in WSL")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--import-main-validation", type=Path,
                        default=DEFAULT_IMPORT,
                        help="reuse behavior-identical completed Garnet runs")
    parser.add_argument("--no-import", action="store_true")
    parser.add_argument("--no-resume", action="store_true")
    parser.add_argument("--keep-raw", action="store_true",
                        help="retain ~17 MiB of raw gem5 files per execution")
    parser.add_argument("--plan-only", action="store_true")
    parser.add_argument("--seconds-per-run", type=float,
                        default=EMPIRICAL_SECONDS_PER_RUN)
    args = parser.parse_args()
    if args.workers < 1:
        parser.error("--workers must be positive")

    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    cache_dir = output / "case_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    garnet.RESULTS = output / "gem5"
    catalog = suite.topology_catalog(output)
    executions = build_executions(catalog, set(args.sections))

    imported = 0
    if not args.no_import and not args.no_resume:
        imported = import_legacy(executions,
                                 args.import_main_validation.resolve(),
                                 cache_dir)
    cached = [] if args.no_resume else existing_rows(executions, cache_dir)
    cached_keys = {row["execution_key"] for row in cached}
    pending = [execution for execution in executions
               if execution.key() not in cached_keys]
    estimated_cpu_hours = len(pending) * args.seconds_per_run / 3600.0
    manifest = {
        "schema": RESULT_SCHEMA,
        "sections": args.sections,
        "logical_rows": sum(len(execution.uses) for execution in executions),
        "unique_executions": len(executions),
        "cached_executions": len(cached),
        "imported_this_invocation": imported,
        "pending_executions": len(pending),
        "workers": args.workers,
        "empirical_seconds_per_run": args.seconds_per_run,
        "estimated_cpu_hours_remaining": estimated_cpu_hours,
        "ideal_parallel_wall_hours_remaining":
            estimated_cpu_hours / args.workers,
        "cross_rates": CROSS_RATES,
        "random_distribution_deadlock_threshold": 200_000,
        "config": standard_config(),
        "keep_raw": args.keep_raw,
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    print(json.dumps(manifest, indent=2, sort_keys=True), flush=True)
    if args.plan_only:
        return
    if not garnet.GEM5_BINARY.exists():
        raise FileNotFoundError(
            f"missing Garnet binary {garnet.GEM5_BINARY}; build it first")

    rows = list(cached)
    failures: list[dict] = []
    completed = len(rows)
    # Submit only a small batch at a time.  Ctrl-C therefore stops after at
    # most a few active runs instead of leaving thousands of queued futures.
    batch_size = max(args.workers * 3, 1)
    for begin in range(0, len(pending), batch_size):
        batch = pending[begin:begin + batch_size]
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            futures = {
                executor.submit(run_execution, execution, output,
                                args.keep_raw, not args.no_resume): execution
                for execution in batch
            }
            for future in as_completed(futures):
                execution = futures[future]
                try:
                    rows.append(future.result())
                    completed += 1
                except Exception as error:
                    failures.append({
                        "execution_key": execution.key(),
                        "topology_file": str(execution.topology_file),
                        "traffic": execution.traffic,
                        "rate": execution.rate,
                        "seed": execution.seed,
                        "uses": execution.uses,
                        "error": repr(error),
                    })
                print(f"[{completed}/{len(executions)}] "
                      f"failures={len(failures)}", flush=True)
        write_snapshot(output, rows, failures)

    write_snapshot(output, rows, failures)
    host_seconds = [float(row.get("host_seconds", 0.0)) for row in rows
                    if row.get("host_seconds")]
    if host_seconds:
        print(f"observed median host seconds/run: "
              f"{statistics.median(host_seconds):.2f}", flush=True)
    print(f"wrote {len(rows)} unique executions and "
          f"{len(expanded_rows(rows))} logical rows to {output}; "
          f"failures={len(failures)}", flush=True)


if __name__ == "__main__":
    main()
