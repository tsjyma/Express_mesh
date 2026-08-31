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
    cutstress_demand,
    hotspot_demand,
    tornado_demand,
    uniform_demand,
)
from express_mesh_project.placement import candidate_edges, validate_placement


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BINARY = ROOT / "express_mesh_project" / "standalone_noc" / "express_noc"
TRAFFIC_NAMES = (
    "uniform_random", "cutstress", "hotspot", "bit_complement", "tornado",
)


def traffic_demand(name: str, n: int = 8):
    return {
        "uniform_random": uniform_demand(n * n),
        "cutstress": cutstress_demand(n),
        "hotspot": hotspot_demand(n),
        "bit_complement": bit_complement_demand(n),
        "tornado": tornado_demand(n),
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
                     wire_budget: int = 64, max_degree: int = 1,
                     min_wire_length: int = 3):
    graph = GridGraph(8)
    constraints = validate_placement(
        8, edges, wire_budget, max_degree, min_wire_length,
    )
    result = {
        "name": name,
        "dimension": 8,
        "node_count": 64,
        "latency_model": "ideal",
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


def _degrees_and_cost(edges):
    degree = [0] * 64
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
                 min_wire_length: int = 3):
        self.rng = random.Random(seed)
        self.wire_budget = wire_budget
        self.max_degree = max_degree
        self.min_wire_length = min_wire_length
        self.pool = candidate_edges(8, min_wire_length, "ideal")
        self.scores = direct_benefit_scores(8, self.pool, demands)
        self.ranked = sorted(
            self.pool,
            key=lambda edge: (-self.scores[edge.key], edge.wire_length, edge.key),
        )
        self.pool_size = min(pool_size, len(self.ranked))

    def _choose_removed(self, current, count, guided):
        if not guided:
            return set(self.rng.sample(range(len(current)), count))
        # Low-demand-benefit links are more likely to be reconsidered, but all
        # links retain a nonzero probability to avoid freezing a bad proxy.
        ranked_indices = sorted(
            range(len(current)),
            key=lambda index: (self.scores.get(current[index].key, 0.0),
                               self.rng.random()),
        )
        window = ranked_indices[:max(count, (len(current) + 1) // 2)]
        return set(self.rng.sample(window, count))

    def _pick_addition(self, legal, strategy):
        exact = [edge for edge in legal if edge[1]]
        choices = exact or legal
        if strategy == "random":
            return self.rng.choice(choices)[0]
        choices.sort(
            key=lambda item: (-self.scores.get(item[0].key, 0.0),
                              item[0].wire_length, item[0].key)
        )
        limit = min(len(choices), 24 if strategy == "guided" else 64)
        # Exponential rank sampling mixes strong demand-aware proposals with
        # occasional exploratory edges.
        rank = min(limit - 1, int(self.rng.expovariate(0.35)))
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
            elif variant == "large":
                remove_count = self.rng.randint(3, min(8, len(current)))
                guided = self.rng.random() < 0.75
                add_strategy = "mixed"
            else:
                raise ValueError(f"unknown mutation variant: {variant}")

            removed = (self._choose_removed(current, remove_count, guided)
                       if remove_count else set())
            proposal = [edge for index, edge in enumerate(current)
                        if index not in removed]
            degree, cost, keys = _degrees_and_cost(proposal)
            while True:
                remaining = self.wire_budget - cost
                source_pool = (self.ranked[:self.pool_size]
                               if add_strategy != "random" else self.pool)
                legal = [
                    (edge, edge.wire_length == remaining)
                    for edge in source_pool
                    if edge.key not in keys
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
                    8, proposal, self.wire_budget, self.max_degree,
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
                 wire_budget=64, max_degree=1, min_wire_length=3):
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
        self.wire_budget = wire_budget
        self.max_degree = max_degree
        self.min_wire_length = min_wire_length
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
            "--source-route-candidates", "8", "--source-mesh-routing", "xy",
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
            "--output", str(output),
        ]
        completed = subprocess.run(
            command, text=True, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, check=False,
        )
        if completed.returncode:
            raise RuntimeError(completed.stdout.strip())
        return json.loads(output.read_text(encoding="utf-8"))

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
                "advanced_sa", edges, wire_budget=self.wire_budget,
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
        utility = fmean(ratios) + 0.25 * min(ratios) - latency_penalty
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
                mutation = ("large" if local_iteration == 1
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
    parser.add_argument("--wire-budget", type=int, default=64)
    parser.add_argument("--max-degree", type=int, default=1)
    parser.add_argument("--min-wire-length", type=int, default=3)
    parser.add_argument(
        "--save-finalists", type=int, default=0,
        help="also write the top N simulation-evaluated placements",
    )
    args = parser.parse_args()
    if len(args.rates) == 1:
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
            8, edges, args.wire_budget, args.max_degree,
            args.min_wire_length,
        )
    demands = [traffic_demand(name) for name in args.traffics]
    mutator = PlacementMutator(
        demands, args.seed, args.proposal_pool,
        wire_budget=args.wire_budget, max_degree=args.max_degree,
        min_wire_length=args.min_wire_length,
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
                wire_budget=args.wire_budget,
                max_degree=args.max_degree,
                min_wire_length=args.min_wire_length,
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

    metadata = {
        "mode": args.mode,
        "traffics": args.traffics,
        "rates": args.rates,
        "search_seed": args.seed,
        "best_search_result": best_result,
        **extra,
    }
    (args.output_dir / "best.json").write_text(
        json.dumps(placement_record(
                       f"v6_{args.mode}", best, metadata,
                       wire_budget=args.wire_budget,
                       max_degree=args.max_degree,
                       min_wire_length=args.min_wire_length),
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
            evaluator.cache.items(),
            key=lambda item: item[1]["utility"], reverse=True,
        )[:args.save_finalists]
        for index, (key, result) in enumerate(ranked, 1):
            edges = [ExpressEdge(*item) for item in key]
            record = placement_record(
                f"v8_{args.mode}_finalist_{index}", edges,
                {"rank": index, "search_result": result},
                wire_budget=args.wire_budget,
                max_degree=args.max_degree,
                min_wire_length=args.min_wire_length,
            )
            (args.output_dir / f"finalist_{index:02d}.json").write_text(
                json.dumps(record, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
    print(f"best topology: {args.output_dir / 'best.json'}")


if __name__ == "__main__":
    main()
