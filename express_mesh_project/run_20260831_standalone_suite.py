#!/usr/bin/env python3
"""Run and summarize the experiment matrix in docs/20260831.md.

The suite is intentionally resumable.  Per-run JSON lives below ``runs/``
(ignored by git); compact aggregates, figures, the manifest, and the informal
Markdown report remain suitable for version control.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import csv
from dataclasses import dataclass, asdict, field
import hashlib
import json
import math
from pathlib import Path
import statistics
import subprocess
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from express_mesh_project.model import (
    bit_complement_demand,
    cutstress_bidirectional_demand,
    soc_heterogeneous_demand,
    tornado_demand,
    uniform_demand,
)
from express_mesh_project.placement import random_placement
from express_mesh_project.search_placement_advanced import (
    load_placement,
    placement_record,
)


ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "express_mesh_project"
BINARY = PROJECT / "standalone_noc" / "express_noc"
DEFAULT_OUTPUT = PROJECT / "results" / "20260831" / "standalone_suite"
TRAFFICS = (
    "uniform_random", "tornado", "bit_complement",
    "cutstress_bidirectional",
)
RATES = tuple(round(index * 0.05, 2) for index in range(1, 17))


def _merged_rate_sweep(start: float, stop: float) -> tuple[float, ...]:
    """Keep the report-wide coarse sweep and add 0.01 saturation samples."""
    fine = (round(start + index * 0.01, 2)
            for index in range(round((stop - start) / 0.01) + 1))
    return tuple(sorted(set(RATES).union(fine)))


# Bit-complement reaches its knee earlier than Tornado/CutStress.  These are
# measurement refinements only: placement, routing, seeds, and all hardware
# parameters remain identical to the original main experiment.
MAIN_RATE_SWEEPS = {
    "uniform_random": RATES,
    "tornado": _merged_rate_sweep(0.36, 0.80),
    "bit_complement": _merged_rate_sweep(0.26, 0.52),
    "cutstress_bidirectional": _merged_rate_sweep(0.36, 0.80),
}
STANDARD = {
    "source_route_policy": 4,
    "source_route_candidates": 8,
    "source_mesh_routing": "xy",
    "reservation_weight": 0.6,
    "vc_pressure_weight": 1.0,
    "express_info_mode": "distance-gossip",
    "express_info_period": 1,
    "express_info_delay": 1,
    "express_info_bits": 4,
    "express_reservation_mode": "registered",
    "express_admission_fraction": 1.0,
    "escape_timeout": 32,
    "wire_budget": 32,
    "max_degree": 1,
    "min_wire_length": 3,
    "vcs_per_vnet": 4,
    "buffer_depth": 1,
    "packet_flits": 1,
    "router_latency": 1,
    "mesh_link_latency": 1,
    "express_latency_mode": "topology",
    "express_wire_per_cycle": 4,
}


@dataclass(frozen=True)
class Case:
    section: str
    topology_label: str
    topology_class: str
    topology_file: str
    traffic: str
    rate: float
    seed: int
    warmup: int = 20_000
    measurement: int = 100_000
    overrides: dict[str, object] = field(default_factory=dict)

    def key(self) -> str:
        payload = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()[:20]


def write_mesh(path: Path, dimension: int) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "name": f"mesh_{dimension}x{dimension}",
        "dimension": dimension,
        "node_count": dimension * dimension,
        "latency_model": "ideal",
        "constraints": {"wire_cost": 0, "max_degree": 0},
        "express_links": [],
    }
    path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return path


def write_placement(path: Path, name: str, dimension: int, edges,
                    budget: int, min_length: int, metadata=None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    record = placement_record(
        name, list(edges), metadata or {}, dimension=dimension,
        wire_budget=budget, max_degree=1, min_wire_length=min_length,
    )
    path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")
    return path


def make_random(path: Path, dimension: int, budget: int, min_length: int,
                seed: int, latency_model: str = "ideal") -> Path:
    edges = random_placement(
        dimension, budget, 1, min_length, latency_model, seed,
    )
    return write_placement(
        path, f"random_{dimension}_{budget}_{seed}", dimension, edges,
        budget, min_length, {"algorithm": "random", "seed": seed},
    )


def demand_for(traffic: str, dimension: int):
    return {
        "uniform_random": uniform_demand(dimension * dimension),
        "tornado": tornado_demand(dimension),
        "bit_complement": bit_complement_demand(dimension),
        "cutstress_bidirectional": cutstress_bidirectional_demand(dimension),
        "soc_heterogeneous": soc_heterogeneous_demand(dimension),
    }[traffic]


def topology_catalog(output: Path) -> dict[str, dict[str, Path]]:
    generated = PROJECT / "results" / "20260831" / "placements"
    b32 = generated / "b32"
    sa_root = generated / "b32_sa"
    # The report-facing SA is one unified reheat algorithm for every traffic.
    # It scores the complete injection-rate curve, starts every restart from
    # matched ASPL Greedy, then independently validates an elite archive with
    # a small pointwise throughput guardrail.  Keep these paths versioned:
    # Case.key() contains the path (not a content hash), so replacing an old
    # best.json in place would incorrectly reuse cached main-suite runs.
    validated_sa = {
        "uniform_random": (
            PROJECT / "results" / "20260902" / "sa_curve_unified_v2" /
            "uniform_random" / "best.json"
        ),
        "tornado": (
            PROJECT / "results" / "20260902" / "sa_curve_unified_v2" /
            "tornado" / "best.json"
        ),
        "bit_complement": (
            PROJECT / "results" / "20260902" / "sa_curve_unified_v2" /
            "bit_complement" / "best.json"
        ),
        "cutstress_bidirectional": (
            PROJECT / "results" / "20260902" / "sa_curve_unified_v2" /
            "cutstress_bidirectional" / "best.json"
        ),
    }
    mesh = PROJECT / "results" / "phase1" / "mesh.json"
    catalog: dict[str, dict[str, Path]] = {}
    for traffic in TRAFFICS:
        greedy = b32 / f"{traffic}_aspl.json"
        sa = validated_sa[traffic]
        if not sa.exists():
            sa = sa_root / traffic / "best.json"
            if traffic == "uniform_random":
                existing_b32_sa = (PROJECT / "results" / "budget32_v8" /
                                   "sa_search" / "best.json")
                if existing_b32_sa.exists():
                    sa = existing_b32_sa
        if not greedy.exists():
            raise FileNotFoundError(f"missing matched Greedy placement: {greedy}")
        if not sa.exists():
            # A fallback keeps the runner usable while searches are in flight,
            # but the report marks it and never labels it an SA improvement.
            sa = greedy
        catalog[traffic] = {"mesh": mesh, "greedy": greedy, "sa": sa}
    mixture_greedy = generated / "mixture_b32" / "network_mix4_aspl.json"
    # The mixture archive is re-ranked by its cross-workload aggregate on
    # independent validation seeds.  Finalist 09 is the validated winner;
    # report seeds 5--8 remain untouched by this choice.
    mixture_sa = (
        PROJECT / "results" / "20260902" / "sa_curve_unified_v2" /
        "mixture" / "finalist_09.json"
    )
    legacy_mixture_sa = generated / "mixture_b32_sa" / "best.json"
    if not mixture_greedy.exists():
        mixture_greedy = catalog["uniform_random"]["greedy"]
    if not mixture_sa.exists():
        mixture_sa = (legacy_mixture_sa if legacy_mixture_sa.exists()
                      else mixture_greedy)
    catalog["mixture"] = {
        "mesh": mesh, "greedy": mixture_greedy, "sa": mixture_sa,
    }
    random_dir = generated / "random_b32"
    catalog["random"] = {}
    # The main/cross expectations use only the first ten layouts, while the
    # dedicated distribution figure needs a much larger independent topology
    # sample.  Keeping all 400 in one catalog makes the seed mapping explicit.
    for seed in range(1, 401):
        path = random_dir / f"p{seed}" / "random.json"
        if not path.exists():
            make_random(path, 8, 32, 3, seed)
        catalog["random"][f"p{seed}"] = path
    return catalog


def standard_overrides(**changes) -> dict[str, object]:
    result = dict(STANDARD)
    result.update(changes)
    return result


def main_cases(catalog) -> list[Case]:
    cases = []
    for traffic in TRAFFICS:
        for rate in MAIN_RATE_SWEEPS[traffic]:
            for label in ("mesh", "greedy", "sa"):
                for seed in (5, 6, 7, 8):
                    cases.append(Case(
                        "main", label, label,
                        str(catalog[traffic][label]), traffic, rate, seed,
                    ))
            # Random expectation: topology is the independent sample; two
            # traffic seeds per each of ten layouts keep the full curve finite.
            for topology_seed in range(1, 11):
                for seed in (5, 6):
                    cases.append(Case(
                        "main", f"random_p{topology_seed}", "random",
                        str(catalog["random"][f"p{topology_seed}"]),
                        traffic, rate, seed,
                    ))
    return cases


def random_distribution_cases(catalog) -> list[Case]:
    """Two traffic seeds for each of 400 independent Random layouts."""
    cases = []
    for topology_seed in range(1, 401):
        for seed in (7, 8):
            cases.append(Case(
                "random_distribution", f"random_p{topology_seed}", "random",
                str(catalog["random"][f"p{topology_seed}"]),
                "uniform_random", 0.8, seed,
                overrides={"continue_after_ni_watchdog": True},
            ))
    return cases


def cross_cases(catalog, rate: float = 0.8,
                seeds: tuple[int, ...] = (5, 6, 7, 8),
                warmup: int = 20_000, measurement: int = 100_000,
                include_baselines: bool = True) -> list[Case]:
    cases = []
    training = list(TRAFFICS) + ["mixture"]
    for trained in training:
        for algorithm in ("greedy", "sa"):
            path = catalog[trained][algorithm]
            for traffic in TRAFFICS:
                for seed in seeds:
                    cases.append(Case(
                        "cross", f"{trained}_{algorithm}", algorithm,
                        str(path), traffic, rate, seed, warmup, measurement,
                    ))
    if not include_baselines:
        return cases
    for traffic in TRAFFICS:
        for seed in seeds:
            cases.append(Case(
                "cross", "mesh", "mesh", str(catalog[traffic]["mesh"]),
                traffic, rate, seed, warmup, measurement,
            ))
        for topology_seed in range(1, 11):
            # Random retains two traffic seeds per independent topology in the
            # production matrix.  A caller can omit baselines entirely during
            # disjoint-seed operating-point screening.
            for seed in seeds[:2]:
                cases.append(Case(
                    "cross", f"random_p{topology_seed}", "random",
                    str(catalog["random"][f"p{topology_seed}"]),
                    traffic, rate, seed, warmup, measurement,
                ))
    return cases


def routing_cases(catalog) -> list[Case]:
    variants = {
        # Keep policy-4's top-K express sequence selected and committed at
        # injection.  Only the ordinary mesh portions are routed per-hop by
        # the shared LocalMeshAdaptive primitive toward the next waypoint.
        "local_mesh_adaptive": {"source_mesh_routing": "adaptive"},
        "static_shortest": {"source_route_policy": 0,
                            "express_reservation_mode": "instant",
                            "express_info_mode": "instant"},
        "random_top8": {"source_route_policy": 3,
                        "express_reservation_mode": "instant",
                        "express_info_mode": "instant"},
        "q_only": {"reservation_weight": 0.0},
        "express_dijkstra": {"source_route_policy": 5,
                             "express_reservation_mode": "instant",
                             "express_info_mode": "instant"},
        "standard": {},
        "global_dijkstra_oracle": {"source_route_policy": 6,
                                   "express_reservation_mode": "instant",
                                   "express_info_mode": "instant"},
    }
    cases = []
    for topology in ("greedy", "sa"):
        for name, overrides in variants.items():
            for rate in (0.7, 0.8):
                for seed in (5, 6, 7, 8):
                    cases.append(Case(
                        "routing_ablation", f"{topology}:{name}", topology,
                        str(catalog["uniform_random"][topology]),
                        "uniform_random", rate, seed,
                        overrides=overrides,
                    ))
    return cases


def information_cases(catalog) -> list[Case]:
    cases = []
    modes = {
        "instant": {
            "express_info_mode": "instant",
            "express_info_delay": 0,
            "express_info_bits": 0,
            "express_reservation_mode": "instant",
        },
        "physical_registered": {},
    }
    for topology in ("mesh", "greedy", "sa"):
        for mode, overrides in modes.items():
            for rate in (0.7, 0.8):
                for seed in (5, 6, 7, 8):
                    cases.append(Case(
                        "information_ablation", f"{topology}:{mode}",
                        topology, str(catalog["uniform_random"][topology]),
                        "uniform_random", rate, seed, overrides=overrides,
                    ))
    return cases


def escape_cases(catalog) -> list[Case]:
    cases = []
    greedy = str(catalog["uniform_random"]["greedy"])
    for timeout in (8, 16, 32, 64, 128):
        for rate in RATES:
            for seed in range(1, 21):
                cases.append(Case(
                    "escape", f"on_t{timeout}", "greedy", greedy,
                    "uniform_random", rate, seed,
                    overrides={"escape_timeout": timeout,
                               "continue_after_ni_watchdog": True},
                ))
    for traffic in TRAFFICS:
        topology = str(catalog[traffic]["greedy"])
        for rate in RATES:
            for seed in range(1, 21):
                cases.append(Case(
                    "escape_off", "off", "greedy", topology,
                    traffic, rate, seed,
                    overrides={"no_escape": True,
                               "correct_no_escape_vcs": True,
                               "continue_after_ni_watchdog": True,
                               "drain_cycles": 20_000},
                ))
    return cases


def escape_deadlock_contrast_cases(catalog) -> list[Case]:
    """A reproducible cyclic-dependency stress test for the escape VC.

    Two VCs are intentionally used: one adaptive VC plus one escape VC when
    escape is enabled, versus two unrestricted adaptive VCs when disabled.
    All other routing, placement, traffic, and random seeds are paired.
    """
    topology = str(catalog["uniform_random"]["greedy"])
    common = {
        "vcs_per_vnet": 2,
        "deadlock_threshold": 5_000,
        "drain_cycles": 50_000,
        "continue_after_ni_watchdog": True,
    }
    cases = []
    for seed in range(1, 21):
        cases.append(Case(
            "escape_deadlock_contrast", "on_t32", "greedy", topology,
            "uniform_random", 0.4, seed, warmup=2_000, measurement=10_000,
            overrides=common,
        ))
        cases.append(Case(
            "escape_deadlock_contrast", "off", "greedy", topology,
            "uniform_random", 0.4, seed, warmup=2_000, measurement=10_000,
            overrides=common | {
                "no_escape": True,
                "correct_no_escape_vcs": True,
            },
        ))
    return cases


def scaling_topologies(output: Path, dimension: int, budget: int,
                       min_length: int, traffic="uniform_random"):
    root = output / "generated_scaling" / f"n{dimension}_b{budget}_{traffic}"
    mesh = write_mesh(root / "mesh.json", dimension)
    random_path = make_random(root / "random.json", dimension, budget,
                              min_length, 10_000 + dimension + budget)
    # For 8x8 use the fully recomputed ASPL Greedy/SA where available.
    if dimension == 8 and budget == 32:
        catalog = topology_catalog(output)
        return mesh, random_path, catalog[traffic]["greedy"], catalog[traffic]["sa"]
    if dimension == 8 and traffic == "uniform_random" and budget in (64, 128):
        corrected = PROJECT / "results" / "20260903" / "scaling_corrected"
        greedy = corrected / f"n8_b{budget}" / "uniform_random_aspl.json"
        sa = corrected / f"n8_b{budget}_sa" / "best.json"
        if not greedy.exists() or not sa.exists():
            raise FileNotFoundError(
                f"missing corrected 8x8/B{budget} Greedy or SA placement"
            )
        return mesh, random_path, greedy, sa
    # Never label selected Random samples as Greedy/SA.  A new scaling point
    # must first run the real placement generators and explicitly register
    # their output above.
    raise FileNotFoundError(
        f"no algorithm-generated Greedy/SA placements registered for "
        f"{dimension}x{dimension}, budget={budget}, traffic={traffic}"
    )


def scaling_cases(output: Path) -> list[Case]:
    # Each budget is evaluated near its own saturation region rather than at
    # one fixed offered load.  The 16x16 point scales B32 by router count and
    # physical distance (32 * 4 * 2 = 256), and doubles K/minimum length/VCs.
    # Row 3 keeps unrestricted express admission and the K16 routing algorithm.
    # The 8x8 escape timeout is doubled with linear network dimension, from 32
    # to 64 cycles.  Random remains diagnostic and is not used for selection.
    # Row numbers are explicit so deleting an ablation does not silently
    # relabel every later result and invalidate references in the report.
    rows = [
        (0, 8, 32, 0.8, {}),
        (1, 8, 64, 0.8, {}),
        (2, 8, 128, 0.9, {}),
        (3, 16, 256, 0.45, {
            "vcs_per_vnet": 8,
            "source_route_candidates": 16,
            "retain_mesh_candidate": True,
            "express_admission_fraction": 1.0,
            "escape_timeout": 64,
            "continue_after_ni_watchdog": True,
        }),
        (4, 16, 256, 0.45, {
            "vcs_per_vnet": 8,
            "source_route_candidates": 16,
            "retain_mesh_candidate": True,
            "express_admission_fraction": 1.0,
            "escape_timeout": 64,
            "continue_after_ni_watchdog": True,
            "express_latency_mode": "length-aware",
            "express_wire_per_cycle": 4,
        }),
        (5, 8, 32, 0.8, {"vcs_per_vnet": 8}),
        (6, 8, 64, 0.8, {
            "express_latency_mode": "length-aware",
            "express_wire_per_cycle": 2,
        }),
        # Keep the escape patience measured in packet-service opportunities:
        # two-flit serialization doubles the one-flit timeout of 32 cycles.
        (7, 8, 64, 0.8, {
            "packet_flits": 2,
            "escape_timeout": 64,
            "continue_after_ni_watchdog": True,
        }),
        # A probabilistic tiled heterogeneous-SoC workload.  Its hottest
        # endpoint is 3x the uniform-endpoint mean, corresponding to an
        # analytical configured-rate ceiling of 2/3.  Rate 0.55 is a
        # pressure-heavy 0.05-grid point.  A 256-cycle local escape timeout
        # avoids prematurely funnelling recoverable adaptive traffic into the
        # single escape VC; the placement algorithms and pressure policy are
        # unchanged and are both re-evaluated under this complete row config.
        # Hardware/routing parameters otherwise match the ideal-latency
        # 16x16 row3, and both traffic-aware placements are searched afresh.
        (8, 16, 256, 0.55, {
            "traffic": "soc_heterogeneous",
            "vcs_per_vnet": 8,
            "source_route_candidates": 16,
            "retain_mesh_candidate": True,
            "express_admission_fraction": 1.0,
            "escape_timeout": 256,
            "continue_after_ni_watchdog": True,
        }),
    ]
    cases = []
    row_sa = {
        3: (PROJECT / "results" / "20260903" / "scaling_corrected" /
            "n16_b256_sa_row3_t64" / "best.json"),
        4: (PROJECT / "results" / "20260904" / "scaling_length_aware" /
            "n16_b256_sa_row4_d4" / "best.json"),
        5: (PROJECT / "results" / "20260903" / "scaling_corrected" /
            "n8_b32_sa_row4_v8" / "best.json"),
        6: (PROJECT / "results" / "20260904" / "scaling_length_aware" /
            "n8_b64_sa_row6_d2" / "best.json"),
        7: (PROJECT / "results" / "20260903" / "scaling_corrected" /
            "n8_b64_sa_row7_packet2_t64" / "best.json"),
        8: (PROJECT / "results" / "20260906" / "row8_t256" /
            "chain_s44" / "best.json"),
    }
    for index, dimension, budget, rate, overrides in rows:
        traffic = str(overrides.get("traffic", "uniform_random"))
        min_length = 6 if dimension == 16 else 3
        if dimension == 16:
            corrected = (PROJECT / "results" / "20260903" /
                         "scaling_corrected" / "n16_b256")
            sa = row_sa[index]
            if not sa.exists():
                raise FileNotFoundError(f"missing corrected 16x16 SA: {sa}")
            greedy = corrected / "uniform_random_aspl.json"
            if index == 4:
                greedy = (PROJECT / "results" / "20260904" /
                          "scaling_length_aware" / "n16_b256_d4_greedy" /
                          "uniform_random_aspl.json")
            elif index == 8:
                greedy = (PROJECT / "results" / "20260905" /
                          "row8_v6" / "greedy" /
                          "soc_heterogeneous_aspl.json")
            fixed = {
                "mesh": corrected / "mesh.json",
                "greedy": greedy,
                "sa": sa,
            }
            # The SoC search procedure was revised after inspecting seeds 5--8.
            # Use the next consecutive, previously unseen block for every fixed
            # topology so its final comparison is paired and genuinely held out.
            fixed_seeds = (17, 18, 19, 20) if index == 8 else (5, 6, 7, 8)
            for label, topology in fixed.items():
                for seed in fixed_seeds:
                    params = dict(overrides)
                    params.pop("traffic", None)
                    params.update({"wire_budget": budget,
                                   "min_wire_length": min_length})
                    cases.append(Case(
                        "scaling", f"row{index}:{label}", label,
                        str(topology), traffic, rate, seed,
                        overrides=params,
                    ))
            # Scaling uses five independent Random layouts per 16x16 row;
            # main/cross 8x8 comparisons retain their ten-layout baseline.
            for topology_seed in range(1, 6):
                topology = corrected / f"random_p{topology_seed}.json"
                for seed in (5, 6):
                    params = dict(overrides)
                    params.pop("traffic", None)
                    params.update({"wire_budget": budget,
                                   "min_wire_length": min_length})
                    cases.append(Case(
                        "scaling", f"row{index}:random_p{topology_seed}",
                        "random", str(topology), traffic, rate, seed,
                        overrides=params,
                    ))
            continue
        topologies = list(scaling_topologies(
            output, dimension, budget, min_length, traffic,
        ))
        if index == 6:
            topologies[2] = (PROJECT / "results" / "20260904" /
                             "scaling_length_aware" / "n8_b64_d2_greedy" /
                             "uniform_random_aspl.json")
        if index in row_sa:
            if not row_sa[index].exists():
                raise FileNotFoundError(
                    f"missing row-specific SA for row{index}: {row_sa[index]}"
                )
            topologies[3] = row_sa[index]
        for label, topology in zip(("mesh", "random", "greedy", "sa"),
                                   topologies):
            for seed in (5, 6, 7, 8):
                params = dict(overrides)
                params.pop("traffic", None)
                params.update({"wire_budget": budget,
                               "min_wire_length": min_length})
                cases.append(Case(
                    "scaling", f"row{index}:{label}", label, str(topology),
                    traffic, rate, seed, overrides=params,
                ))
    return cases


def command_for(case: Case, output: Path) -> list[str]:
    options = standard_overrides(**case.overrides)
    command = [
        str(BINARY), "--topology-file", case.topology_file,
        "--topology", case.topology_label, "--routing", "adaptive",
        "--traffic", case.traffic, "--rate", str(case.rate),
        "--seed", str(case.seed), "--warmup-cycles", str(case.warmup),
        "--measurement-cycles", str(case.measurement),
    ]
    source_route = bool(options.pop("source_route", True))
    if not source_route:
        # Registered reservations are defined only for committed policy-4
        # source routes.  Per-hop adaptive routing has no route to register.
        options["express_reservation_mode"] = "instant"
        options["express_info_mode"] = "instant"
    flags = {
        "no_escape": "--no-escape",
        "correct_no_escape_vcs": "--correct-no-escape-vcs",
        "continue_after_ni_watchdog": "--continue-after-ni-watchdog",
        "retain_mesh_candidate": "--retain-mesh-candidate",
    }
    if source_route:
        command.append("--source-route")
    for key, flag in flags.items():
        if options.pop(key, False):
            command.append(flag)
    mapping = {
        "source_route_policy": "--source-route-policy",
        "source_route_candidates": "--source-route-candidates",
        "source_mesh_routing": "--source-mesh-routing",
        "reservation_weight": "--reservation-weight",
        "vc_pressure_weight": "--express-vc-weight",
        "express_info_mode": "--express-info-mode",
        "express_info_period": "--express-info-period",
        "express_info_delay": "--express-info-delay",
        "express_info_bits": "--express-info-bits",
        "express_reservation_mode": "--express-reservation-mode",
        "express_admission_fraction": "--express-admission-fraction",
        "escape_timeout": "--escape-timeout",
        "wire_budget": "--express-wire-budget",
        "max_degree": "--express-max-degree",
        "min_wire_length": "--express-min-wire-length",
        "vcs_per_vnet": "--vcs-per-vnet",
        "buffer_depth": "--buffer-depth",
        "packet_flits": "--packet-flits",
        "router_latency": "--router-latency",
        "mesh_link_latency": "--mesh-link-latency",
        "express_latency_mode": "--express-latency-mode",
        "express_wire_per_cycle": "--express-wire-per-cycle",
        "deadlock_threshold": "--deadlock-threshold",
        "drain_cycles": "--drain-cycles",
    }
    for key, value in options.items():
        if key not in mapping:
            raise KeyError(f"unmapped standalone option {key}")
        command += [mapping[key], str(value)]
    return command + ["--output", str(output)]


METRICS = (
    "accepted_throughput", "average_packet_latency_cycles", "average_hops",
    "delivered_escape_fraction", "max_link_utilization",
    "p95_link_utilization", "link_utilization_cv",
    "per_source_throughput_jain",
)

# High-dimensional telemetry is useful for a few explanatory plots but would
# make a checked-in all-run JSON exceed common Git hosting limits.  Every raw
# run remains locally resumable below ignored runs/; selected representative
# samples are retained in diagnostics.json.
LARGE_RESULT_FIELDS = {
    "per_source_packets_received", "per_source_received",
    "per_source_throughput", "per_source_average_latency_cycles",
    "per_source_average_latency", "latency_histogram_upper_bounds",
    "latency_histogram", "escape_latency_histogram",
    "adaptive_latency_histogram", "directed_link_sources",
    "directed_link_destinations", "directed_link_express_ids",
    "directed_link_utilization", "express_edge_packets_selected",
    "express_q_sample_sum", "express_q_sample_max", "express_r_sample_sum",
    "express_r_sample_max", "express_q_sample_average",
    "express_r_sample_average", "reservation_increments",
    "reservation_decrements",
}


def run_cases(cases: list[Case], output_dir: Path, workers: int,
              resume: bool) -> list[dict]:
    run_dir = output_dir / "runs"
    run_dir.mkdir(parents=True, exist_ok=True)

    def run(case: Case):
        path = run_dir / f"{case.section}_{case.key()}.json"
        if not (resume and path.exists()):
            completed = subprocess.run(
                command_for(case, path), text=True, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, check=False,
            )
            if completed.returncode:
                return {"case": asdict(case), "case_key": case.key(),
                        "error": completed.stdout[-4000:]}
        row = json.loads(path.read_text(encoding="utf-8"))
        row.update({
            "section": case.section,
            "topology_label": case.topology_label,
            "topology_class": case.topology_class,
            "topology_file": case.topology_file,
            "case_key": case.key(),
        })
        return row

    rows = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(run, case): case for case in cases}
        for index, future in enumerate(as_completed(futures), 1):
            rows.append(future.result())
            if index % 25 == 0 or index == len(cases):
                print(f"[{index}/{len(cases)}]", flush=True)
    return rows


def aggregate(rows: list[dict]) -> list[dict]:
    groups: dict[tuple, list[dict]] = {}
    for row in rows:
        if "error" in row:
            continue
        key = (row["section"], row["topology_label"],
               row["topology_class"], row["traffic"],
               row["configured_injection_rate"])
        groups.setdefault(key, []).append(row)
    result = []
    for key, samples in sorted(groups.items()):
        item = dict(zip(("section", "topology_label", "topology_class",
                         "traffic", "rate"), key))
        item["sample_count"] = len(samples)
        completed = []
        for row in samples:
            reason = str(row.get("termination_reason", ""))
            if reason:
                valid = (reason.startswith("simulate_limit") or
                         reason == "drain_completed")
            else:
                valid = not row.get("no_progress", False)
            if valid:
                completed.append(row)
        item["completion_count"] = len(completed)
        for metric in METRICS:
            values = [float(row.get(metric, 0.0)) for row in completed]
            if values:
                item[f"{metric}_mean"] = statistics.fmean(values)
                item[f"{metric}_sd"] = (statistics.stdev(values)
                                          if len(values) > 1 else 0.0)
                item[f"{metric}_ci95"] = (
                    1.96 * item[f"{metric}_sd"] / math.sqrt(len(values)))
            else:
                # A watchdog-stopped run is censored, not a zero-throughput
                # sample.  JSON null keeps that distinction explicit.
                item[f"{metric}_mean"] = None
                item[f"{metric}_sd"] = None
                item[f"{metric}_ci95"] = None
        if completed:
            item["express_traversals_per_packet_mean"] = statistics.fmean(
                float(row.get("express_traversals", 0)) /
                max(float(row.get("packets_received", 0)), 1.0)
                for row in completed
            )
        else:
            item["express_traversals_per_packet_mean"] = None
        result.append(item)
    return result


def write_outputs(output_dir: Path, cases, rows, summary):
    output_dir.mkdir(parents=True, exist_ok=True)
    rows = [row for row in rows if row.get("section") != "placement_ablation"]
    summary = [row for row in summary
               if row.get("section") != "placement_ablation"]
    (output_dir / "manifest.json").write_text(
        json.dumps([asdict(case) for case in cases], indent=2) + "\n",
        encoding="utf-8",
    )
    diagnostic_path = output_dir / "diagnostics.json"
    diagnostics = {}
    if diagnostic_path.exists():
        diagnostics = {row["case_key"]: row for row in json.loads(
            diagnostic_path.read_text(encoding="utf-8"))}
    valid_keys = {row.get("case_key") for row in rows}
    diagnostics = {key: row for key, row in diagnostics.items()
                   if key in valid_keys}
    for row in rows:
        keep_main = (
            row.get("section") == "main" and
            row.get("traffic") == "uniform_random" and
            row.get("configured_injection_rate") == 0.8 and
            row.get("seed") == 5
        )
        keep_row8 = (
            row.get("section") == "scaling" and
            row.get("topology_label") == "row8:sa"
        )
        if "error" not in row and (keep_main or keep_row8):
            topology = row.get("topology_class")
            # Keep one Random layout plus each deterministic class.
            if (keep_row8 or topology != "random" or
                    row.get("topology_label") == "random_p1"):
                # A later section-by-section merge reads compact results.json.
                # Never let such a row erase telemetry previously recovered
                # from the ignored per-run JSON.
                key = row["case_key"]
                has_telemetry = any(field in row for field in LARGE_RESULT_FIELDS)
                if has_telemetry or key not in diagnostics:
                    diagnostics[key] = row
    diagnostic_path.write_text(
        json.dumps(list(diagnostics.values()), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    compact_rows = [
        {key: value for key, value in row.items()
         if key not in LARGE_RESULT_FIELDS}
        for row in rows
    ]
    (output_dir / "results.json").write_text(
        json.dumps(compact_rows, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (output_dir / "aggregate.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8",
    )
    if summary:
        with (output_dir / "aggregate.csv").open(
                "w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(summary[0]))
            writer.writeheader();writer.writerows(summary)
    failures = [row for row in rows if "error" in row]
    (output_dir / "failures.json").write_text(
        json.dumps(failures, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--sections", nargs="+", choices=[
        "main", "random_distribution", "cross", "routing", "information",
        "escape", "escape_deadlock_contrast", "scaling",
    ], default=[
        "main", "random_distribution", "cross", "routing", "information",
        "escape", "escape_deadlock_contrast", "scaling",
    ])
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--aggregate-only", action="store_true")
    parser.add_argument("--cross-rate", type=float, default=0.65)
    parser.add_argument("--cross-seeds", type=int, nargs="+",
                        default=[5, 6, 7, 8])
    parser.add_argument("--cross-warmup", type=int, default=20_000)
    parser.add_argument("--cross-measurement", type=int, default=100_000)
    parser.add_argument("--cross-optimized-only", action="store_true")
    args = parser.parse_args()
    if not BINARY.exists():
        subprocess.run(["make", "-C", str(BINARY.parent)], check=True)
    catalog = topology_catalog(args.output_dir)
    builders = {
        "main": lambda: main_cases(catalog),
        "random_distribution": lambda: random_distribution_cases(catalog),
        "cross": lambda: cross_cases(
            catalog, args.cross_rate, tuple(args.cross_seeds),
            args.cross_warmup, args.cross_measurement,
            not args.cross_optimized_only,
        ),
        "routing": lambda: routing_cases(catalog),
        "information": lambda: information_cases(catalog),
        "escape": lambda: escape_cases(catalog),
        "escape_deadlock_contrast": lambda: escape_deadlock_contrast_cases(
            catalog),
        "scaling": lambda: scaling_cases(args.output_dir),
    }
    cases = [case for section in args.sections for case in builders[section]()]
    if args.aggregate_only:
        rows = json.loads((args.output_dir / "results.json").read_text(
            encoding="utf-8"))
        rows = [row for row in rows
                if row.get("section") != "placement_ablation"]
    else:
        rows = run_cases(cases, args.output_dir, args.workers, args.resume)
        replaced_sections = set()
        for section in args.sections:
            if section == "routing":
                replaced_sections.add("routing_ablation")
            elif section == "information":
                replaced_sections.add("information_ablation")
            elif section == "escape":
                replaced_sections.update(("escape", "escape_off"))
            else:
                replaced_sections.add(section)
        replaced_sections.add("placement_ablation")
        # Section-by-section execution is useful for a multi-hour suite.  Keep
        # prior sections in the compact result instead of overwriting them.
        old_results = args.output_dir / "results.json"
        if old_results.exists():
            old_rows = json.loads(old_results.read_text(encoding="utf-8"))
            # The removed placement weight sweep must never leak back into a
            # newly aggregated ASPL-only suite.  Sections requested in
            # this invocation are replaced atomically, not appended by key,
            # because changing a topology file changes the case key.
            merged = {
                row.get("case_key"): row for row in old_rows
                if row.get("case_key") is not None
                and row.get("section") not in replaced_sections
            }
            merged.update({row.get("case_key"): row for row in rows})
            rows = list(merged.values())
        old_manifest = args.output_dir / "manifest.json"
        if old_manifest.exists():
            old_cases = [Case(**item) for item in json.loads(
                old_manifest.read_text(encoding="utf-8"))]
            merged_cases = {
                case.key(): case for case in old_cases
                if case.section not in replaced_sections
            }
            merged_cases.update({case.key(): case for case in cases})
            cases = list(merged_cases.values())
    summary = aggregate(rows)
    write_outputs(args.output_dir, cases, rows, summary)
    print(f"wrote {len(rows)} rows, {len(summary)} groups to {args.output_dir}")


if __name__ == "__main__":
    main()
