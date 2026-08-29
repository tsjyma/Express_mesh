#!/usr/bin/env python3
"""Simulation-guided, reversible placement search for standalone experiments.

This is deliberately separate from placement.py: it does not replace the
deterministic Greedy algorithms used by V1--V3.  It uses arbitrary legal
express edges and evaluates each mutation with the standalone cycle model.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import random
import subprocess
import tempfile

from express_mesh_project.model import ExpressEdge, GridGraph
from express_mesh_project.placement import candidate_edges, validate_placement


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BINARY = ROOT / "express_mesh_project" / "standalone_noc" / "express_noc"


def load_placement(path: Path) -> list[ExpressEdge]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [ExpressEdge(record["u"], record["v"], record["wire_length"],
                        record["latency"])
            for record in data["express_links"]]


def placement_record(name: str, edges: list[ExpressEdge]) -> dict:
    graph = GridGraph(8, tuple(edges))
    constraints = validate_placement(8, edges, 64, 1, 3)
    return {
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


def placement_key(edges: list[ExpressEdge]):
    return tuple(sorted((edge.u, edge.v, edge.wire_length, edge.latency)
                        for edge in edges))


def mutate(current, pool, rng, budget=64, max_degree=1):
    """Remove 1--3 edges, then randomly refill; retry until cost >= budget-2."""
    for _ in range(256):
        remove_count = rng.randint(1, min(3, len(current)))
        removed = set(rng.sample(range(len(current)), remove_count))
        proposal = [edge for index, edge in enumerate(current)
                    if index not in removed]
        degree = [0] * 64
        keys = set()
        cost = 0
        for edge in proposal:
            degree[edge.u] += 1
            degree[edge.v] += 1
            keys.add(edge.key)
            cost += edge.wire_length
        while True:
            remaining = budget - cost
            legal = [edge for edge in pool
                     if edge.key not in keys
                     and edge.wire_length <= remaining
                     and degree[edge.u] < max_degree
                     and degree[edge.v] < max_degree]
            if not legal:
                break
            exact = [edge for edge in legal if edge.wire_length == remaining]
            # Prefer an exact fill, otherwise avoid consuming the entire
            # mutation budget with a single extremely long diagonal.
            choices = exact or [edge for edge in legal if edge.wire_length <= 7]
            edge = rng.choice(choices or legal)
            proposal.append(edge)
            keys.add(edge.key)
            degree[edge.u] += 1
            degree[edge.v] += 1
            cost += edge.wire_length
        if cost >= budget - 2 and placement_key(proposal) != placement_key(current):
            return sorted(proposal, key=lambda edge: edge.key)
    raise RuntimeError("could not construct a legal placement mutation")


def run_case(binary: Path, topology_path: Path, result_path: Path,
             traffic: str, rate: float, args):
    """Run one traffic/rate case for an already materialized topology."""
    command = [
        str(binary), "--topology-file", str(topology_path),
        "--topology", "sa_candidate", "--routing", "adaptive",
        "--traffic", traffic, "--rate", str(rate),
        "--seed", str(args.traffic_seed),
        "--warmup-cycles", str(args.warmup_cycles),
        "--measurement-cycles", str(args.measurement_cycles),
        "--source-route", "--source-route-policy", "4",
        "--source-route-candidates", "8", "--source-mesh-routing", "xy",
        "--reservation-weight", "0.375", "--express-vc-weight", "0.625",
        "--express-wire-budget", "64", "--express-max-degree", "1",
        "--output", str(result_path),
    ]
    completed = subprocess.run(command, text=True, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, check=False)
    if completed.returncode:
        raise RuntimeError(completed.stdout.strip())
    return json.loads(result_path.read_text(encoding="utf-8"))


def offered_capacity(traffic: str, rate: float) -> float:
    # Tester injection attempts happen every two network cycles. CutStress
    # additionally enables only the left half of the sources.
    return rate * (0.25 if traffic == "cutstress" else 0.5)


def evaluate(binary: Path, edges, args, scratch: Path):
    key = placement_key(edges)
    topology_path = scratch / "topology.json"
    result_path = scratch / "result.json"
    topology_path.write_text(
        json.dumps(placement_record("sa_candidate", edges), indent=2) + "\n",
        encoding="utf-8",
    )
    result = run_case(binary, topology_path, result_path,
                      args.traffic, args.rate, args)
    samples = [(result, args.traffic, args.rate)]
    if args.secondary_traffic:
        secondary = run_case(
            binary, topology_path, scratch / "secondary_result.json",
            args.secondary_traffic, args.secondary_rate, args,
        )
        result["secondary_result"] = secondary
        samples.append((secondary, args.secondary_traffic,
                        args.secondary_rate))
    throughput = sum(
        sample["accepted_throughput"] / offered_capacity(traffic, rate)
        for sample, traffic, rate in samples
    ) / len(samples)
    latency = sum(max(0.0, sample["average_packet_latency_cycles"])
                  for sample, _, _ in samples) / len(samples)
    latency_penalty = 0.005 * math.log1p(latency) / math.log1p(
        args.measurement_cycles)
    utility = throughput - latency_penalty
    return key, utility, result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--initial", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--binary", type=Path, default=DEFAULT_BINARY)
    parser.add_argument("--iterations", type=int, default=100)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--traffic-seed", type=int, default=1)
    traffic_choices = ["uniform_random", "cutstress", "bit_complement", "tornado"]
    parser.add_argument("--traffic", choices=traffic_choices,
                        default="uniform_random")
    parser.add_argument("--rate", type=float, default=0.8)
    parser.add_argument("--secondary-traffic",
                        choices=traffic_choices)
    parser.add_argument("--secondary-rate", type=float, default=0.8)
    parser.add_argument("--warmup-cycles", type=int, default=1000)
    parser.add_argument("--measurement-cycles", type=int, default=5000)
    parser.add_argument("--initial-temperature", type=float, default=0.01)
    parser.add_argument("--final-temperature", type=float, default=0.0002)
    args = parser.parse_args()
    if args.iterations < 1:
        parser.error("--iterations must be positive")
    if not args.binary.exists():
        parser.error(f"standalone binary does not exist: {args.binary}")

    rng = random.Random(args.seed)
    pool = candidate_edges(8, 3, "ideal")
    current = load_placement(args.initial)
    validate_placement(8, current, 64, 1, 3)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    cache = {}
    history = []
    with tempfile.TemporaryDirectory(prefix="express-mesh-sa-") as temp:
        scratch = Path(temp)

        def cached_evaluate(edges):
            key = placement_key(edges)
            if key not in cache:
                _, utility, result = evaluate(args.binary.resolve(), edges,
                                               args, scratch)
                cache[key] = (utility, result)
            return cache[key]

        current_utility, current_result = cached_evaluate(current)
        best = list(current)
        best_utility, best_result = current_utility, current_result
        (args.output_dir / "best_000.json").write_text(
            json.dumps(placement_record("sa_best", best), indent=2) + "\n",
            encoding="utf-8",
        )
        for iteration in range(1, args.iterations + 1):
            fraction = iteration / args.iterations
            temperature = args.initial_temperature * (
                args.final_temperature / args.initial_temperature) ** fraction
            proposal = mutate(current, pool, rng)
            utility, result = cached_evaluate(proposal)
            delta = utility - current_utility
            accepted = delta >= 0 or rng.random() < math.exp(delta / temperature)
            if accepted:
                current, current_utility, current_result = proposal, utility, result
            improved = utility > best_utility
            if improved:
                best, best_utility, best_result = list(proposal), utility, result
                (args.output_dir / f"best_{iteration:03d}.json").write_text(
                    json.dumps(placement_record("sa_best", best), indent=2) + "\n",
                    encoding="utf-8",
                )
            history.append({
                "iteration": iteration,
                "temperature": temperature,
                "accepted": accepted,
                "improved_best": improved,
                "proposal_utility": utility,
                "proposal_throughput": result["accepted_throughput"],
                "proposal_latency": result["average_packet_latency_cycles"],
                "current_utility": current_utility,
                "best_utility": best_utility,
                "best_throughput": best_result["accepted_throughput"],
                "best_latency": best_result["average_packet_latency_cycles"],
            })
            secondary_text = ""
            if result.get("secondary_result"):
                secondary_text = (" secondary_th="
                    f"{result['secondary_result']['accepted_throughput']:.6f}")
            print(f"[{iteration}/{args.iterations}] utility={utility:.6f} "
                  f"current={current_utility:.6f} best={best_utility:.6f} "
                  f"th={result['accepted_throughput']:.6f} "
                  f"lat={result['average_packet_latency_cycles']:.1f}"
                  f"{secondary_text}", flush=True)

    (args.output_dir / "best.json").write_text(
        json.dumps(placement_record("sa_best", best), indent=2) + "\n",
        encoding="utf-8",
    )
    (args.output_dir / "search.json").write_text(json.dumps({
        "config": {key: str(value) if isinstance(value, Path) else value
                   for key, value in vars(args).items()},
        "evaluated_placements": len(cache),
        "best_utility": best_utility,
        "best_result": best_result,
        "history": history,
    }, indent=2) + "\n", encoding="utf-8")
    print(f"Best topology written to {args.output_dir / 'best.json'}")


if __name__ == "__main__":
    main()
