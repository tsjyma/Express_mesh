#!/usr/bin/env python3
"""Advanced reversible placement searches for the V6 traffic-aware study.

This file is incremental: the original deterministic Greedy algorithms and
``search_placement_sa.py`` remain unchanged.  It adds three simulation-guided
SA variants and two searches driven by a path-based multicommodity-flow proxy.
Every topology is finally intended to run with the same policy-4 routing.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import math
from pathlib import Path
import random
from statistics import fmean
import subprocess
import sys
import tempfile
import threading

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from express_mesh_project.flow_placement import (
    compress_demand,
    direct_benefit_scores,
    flow_metrics,
    normalized_flow_utility,
)
from express_mesh_project.model import (
    ExpressEdge,
    GridGraph,
    bit_complement_demand,
    cutstress_bidirectional_demand,
    cutstress_demand,
    hotspot_demand,
    soc_heterogeneous_demand,
    tornado_demand,
    uniform_demand,
)
from express_mesh_project.placement import candidate_edges, validate_placement


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BINARY = ROOT / "express_mesh_project" / "standalone_noc" / "express_noc"
TRAFFIC_NAMES = (
    "uniform_random", "cutstress", "cutstress_bidirectional", "hotspot",
    "bit_complement", "tornado", "soc_heterogeneous",
)


def traffic_demand(name: str, n: int = 8):
    return {
        "uniform_random": uniform_demand(n * n),
        "cutstress": cutstress_demand(n),
        "cutstress_bidirectional": cutstress_bidirectional_demand(n),
        "hotspot": hotspot_demand(n),
        "bit_complement": bit_complement_demand(n),
        "tornado": tornado_demand(n),
        "soc_heterogeneous": soc_heterogeneous_demand(n),
    }[name]


def load_placement(path: Path) -> list[ExpressEdge]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return sorted([
        ExpressEdge(item["u"], item["v"], item["wire_length"], item["latency"])
        for item in data["express_links"]
    ], key=lambda edge: edge.key)


def placement_key(edges):
    return tuple(sorted(
        (edge.u, edge.v, edge.wire_length, edge.latency) for edge in edges
    ))


def placement_record(name: str, edges: list[ExpressEdge], metadata=None, *,
                     dimension: int = 8,
                     wire_budget: int = 64, max_degree: int = 1,
                     min_wire_length: int = 3,
                     latency_model: str = "ideal",
                     express_wire_per_cycle: int = 4):
    graph = GridGraph(dimension)
    constraints = validate_placement(
        dimension, edges, wire_budget, max_degree, min_wire_length,
    )
    result = {
        "name": name,
        "dimension": dimension,
        "node_count": dimension * dimension,
        "latency_model": latency_model,
        "express_wire_per_cycle": express_wire_per_cycle,
        "constraints": constraints,
        "express_links": [
            {
                "u": edge.u,
                "v": edge.v,
                "u_coord": list(graph.coordinate(edge.u)),
                "v_coord": list(graph.coordinate(edge.v)),
                "wire_length": edge.wire_length,
                "latency": edge.latency,
            }
            for edge in sorted(edges, key=lambda item: item.key)
        ],
    }
    if metadata:
        result["search_metadata"] = metadata
    return result


def offered_capacity(traffic: str, rate: float) -> float:
    return rate * (0.25 if traffic == "cutstress" else 0.5)


def _degrees_and_cost(edges, node_count=64):
    degree = [0] * node_count
    cost = 0
    keys = set()
    for edge in edges:
        degree[edge.u] += 1
        degree[edge.v] += 1
        cost += edge.wire_length
        keys.add(edge.key)
    return degree, cost, keys


class PlacementMutator:
    def __init__(self, demands, seed: int, pool_size: int = 192, *,
                 wire_budget: int = 64, max_degree: int = 1,
                 min_wire_length: int = 3, dimension: int = 8,
                 latency_model: str = "ideal",
                 express_wire_per_cycle: int = 4):
        self.rng = random.Random(seed)
        self.dimension = dimension
        self.wire_budget = wire_budget
        self.max_degree = max_degree
        self.min_wire_length = min_wire_length
        self.pool = candidate_edges(
            dimension, min_wire_length, latency_model,
            express_wire_per_cycle,
        )
        self.scores = direct_benefit_scores(dimension, self.pool, demands)
        self.ranked = sorted(
            self.pool,
            key=lambda edge: (-self.scores[edge.key], edge.wire_length, edge.key),
        )
        self.pool_size = min(pool_size, len(self.ranked))

    def _choose_removed(self, current, count, guided):
        if not guided:
            return set(self.rng.sample(range(len(current)), count))
        # Usually reconsider low direct-benefit links, while retaining a fixed
        # exploration probability for every existing link.  The old code only
        # sampled the lower-ranked half despite claiming nonzero probability
        # for all links; that silently froze some useful multi-edge swaps.
        if self.rng.random() < 0.30:
            return set(self.rng.sample(range(len(current)), count))
        ranked_indices = sorted(
            range(len(current)),
            key=lambda index: (self.scores.get(current[index].key, 0.0),
                               self.rng.random()),
        )
        window = ranked_indices[:max(count, (len(current) + 1) // 2)]
        return set(self.rng.sample(window, count))

    def _pick_addition(self, legal, strategy):
        exact = [edge for edge in legal if edge[1]]
        # Prefer exact budget completion sometimes, but do not make it a hard
        # restriction.  A hard preference made useful combinations such as a
        # length-6 plus length-3 refill unreachable whenever any legal
        # length-9 edge existed.
        choices = exact if exact and self.rng.random() < 0.25 else legal
        if strategy == "random":
            return self.rng.choice(choices)[0]
        choices.sort(
            key=lambda item: (-self.scores.get(item[0].key, 0.0),
                              item[0].wire_length, item[0].key)
        )
        if strategy == "guided":
            # A stable exploit/explore mixture.  Exploitation remains strongly
            # traffic-aware; exploration is wide enough to discover links that
            # look mediocre in isolation but relieve dynamic contention when
            # combined with another swap.
            if self.rng.random() < 0.70:
                limit = min(len(choices), 96)
                rank = min(limit - 1, int(self.rng.expovariate(0.20)))
            else:
                limit = min(len(choices), 192)
                rank = self.rng.randrange(limit)
        else:
            limit = min(len(choices), 384)
            if self.rng.random() < 0.50:
                rank = min(limit - 1, int(self.rng.expovariate(0.10)))
            else:
                rank = self.rng.randrange(limit)
        return choices[rank][0]

    def mutate(self, current, variant: str):
        for _ in range(512):
            if not current:
                remove_count = 0
                guided = variant != "classic"
                add_strategy = "random" if variant == "classic" else "guided"
            elif variant == "classic":
                remove_count = self.rng.randint(1, min(3, len(current)))
                guided = False
                add_strategy = "random"
            elif variant == "guided":
                draw = self.rng.random()
                remove_count = (1 if draw < 0.45 else
                                self.rng.randint(2, min(4, len(current)))
                                if draw < 0.85 else
                                self.rng.randint(4, min(7, len(current))))
                guided = True
                add_strategy = "guided"
            elif variant == "pair":
                # Explicit two-edge exchange is important under degree=1:
                # moving one endpoint often requires releasing another edge
                # at the same time.  It is a generic local neighborhood, not a
                # workload-specific proposal.
                remove_count = min(2, len(current))
                guided = False
                add_strategy = "mixed"
            elif variant == "large":
                remove_count = self.rng.randint(3, min(8, len(current)))
                guided = self.rng.random() < 0.75
                add_strategy = "mixed"
            else:
                raise ValueError(f"unknown mutation variant: {variant}")

            removed = (self._choose_removed(current, remove_count, guided)
                       if remove_count else set())
            removed_keys = {current[index].key for index in removed}
            proposal = [edge for index, edge in enumerate(current)
                        if index not in removed]
            degree, cost, keys = _degrees_and_cost(
                proposal, self.dimension * self.dimension,
            )
            while True:
                remaining = self.wire_budget - cost
                source_pool = (self.ranked[:self.pool_size]
                               if add_strategy != "random" else self.pool)
                legal = [
                    (edge, edge.wire_length == remaining)
                    for edge in source_pool
                    if edge.key not in keys
                    and edge.key not in removed_keys
                    and edge.wire_length <= remaining
                    and degree[edge.u] < self.max_degree
                    and degree[edge.v] < self.max_degree
                ]
                if not legal:
                    break
                edge = self._pick_addition(legal, add_strategy)
                proposal.append(edge)
                keys.add(edge.key)
                degree[edge.u] += 1
                degree[edge.v] += 1
                cost += edge.wire_length
            if (cost >= self.wire_budget - (self.min_wire_length - 1)
                    and placement_key(proposal) != placement_key(current)):
                validate_placement(
                    self.dimension, proposal, self.wire_budget, self.max_degree,
                    self.min_wire_length,
                )
                return sorted(proposal, key=lambda edge: edge.key)
        raise RuntimeError("could not construct a legal placement mutation")


class SimulationEvaluator:
    def __init__(self, binary, traffics, rates, seeds, warmup, measurement,
                 workers, scratch, *, reservation_weight=0.375,
                 vc_pressure_weight=0.625, express_info_mode="instant",
                 express_info_period=1, express_info_delay=0,
                 express_info_bits=0, express_admission_fraction=1.0,
                 express_reservation_mode="instant", latency_weight=0.005,
                 worst_throughput_weight=0.25,
                 wire_budget=64, max_degree=1, min_wire_length=3,
                 dimension=8, source_route_candidates=8,
                 retain_mesh_candidate=False, escape_timeout=32,
                 continue_after_ni_watchdog=False, vcs_per_vnet=4,
                 buffer_depth=1, packet_flits=1, router_latency=1,
                 mesh_link_latency=1, express_latency_mode="topology",
                 express_wire_per_cycle=4):
        self.binary = binary.resolve()
        self.traffics = traffics
        self.rates = rates
        self.seeds = seeds
        self.warmup = warmup
        self.measurement = measurement
        self.workers = workers
        self.scratch = scratch
        self.reservation_weight = reservation_weight
        self.vc_pressure_weight = vc_pressure_weight
        self.express_info_mode = express_info_mode
        self.express_info_period = express_info_period
        self.express_info_delay = express_info_delay
        self.express_info_bits = express_info_bits
        self.express_admission_fraction = express_admission_fraction
        self.express_reservation_mode = express_reservation_mode
        self.latency_weight = latency_weight
        self.worst_throughput_weight = worst_throughput_weight
        self.wire_budget = wire_budget
        self.max_degree = max_degree
        self.min_wire_length = min_wire_length
        self.dimension = dimension
        self.source_route_candidates = source_route_candidates
        self.retain_mesh_candidate = retain_mesh_candidate
        self.escape_timeout = escape_timeout
        self.continue_after_ni_watchdog = continue_after_ni_watchdog
        self.vcs_per_vnet = vcs_per_vnet
        self.buffer_depth = buffer_depth
        self.packet_flits = packet_flits
        self.router_latency = router_latency
        self.mesh_link_latency = mesh_link_latency
        self.express_latency_mode = express_latency_mode
        self.express_wire_per_cycle = express_wire_per_cycle
        self.cache = {}
        self.lock = threading.Lock()

    def _run_case(self, topology, traffic, rate, seed, tag):
        output = self.scratch / f"{tag}_{traffic}_{rate}_{seed}.json"
        command = [
            str(self.binary), "--topology-file", str(topology),
            "--topology", "advanced_sa", "--routing", "adaptive",
            "--traffic", traffic, "--rate", str(rate), "--seed", str(seed),
            "--warmup-cycles", str(self.warmup),
            "--measurement-cycles", str(self.measurement),
            "--source-route", "--source-route-policy", "4",
            "--source-route-candidates", str(self.source_route_candidates),
            "--source-mesh-routing", "xy",
            "--reservation-weight", str(self.reservation_weight),
            "--express-vc-weight", str(self.vc_pressure_weight),
            "--express-info-mode", self.express_info_mode,
            "--express-info-period", str(self.express_info_period),
            "--express-info-delay", str(self.express_info_delay),
            "--express-info-bits", str(self.express_info_bits),
            "--express-admission-fraction",
            str(self.express_admission_fraction),
            "--express-reservation-mode", self.express_reservation_mode,
            "--express-wire-budget", str(self.wire_budget),
            "--express-max-degree", str(self.max_degree),
            "--express-min-wire-length", str(self.min_wire_length),
            "--escape-timeout", str(self.escape_timeout),
            "--vcs-per-vnet", str(self.vcs_per_vnet),
            "--buffer-depth", str(self.buffer_depth),
            "--packet-flits", str(self.packet_flits),
            "--router-latency", str(self.router_latency),
            "--mesh-link-latency", str(self.mesh_link_latency),
            "--express-latency-mode", self.express_latency_mode,
            "--express-wire-per-cycle", str(self.express_wire_per_cycle),
            "--output", str(output),
        ]
        if self.retain_mesh_candidate:
            command.append("--retain-mesh-candidate")
        if self.continue_after_ni_watchdog:
            command.append("--continue-after-ni-watchdog")
        completed = subprocess.run(
            command, text=True, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, check=False,
        )
        if completed.returncode:
            raise RuntimeError(completed.stdout.strip())
        record = json.loads(output.read_text(encoding="utf-8"))
        # Placement search needs scalar objective inputs, not the large
        # per-source/per-link telemetry vectors.  Keeping those vectors for
        # hundreds of candidates previously made long searches grow toward
        # WSL OOM despite every child simulation having a small footprint.
        keep = {
            "accepted_throughput", "average_packet_latency_cycles",
            "termination_reason", "no_progress", "ni_watchdog_triggered",
            "max_ni_busy_streak", "packets_received",
        }
        return {key: value for key, value in record.items() if key in keep}

    def __call__(self, edges):
        key = placement_key(edges)
        with self.lock:
            cached = self.cache.get(key)
        if cached is not None:
            return cached
        tag = hashlib.sha1(repr(key).encode()).hexdigest()[:12]
        topology = self.scratch / f"topology_{tag}.json"
        topology.write_text(
            json.dumps(placement_record(
                "advanced_sa", edges, dimension=self.dimension,
                wire_budget=self.wire_budget,
                max_degree=self.max_degree,
                min_wire_length=self.min_wire_length,
            ), indent=2) + "\n",
            encoding="utf-8",
        )
        cases = [
            (traffic, rate, seed)
            for traffic, rate in zip(self.traffics, self.rates)
            for seed in self.seeds
        ]
        with ThreadPoolExecutor(max_workers=self.workers) as executor:
            results = list(executor.map(
                lambda case: self._run_case(topology, *case, tag), cases,
            ))
        by_traffic = []
        cursor = 0
        for traffic, rate in zip(self.traffics, self.rates):
            samples = results[cursor:cursor + len(self.seeds)]
            cursor += len(self.seeds)
            normalized_throughput = fmean(
                sample["accepted_throughput"] / offered_capacity(traffic, rate)
                for sample in samples
            )
            latency = fmean(
                max(0.0, sample["average_packet_latency_cycles"])
                for sample in samples
            )
            by_traffic.append({
                "traffic": traffic,
                "rate": rate,
                "normalized_throughput": normalized_throughput,
                "accepted_throughput": fmean(
                    sample["accepted_throughput"] for sample in samples
                ),
                "latency": latency,
                "samples": samples,
            })
        ratios = [item["normalized_throughput"] for item in by_traffic]
        latency_penalty = fmean(
            self.latency_weight * math.log1p(item["latency"])
            / math.log1p(self.measurement)
            for item in by_traffic
        )
        utility = (fmean(ratios)
                   + self.worst_throughput_weight * min(ratios)
                   - latency_penalty)
        record = {"utility": utility, "by_traffic": by_traffic}
        with self.lock:
            self.cache.setdefault(key, record)
            return self.cache[key]


class FlowEvaluator:
    def __init__(self, demands, *, max_commodities, rounds, max_express=1):
        self.full_demands = demands
        self.demands = [compress_demand(demand, max_commodities)
                        for demand in demands]
        self.rounds = rounds
        self.max_express = max_express
        self.mesh = [
            flow_metrics(8, [], demand, rounds=rounds,
                         max_express=max_express)
            for demand in self.demands
        ]
        self.cache = {}

    def __call__(self, edges):
        key = placement_key(edges)
        if key not in self.cache:
            metrics = [
                flow_metrics(
                    8, edges, demand, rounds=self.rounds,
                    max_express=self.max_express,
                )
                for demand in self.demands
            ]
            self.cache[key] = {
                "utility": normalized_flow_utility(metrics, self.mesh),
                "metrics": [metric.as_dict() for metric in metrics],
            }
        return self.cache[key]

    def full_score(self, edges, rounds=32, max_express=2):
        mesh = [flow_metrics(8, [], demand, rounds=rounds,
                             max_express=max_express)
                for demand in self.full_demands]
        metrics = [flow_metrics(8, edges, demand, rounds=rounds,
                                max_express=max_express)
                   for demand in self.full_demands]
        return {
            "utility": normalized_flow_utility(metrics, mesh),
            "metrics": [metric.as_dict() for metric in metrics],
            "mesh_metrics": [metric.as_dict() for metric in mesh],
        }


def _temperature(initial, final, iteration, iterations):
    fraction = iteration / max(1, iterations)
    return initial * (final / initial) ** fraction


def run_chain(initials, evaluator, mutator, args, *, reheat=False):
    global_best = None
    history = []
    for restart in range(args.restarts):
        current = list(initials[restart % len(initials)])
        current_result = evaluator(current)
        best, best_result = list(current), current_result
        for iteration in range(1, args.iterations + 1):
            if reheat:
                local_iteration = (iteration - 1) % args.reheat_period + 1
                temperature = _temperature(
                    args.initial_temperature, args.final_temperature,
                    local_iteration, args.reheat_period,
                )
                # Reheating should launch another excursion from the best
                # basin found by this restart.  Merely raising the temperature
                # while retaining an already poor current state wastes most of
                # the fixed simulation budget climbing back toward the
                # incumbent.  Guided mutation already includes occasional
                # 4--7-edge moves, so no traffic-specific jump rule is needed.
                if local_iteration == 1:
                    current, current_result = list(best), best_result
                mutation = ("pair" if local_iteration % 2 == 0
                            else "guided")
            else:
                temperature = _temperature(
                    args.initial_temperature, args.final_temperature,
                    iteration, args.iterations,
                )
                mutation = "classic"
            proposal = mutator.mutate(current, mutation)
            result = evaluator(proposal)
            delta = result["utility"] - current_result["utility"]
            accepted = (delta >= 0.0
                        or mutator.rng.random() < math.exp(delta / temperature))
            if accepted:
                current, current_result = proposal, result
            if result["utility"] > best_result["utility"]:
                best, best_result = list(proposal), result
            history.append({
                "restart": restart,
                "iteration": iteration,
                "temperature": temperature,
                "accepted": accepted,
                "proposal_utility": result["utility"],
                "current_utility": current_result["utility"],
                "best_utility": best_result["utility"],
            })
            print(
                f"restart={restart + 1}/{args.restarts} "
                f"iter={iteration}/{args.iterations} "
                f"proposal={result['utility']:.6f} "
                f"best={best_result['utility']:.6f}", flush=True,
            )
        if global_best is None or best_result["utility"] > global_best[1]["utility"]:
            global_best = (best, best_result)
    assert global_best is not None
    return global_best[0], global_best[1], history


def run_tempering(initials, evaluator, mutator, args):
    replica_count = args.replicas
    temperatures = [
        args.final_temperature * (
            args.initial_temperature / args.final_temperature
        ) ** (index / max(1, replica_count - 1))
        for index in range(replica_count)
    ]
    states = [list(initials[index % len(initials)])
              for index in range(replica_count)]
    results = [evaluator(state) for state in states]
    best_index = max(range(replica_count),
                     key=lambda index: results[index]["utility"])
    best, best_result = list(states[best_index]), results[best_index]
    history = []
    for iteration in range(1, args.iterations + 1):
        proposals = [
            mutator.mutate(state, "guided" if index < replica_count - 1 else "large")
            for index, state in enumerate(states)
        ]
        with ThreadPoolExecutor(max_workers=min(args.workers, replica_count)) as executor:
            proposal_results = list(executor.map(evaluator, proposals))
        accepted = []
        for index, (proposal, proposal_result) in enumerate(
                zip(proposals, proposal_results)):
            delta = proposal_result["utility"] - results[index]["utility"]
            take = (delta >= 0.0 or mutator.rng.random()
                    < math.exp(delta / temperatures[index]))
            accepted.append(take)
            if take:
                states[index], results[index] = proposal, proposal_result
            if proposal_result["utility"] > best_result["utility"]:
                best, best_result = list(proposal), proposal_result
        swaps = []
        if iteration % args.swap_period == 0:
            parity = (iteration // args.swap_period) % 2
            for low in range(parity, replica_count - 1, 2):
                high = low + 1
                exponent = (
                    (1.0 / temperatures[low] - 1.0 / temperatures[high])
                    * (results[high]["utility"] - results[low]["utility"])
                )
                take = exponent >= 0.0 or mutator.rng.random() < math.exp(exponent)
                swaps.append((low, high, take))
                if take:
                    states[low], states[high] = states[high], states[low]
                    results[low], results[high] = results[high], results[low]
        history.append({
            "iteration": iteration,
            "temperatures": temperatures,
            "accepted": accepted,
            "swaps": swaps,
            "replica_utilities": [result["utility"] for result in results],
            "best_utility": best_result["utility"],
        })
        print(
            f"tempering iter={iteration}/{args.iterations} "
            f"replicas={[round(r['utility'], 5) for r in results]} "
            f"best={best_result['utility']:.6f}", flush=True,
        )
    return best, best_result, history


def run_flow_search(initials, evaluator, mutator, args, *, large):
    evaluated_initials = [(list(edges), evaluator(edges)) for edges in initials]
    current, current_result = max(
        evaluated_initials, key=lambda item: item[1]["utility"]
    )
    best, best_result = list(current), current_result
    history = []
    stagnation = 0
    for iteration in range(1, args.iterations + 1):
        proposals = [
            mutator.mutate(current, "large" if large and index % 3 == 0
                           else "guided")
            for index in range(args.neighbors)
        ]
        proposal_results = [evaluator(proposal) for proposal in proposals]
        winner = max(range(len(proposals)),
                     key=lambda index: proposal_results[index]["utility"])
        proposal, result = proposals[winner], proposal_results[winner]
        improved_current = result["utility"] > current_result["utility"] + 1e-12
        if improved_current:
            current, current_result = proposal, result
            stagnation = 0
        else:
            stagnation += 1
        if result["utility"] > best_result["utility"] + 1e-12:
            best, best_result = list(proposal), result
        if stagnation >= args.stagnation_limit and large:
            current = mutator.mutate(best, "large")
            current_result = evaluator(current)
            stagnation = 0
        history.append({
            "iteration": iteration,
            "winner_utility": result["utility"],
            "improved_current": improved_current,
            "current_utility": current_result["utility"],
            "best_utility": best_result["utility"],
            "evaluated_placements": len(evaluator.cache),
        })
        print(
            f"flow iter={iteration}/{args.iterations} "
            f"winner={result['utility']:.6f} best={best_result['utility']:.6f} "
            f"evaluated={len(evaluator.cache)}", flush=True,
        )
    return best, best_result, history


def _single_traffic_pareto(candidate, incumbent, *, throughput_epsilon=0.0,
                           latency_epsilon=0.0):
    """Return whether candidate is no worse than incumbent on both metrics.

    Search utility is intentionally scalar so simulated annealing can cross
    valleys.  Final topology selection is different: for a single-workload
    experiment we must not publish a topology whose apparent utility gain came
    from trading away accepted throughput or latency relative to the supplied
    incumbent.  Small epsilons can be used to absorb validation noise.
    """
    if len(candidate["by_traffic"]) != 1 or len(incumbent["by_traffic"]) != 1:
        return False
    cand = candidate["by_traffic"][0]
    base = incumbent["by_traffic"][0]
    throughput_ok = (
        cand["accepted_throughput"]
        >= base["accepted_throughput"] - throughput_epsilon
    )
    latency_ok = cand["latency"] <= base["latency"] + latency_epsilon
    strictly_better = (
        cand["accepted_throughput"] > base["accepted_throughput"]
        or cand["latency"] < base["latency"]
    )
    return throughput_ok and latency_ok and strictly_better


def _curve_throughput_guardrail(candidate, incumbent, tolerance=0.0025):
    """Reject a curve candidate with a material regression at any rate.

    The scalar objective is useful during annealing, but it can otherwise
    exchange one knee-of-curve point for a larger saturated-throughput gain.
    Independent validation therefore applies the same small relative
    throughput tolerance at every sampled injection rate.  This is a generic
    robustness rule, independent of workload and topology.
    """
    candidate_points = candidate["by_traffic"]
    incumbent_points = incumbent["by_traffic"]
    if len(candidate_points) != len(incumbent_points):
        return False
    return all(
        cand["traffic"] == base["traffic"]
        and cand["rate"] == base["rate"]
        and cand["accepted_throughput"]
        >= base["accepted_throughput"] * (1.0 - tolerance)
        for cand, base in zip(candidate_points, incumbent_points)
    )


def validate_archive(initials, search_evaluator, search_best, args):
    """Re-rank an elite archive on independent, longer validation seeds.

    The first initial placement is the incumbent (normally traffic-aware ASPL
    Greedy).  It is always included in the archive and can be retained if the
    noisy short search finds no independently validated improvement.
    """
    ranked_search = sorted(
        search_evaluator.cache.items(),
        key=lambda item: item[1]["utility"], reverse=True,
    )
    candidates = {}

    def add(edges, origin):
        key = placement_key(edges)
        candidates.setdefault(key, {
            "edges": list(edges),
            "origins": [],
            "search_result": search_evaluator.cache.get(key),
        })["origins"].append(origin)

    for index, edges in enumerate(initials):
        add(edges, "incumbent" if index == 0 else f"initial_{index + 1}")
    add(search_best, "search_best")
    for index, (key, _result) in enumerate(
            ranked_search[:args.validation_top_n], 1):
        add([ExpressEdge(*item) for item in key], f"search_rank_{index}")

    with tempfile.TemporaryDirectory(prefix="express-v6-validation-") as temp:
        evaluator = SimulationEvaluator(
            args.binary, args.traffics, args.rates, args.validation_seeds,
            args.validation_warmup_cycles, args.validation_measurement_cycles,
            args.workers, Path(temp),
            reservation_weight=args.reservation_weight,
            vc_pressure_weight=args.vc_pressure_weight,
            express_info_mode=args.express_info_mode,
            express_info_period=args.express_info_period,
            express_info_delay=args.express_info_delay,
            express_info_bits=args.express_info_bits,
            express_admission_fraction=args.express_admission_fraction,
            express_reservation_mode=args.express_reservation_mode,
            latency_weight=args.latency_weight,
            worst_throughput_weight=args.worst_throughput_weight,
            wire_budget=args.wire_budget,
            max_degree=args.max_degree,
            min_wire_length=args.min_wire_length,
            dimension=args.dimension,
            source_route_candidates=args.source_route_candidates,
            retain_mesh_candidate=args.retain_mesh_candidate,
            escape_timeout=args.escape_timeout,
            continue_after_ni_watchdog=args.continue_after_ni_watchdog,
            vcs_per_vnet=args.vcs_per_vnet,
            buffer_depth=args.buffer_depth,
            packet_flits=args.packet_flits,
            router_latency=args.router_latency,
            mesh_link_latency=args.mesh_link_latency,
            express_latency_mode=args.express_latency_mode,
            express_wire_per_cycle=args.express_wire_per_cycle,
        )
        for index, item in enumerate(candidates.values(), 1):
            item["validation_result"] = evaluator(item["edges"])
            print(
                f"validation={index}/{len(candidates)} "
                f"utility={item['validation_result']['utility']:.6f} "
                f"origins={item['origins']}", flush=True,
            )

    incumbent_key = placement_key(initials[0])
    incumbent_result = candidates[incumbent_key]["validation_result"]
    pool = list(candidates.values())
    if args.incumbent_policy == "pareto":
        improvements = [
            item for item in pool
            if _single_traffic_pareto(
                item["validation_result"], incumbent_result,
                throughput_epsilon=args.pareto_throughput_epsilon,
                latency_epsilon=args.pareto_latency_epsilon,
            )
        ]
        pool = improvements or [candidates[incumbent_key]]
    elif args.incumbent_policy == "curve-guardrail":
        guarded = [
            item for item in pool
            if _curve_throughput_guardrail(
                item["validation_result"], incumbent_result,
                args.max_curve_throughput_regression,
            )
        ]
        pool = guarded or [candidates[incumbent_key]]
    selected = max(pool, key=lambda item: item["validation_result"]["utility"])
    archive = sorted(
        ({
            "placement_key": [list(value) for value in key],
            "origins": item["origins"],
            "search_result": item["search_result"],
            "validation_result": item["validation_result"],
            "selected": item is selected,
            "pareto_over_incumbent": _single_traffic_pareto(
                item["validation_result"], incumbent_result,
                throughput_epsilon=args.pareto_throughput_epsilon,
                latency_epsilon=args.pareto_latency_epsilon,
            ),
            "passes_curve_throughput_guardrail": (
                _curve_throughput_guardrail(
                    item["validation_result"], incumbent_result,
                    args.max_curve_throughput_regression,
                )
            ),
        } for key, item in candidates.items()),
        key=lambda item: item["validation_result"]["utility"], reverse=True,
    )
    return (selected["edges"], selected["validation_result"], archive,
            evaluator.cache)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=[
        "sa-geometric", "sa-reheat", "sa-tempering",
        "flow-swap", "flow-lns",
    ], required=True)
    parser.add_argument("--initial", type=Path, nargs="+", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--binary", type=Path, default=DEFAULT_BINARY)
    parser.add_argument("--traffics", nargs="+", choices=TRAFFIC_NAMES,
                        required=True)
    parser.add_argument("--rates", nargs="+", type=float, default=[0.8])
    parser.add_argument("--traffic-seeds", nargs="+", type=int, default=[1])
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--iterations", type=int, default=40)
    parser.add_argument("--restarts", type=int, default=2)
    parser.add_argument("--replicas", type=int, default=4)
    parser.add_argument("--swap-period", type=int, default=5)
    parser.add_argument("--reheat-period", type=int, default=15)
    parser.add_argument("--neighbors", type=int, default=8)
    parser.add_argument("--stagnation-limit", type=int, default=4)
    parser.add_argument("--initial-temperature", type=float, default=0.02)
    parser.add_argument("--final-temperature", type=float, default=0.0005)
    parser.add_argument("--warmup-cycles", type=int, default=500)
    parser.add_argument("--measurement-cycles", type=int, default=2000)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--latency-weight", type=float, default=0.005)
    parser.add_argument(
        "--worst-throughput-weight", type=float, default=0.25,
        help=("weight of the minimum normalized throughput point in addition "
              "to the equal-weight mean over all requested injection rates"),
    )
    parser.add_argument("--reservation-weight", type=float, default=0.375)
    parser.add_argument("--vc-pressure-weight", type=float, default=0.625)
    parser.add_argument(
        "--express-info-mode",
        choices=["instant", "delayed-global", "distance-gossip"],
        default="instant",
    )
    parser.add_argument("--express-info-period", type=int, default=1)
    parser.add_argument("--express-info-delay", type=int, default=0)
    parser.add_argument("--express-info-bits", type=int, default=0)
    parser.add_argument("--express-admission-fraction", type=float, default=1.0)
    parser.add_argument(
        "--express-reservation-mode",
        choices=["instant", "registered"], default="instant",
    )
    parser.add_argument("--flow-max-commodities", type=int, default=512)
    parser.add_argument("--flow-rounds", type=int, default=16)
    parser.add_argument("--proposal-pool", type=int, default=192)
    parser.add_argument("--dimension", type=int, default=8)
    parser.add_argument("--source-route-candidates", type=int, default=8)
    parser.add_argument("--retain-mesh-candidate", action="store_true")
    parser.add_argument("--escape-timeout", type=int, default=32)
    parser.add_argument("--continue-after-ni-watchdog", action="store_true")
    parser.add_argument("--vcs-per-vnet", type=int, default=4)
    parser.add_argument("--buffer-depth", type=int, default=1)
    parser.add_argument("--packet-flits", type=int, default=1)
    parser.add_argument("--router-latency", type=int, default=1)
    parser.add_argument("--mesh-link-latency", type=int, default=1)
    parser.add_argument(
        "--express-latency-mode", choices=["topology", "length-aware"],
        default="topology",
    )
    parser.add_argument("--express-wire-per-cycle", type=int, default=4)
    parser.add_argument("--wire-budget", type=int, default=64)
    parser.add_argument("--max-degree", type=int, default=1)
    parser.add_argument("--min-wire-length", type=int, default=3)
    parser.add_argument(
        "--save-finalists", type=int, default=0,
        help="also write the top N simulation-evaluated placements",
    )
    parser.add_argument(
        "--validation-top-n", type=int, default=0,
        help=("independently re-evaluate this many search elites plus every "
              "initial topology; zero preserves the legacy one-stage search"),
    )
    parser.add_argument("--validation-seeds", nargs="+", type=int,
                        default=[3, 4])
    parser.add_argument("--validation-warmup-cycles", type=int, default=5000)
    parser.add_argument("--validation-measurement-cycles", type=int,
                        default=30000)
    parser.add_argument(
        "--incumbent-policy", choices=["utility", "pareto", "curve-guardrail"],
        default="utility",
        help=("final validation selection rule; the first --initial is the "
              "incumbent when pareto is selected"),
    )
    parser.add_argument("--pareto-throughput-epsilon", type=float,
                        default=0.0)
    parser.add_argument("--pareto-latency-epsilon", type=float, default=0.0)
    parser.add_argument(
        "--max-curve-throughput-regression", type=float, default=0.0025,
        help=("maximum relative accepted-throughput regression allowed at "
              "any sampled rate by the curve-guardrail validation policy"),
    )
    args = parser.parse_args()
    if len(args.traffics) == 1 and len(args.rates) > 1:
        # A concise, auditable way to optimize one workload over a complete
        # injection-rate curve.  Previously callers had to repeat the traffic
        # name once per rate, which made configs unnecessarily error-prone.
        args.traffics *= len(args.rates)
    elif len(args.rates) == 1:
        args.rates *= len(args.traffics)
    if len(args.rates) != len(args.traffics):
        parser.error("--rates must have length 1 or match --traffics")
    if args.iterations < 1 or args.restarts < 1 or args.workers < 1:
        parser.error("iteration/restart/worker counts must be positive")
    if args.mode.startswith("sa-") and not args.binary.exists():
        parser.error(f"standalone binary does not exist: {args.binary}")

    initials = [load_placement(path) for path in args.initial]
    for edges in initials:
        validate_placement(
            args.dimension, edges, args.wire_budget, args.max_degree,
            args.min_wire_length,
        )
    demands = [traffic_demand(name, args.dimension) for name in args.traffics]
    mutator = PlacementMutator(
        demands, args.seed, args.proposal_pool,
        wire_budget=args.wire_budget, max_degree=args.max_degree,
        min_wire_length=args.min_wire_length, dimension=args.dimension,
        latency_model=("length-aware"
                       if args.express_latency_mode == "length-aware"
                       else "ideal"),
        express_wire_per_cycle=args.express_wire_per_cycle,
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="express-v6-search-") as temp:
        if args.mode.startswith("sa-"):
            evaluator = SimulationEvaluator(
                args.binary, args.traffics, args.rates, args.traffic_seeds,
                args.warmup_cycles, args.measurement_cycles, args.workers,
                Path(temp),
                reservation_weight=args.reservation_weight,
                vc_pressure_weight=args.vc_pressure_weight,
                express_info_mode=args.express_info_mode,
                express_info_period=args.express_info_period,
                express_info_delay=args.express_info_delay,
                express_info_bits=args.express_info_bits,
                express_admission_fraction=args.express_admission_fraction,
                express_reservation_mode=args.express_reservation_mode,
                latency_weight=args.latency_weight,
                worst_throughput_weight=args.worst_throughput_weight,
                wire_budget=args.wire_budget,
                max_degree=args.max_degree,
                min_wire_length=args.min_wire_length,
                dimension=args.dimension,
                source_route_candidates=args.source_route_candidates,
                retain_mesh_candidate=args.retain_mesh_candidate,
                escape_timeout=args.escape_timeout,
                continue_after_ni_watchdog=args.continue_after_ni_watchdog,
                vcs_per_vnet=args.vcs_per_vnet,
                buffer_depth=args.buffer_depth,
                packet_flits=args.packet_flits,
                router_latency=args.router_latency,
                mesh_link_latency=args.mesh_link_latency,
                express_latency_mode=args.express_latency_mode,
                express_wire_per_cycle=args.express_wire_per_cycle,
            )
            if args.mode == "sa-tempering":
                best, best_result, history = run_tempering(
                    initials, evaluator, mutator, args,
                )
            else:
                best, best_result, history = run_chain(
                    initials, evaluator, mutator, args,
                    reheat=args.mode == "sa-reheat",
                )
            extra = {"evaluated_placements": len(evaluator.cache)}
        else:
            evaluator = FlowEvaluator(
                demands, max_commodities=args.flow_max_commodities,
                rounds=args.flow_rounds,
            )
            best, best_result, history = run_flow_search(
                initials, evaluator, mutator, args,
                large=args.mode == "flow-lns",
            )
            extra = {
                "evaluated_placements": len(evaluator.cache),
                "full_flow_score": evaluator.full_score(best),
            }

    search_best_result = best_result
    validation_archive = None
    finalist_cache = evaluator.cache
    if (isinstance(evaluator, SimulationEvaluator)
            and args.validation_top_n > 0):
        best, best_result, validation_archive, finalist_cache = validate_archive(
            initials, evaluator, best, args,
        )

    metadata = {
        "mode": args.mode,
        "traffics": args.traffics,
        "rates": args.rates,
        "search_seed": args.seed,
        "best_search_result": search_best_result,
        "selected_result": best_result,
        **extra,
    }
    if validation_archive is not None:
        metadata["selection_stage"] = "independent_validation"
        metadata["validation_archive"] = validation_archive
    (args.output_dir / "best.json").write_text(
        json.dumps(placement_record(
                       f"v6_{args.mode}", best, metadata,
                       dimension=args.dimension,
                       wire_budget=args.wire_budget,
                       max_degree=args.max_degree,
                       min_wire_length=args.min_wire_length,
                       latency_model=("length-aware"
                                      if args.express_latency_mode == "length-aware"
                                      else "ideal"),
                       express_wire_per_cycle=args.express_wire_per_cycle),
                   indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (args.output_dir / "search.json").write_text(
        json.dumps({
            "config": {
                key: ([str(item) for item in value] if isinstance(value, list)
                      and value and isinstance(value[0], Path)
                      else str(value) if isinstance(value, Path) else value)
                for key, value in vars(args).items()
            },
            "metadata": metadata,
            "history": history,
        }, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if args.save_finalists and isinstance(evaluator, SimulationEvaluator):
        ranked = sorted(
            finalist_cache.items(),
            key=lambda item: item[1]["utility"], reverse=True,
        )[:args.save_finalists]
        for index, (key, result) in enumerate(ranked, 1):
            edges = [ExpressEdge(*item) for item in key]
            record = placement_record(
                f"v8_{args.mode}_finalist_{index}", edges,
                {"rank": index, "search_result": result},
                dimension=args.dimension,
                wire_budget=args.wire_budget,
                max_degree=args.max_degree,
                min_wire_length=args.min_wire_length,
                latency_model=("length-aware"
                               if args.express_latency_mode == "length-aware"
                               else "ideal"),
                express_wire_per_cycle=args.express_wire_per_cycle,
            )
            (args.output_dir / f"finalist_{index:02d}.json").write_text(
                json.dumps(record, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
    print(f"best topology: {args.output_dir / 'best.json'}")


if __name__ == "__main__":
    main()
