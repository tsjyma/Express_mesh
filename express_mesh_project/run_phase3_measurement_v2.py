"""Rerun the Phase 3 matrix with warmup and explicit measurement stats."""

import argparse
import csv
import json
import re
import subprocess
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GEM5 = ROOT
GEM5_BINARY = GEM5 / "build/Garnet_standalone/gem5.opt"
RESULTS = Path(__file__).resolve().parent / "results" / "phase3_measurement_v2"
TOPOLOGIES = {
    "mesh": "mesh.json",
    "random": "random.json",
    "handcrafted": "handcrafted.json",
    "hybrid": "hybrid.json",
    "hybrid_cutstress": "hybrid_cutstress.json",
}
TRAFFICS = ("uniform_random", "cutstress", "hotspot")
ROUTINGS = ("deterministic", "adaptive")

# Garnet runs at the default 2 GHz Ruby clock while gem5 ticks are 1 ps.
# GarnetNetwork latency statistics are accumulated in ticks, so one network
# cycle is 500 ticks. Keep this explicit so latency and throughput use the
# same network-cycle unit.
RUBY_CLOCK_PERIOD_TICKS = 500.0
RESULT_SCHEMA_VERSION = 5

def run_tag(spec, source_route=False, source_route_policy=0, no_escape=False,
            random_placement_seed=1):
    topology, routing, traffic, rate, seed = spec
    mode = "_source_route" if source_route else ""
    if source_route and source_route_policy:
        mode += {1: "_q", 2: "_qr", 3: "_random_candidate"}[source_route_policy]
    if no_escape:
        mode += "_no_escape"
    placement = f"_p{random_placement_seed}" if topology == "random" else ""
    return f"{topology}_{routing}_{traffic}_r{rate:.3f}_s{seed}{placement}{mode}"


def stat_values(text, name):
    match = re.search(rf"^{re.escape(name)}\s+(.+?)\s+#?\s*\(", text, re.M)
    if not match:
        match = re.search(rf"^{re.escape(name)}\s+(.+)$", text, re.M)
    if not match:
        raise ValueError(f"missing statistic {name}")
    return [float(value) for value in match.group(1).replace("|", " ").split()]


def stat_scalar_or_zero(text, name):
    try:
        return stat_values(text, name)[0]
    except ValueError:
        return 0.0


def stat_values_or_zero(text, name):
    try:
        return stat_values(text, name)
    except ValueError:
        return [0.0]


def sum_scalar_suffix(text, suffix, allow_missing=False):
    pattern = rf"^system\.cpu\d+\.{re.escape(suffix)}\s+(\S+)"
    values = [float(value) for value in re.findall(pattern, text, re.M)]
    if not values and allow_missing:
        return 0.0
    if len(values) != 64:
        raise ValueError(f"expected 64 {suffix} statistics, found {len(values)}")
    return sum(values)


def run_one(spec, warmup_cycles, measurement_cycles, deadlock_threshold,
            source_route=False, source_route_policy=0, no_escape=False,
            random_placement_seed=1):
    topology, routing, traffic, rate, seed = spec
    tag = run_tag(spec, source_route, source_route_policy, no_escape,
                  random_placement_seed)
    outdir = RESULTS / "runs" / tag
    outdir.mkdir(parents=True, exist_ok=True)
    placement_dir = (Path(__file__).resolve().parent / "results" /
                     (f"phase1_random_seed{random_placement_seed}"
                      if topology == "random" and random_placement_seed != 1
                      else "phase1"))
    topology_file = placement_dir / TOPOLOGIES[topology]
    command = [
        str(GEM5_BINARY),
        f"--outdir={outdir}",
        "configs/example/garnet_synth_traffic.py",
        "--network=garnet", "--num-cpus=64", "--num-dirs=64",
        "--topology=ExpressMesh", "--mesh-rows=8", "--routing-algorithm=2",
        "--vcs-per-vnet=4", "--inj-vnet=0",
        f"--garnet-deadlock-threshold={deadlock_threshold}",
        f"--warmup-cycles={warmup_cycles}",
        f"--measurement-cycles={measurement_cycles}",
        f"--synthetic={traffic}", f"--injectionrate={rate}",
        f"--traffic-seed={seed}", f"--express-links-file={topology_file}",
        "--express-adaptive-threshold=0.25", "--express-adaptive-lambda=1.0",
        "--express-detour-ratio=1.5", "--express-escape-timeout=32",
    ]
    if routing == "adaptive":
        command.append("--express-adaptive")
    if source_route:
        command.append("--express-source-route")
        command.extend(["--express-source-route-policy", str(source_route_policy)])
    if no_escape:
        command.append("--express-no-escape")
    with (outdir / "run.log").open("w", encoding="utf-8") as log:
        env = os.environ.copy()
        conda_lib = "/home/jing/miniconda3/lib"
        env["LD_LIBRARY_PATH"] = conda_lib + ":" + env.get("LD_LIBRARY_PATH", "")
        result = subprocess.run(command, cwd=GEM5, stdout=log,
                                stderr=subprocess.STDOUT, check=False, env=env)
    stats = (outdir / "stats.txt").read_text(encoding="utf-8")
    run_log = (outdir / "run.log").read_text(encoding="utf-8")
    tick_matches = re.findall(r"Exiting @ tick (\d+)", run_log)
    end_tick = int(tick_matches[-1]) if tick_matches else None
    deadlock_match = re.search(r"Possible network deadlock .*? at time: (\d+)", run_log)
    deadlock_tick = int(deadlock_match.group(1)) if deadlock_match else None
    termination_reason = ("deadlock_panic" if deadlock_match else
                          "simulate_limit" if end_tick is not None else
                          "nonzero_exit" if result.returncode else "completed")
    if deadlock_tick is not None:
        end_tick = deadlock_tick
    if result.returncode and deadlock_match is None:
        raise RuntimeError(f"{tag} failed; see {outdir / 'run.log'}")
    prefix = "system.ruby.network."
    injected_packets = stat_scalar_or_zero(stats, prefix + "packets_injected::total")
    received_packets = stat_scalar_or_zero(stats, prefix + "packets_received::total")
    injected_flits = stat_scalar_or_zero(stats, prefix + "flits_injected::total")
    received_flits = stat_scalar_or_zero(stats, prefix + "flits_received::total")
    allow_missing_stats = termination_reason == "deadlock_panic"
    offered_requests = sum_scalar_suffix(stats, "offeredRequests", allow_missing_stats)
    generated_requests = sum_scalar_suffix(stats, "generatedRequests", allow_missing_stats)
    source_blocked_offers = sum_scalar_suffix(stats, "sourceBlockedOffers", allow_missing_stats)
    attempts = sum_scalar_suffix(stats, "injectionAttempts", allow_missing_stats)
    initial_retries = sum_scalar_suffix(stats, "initialSendRetries", allow_missing_stats)
    link_util = stat_values_or_zero(stats, prefix + "express_mesh_int_link_utilization")
    mean = sum(link_util) / len(link_util)
    variance = sum((value - mean) ** 2 for value in link_util) / len(link_util)
    ordered = sorted(link_util)
    denominator = 64.0 * measurement_cycles
    reservation_current = stat_values_or_zero(
        stats, prefix + "express_reservation_current")
    reservation_increments = stat_values_or_zero(
        stats, prefix + "express_reservation_increments")
    reservation_decrements = stat_values_or_zero(
        stats, prefix + "express_reservation_decrements")
    planned_bins = stat_values_or_zero(stats, prefix + "source_route_packets_planned")
    delivered_bins = stat_values_or_zero(stats, prefix + "source_route_packets_delivered")
    edge_selected = stat_values_or_zero(stats, prefix + "express_edge_packets_selected")
    q_sum = stat_values_or_zero(stats, prefix + "express_q_sample_sum")
    q_max = stat_values_or_zero(stats, prefix + "express_q_sample_max")
    r_sum = stat_values_or_zero(stats, prefix + "express_r_sample_sum")
    r_max = stat_values_or_zero(stats, prefix + "express_r_sample_max")
    state_samples = stat_scalar_or_zero(stats, prefix + "express_state_sample_count")
    escape_transitions = stat_scalar_or_zero(stats, prefix + "escape_vc_transitions")
    escape_delay_cycles = stat_scalar_or_zero(
        stats, prefix + "escape_transition_delay_ticks") / RUBY_CLOCK_PERIOD_TICKS
    delivered_escape_packets = stat_scalar_or_zero(
        stats, prefix + "delivered_escape_packets")
    planned_bins = (planned_bins + [0.0, 0.0, 0.0])[:3]
    delivered_bins = (delivered_bins + [0.0, 0.0, 0.0])[:3]
    # NoEscape has no timeout-based escape fallback.  A run that reaches the
    # simulation limit without receiving a packet is recorded as no-progress,
    # including cases where a few packets were injected before the stall.
    no_progress = (termination_reason == "deadlock_panic" or
                   (no_escape and received_packets == 0 and end_tick is not None))
    row = {
        "result_schema_version": RESULT_SCHEMA_VERSION,
        "source_route": source_route,
        "source_route_policy": source_route_policy,
        "escape_enabled": not no_escape,
        "no_progress": no_progress,
        "no_progress_cycle": (end_tick / RUBY_CLOCK_PERIOD_TICKS
                               if no_progress and end_tick is not None else None),
        "simulation_end_tick": end_tick,
        "termination_reason": termination_reason,
        "topology": topology, "random_placement_seed": random_placement_seed,
        "routing": routing, "traffic": traffic,
        "configured_injection_rate": rate, "seed": seed,
        "warmup_cycles": warmup_cycles, "measurement_cycles": measurement_cycles,
        "garnet_deadlock_threshold": deadlock_threshold,
        "injection_attempts": attempts, "offered_requests": offered_requests,
        "generated_requests": generated_requests,
        "source_blocked_offers": source_blocked_offers,
        "packets_injected": injected_packets, "packets_received": received_packets,
        "flits_injected": injected_flits, "flits_received": received_flits,
        "initial_send_retries": initial_retries,
        "attempted_throughput": attempts / denominator,
        "actual_offered_throughput": offered_requests / denominator,
        "generated_request_throughput": generated_requests / denominator,
        "injected_throughput": injected_packets / denominator,
        "accepted_throughput": received_packets / denominator,
        "delivery_fraction": received_packets / injected_packets if injected_packets else 0.0,
        "average_packet_latency_cycles": stat_values_or_zero(
            stats, prefix + "average_packet_latency")[0] / RUBY_CLOCK_PERIOD_TICKS,
        "average_hops": stat_values_or_zero(stats, prefix + "average_hops")[0],
        "express_traversals": stat_values_or_zero(stats, prefix + "express_link_traversals")[0],
        "escape_transitions": escape_transitions,
        "escape_traversals": stat_values_or_zero(stats, prefix + "escape_vc_traversals")[0],
        "escape_transition_delay_cycles": escape_delay_cycles,
        "delivered_escape_packets": delivered_escape_packets,
        "delivered_escape_fraction": (delivered_escape_packets / received_packets
                                      if received_packets else 0.0),
        "average_cycles_before_escape": (escape_delay_cycles / escape_transitions
                                         if escape_transitions else 0.0),
        "nonminimal_decisions": stat_values_or_zero(stats, prefix + "nonminimal_route_decisions")[0],
        "reservation_current_total": sum(reservation_current),
        "reservation_current_max": max(reservation_current),
        "reservation_increments_total": sum(reservation_increments),
        "reservation_decrements_total": sum(reservation_decrements),
        "reservation_accounting_delta": sum(reservation_increments) - sum(reservation_decrements),
        "source_route_planned_0_express": planned_bins[0],
        "source_route_planned_1_express": planned_bins[1],
        "source_route_planned_2_express": planned_bins[2],
        "delivered_0_express": delivered_bins[0],
        "delivered_1_express": delivered_bins[1],
        "delivered_2_express": delivered_bins[2],
        "delivered_express_bin_total": sum(delivered_bins),
        "express_edge_packets_selected": edge_selected,
        "express_q_sample_sum": q_sum,
        "express_q_sample_max": q_max,
        "express_r_sample_sum": r_sum,
        "express_r_sample_max": r_max,
        "express_state_sample_count": state_samples,
        "express_q_sample_average": [value / state_samples for value in q_sum]
        if state_samples else [0.0 for value in q_sum],
        "express_r_sample_average": [value / state_samples for value in r_sum]
        if state_samples else [0.0 for value in r_sum],
        "max_link_utilization": max(link_util),
        "p95_link_utilization": ordered[int(0.95 * (len(ordered) - 1))],
        "link_utilization_cv": variance ** 0.5 / mean if mean else 0.0,
        "run_dir": str(outdir),
    }
    (outdir / "result.json").write_text(
        json.dumps(row, indent=2) + "\n", encoding="utf-8")
    return row


def cached_result(spec, warmup_cycles, measurement_cycles, deadlock_threshold,
                  source_route=False, source_route_policy=0, no_escape=False,
                  random_placement_seed=1):
    topology, routing, traffic, rate, seed = spec
    tag = run_tag(spec, source_route, source_route_policy, no_escape,
                  random_placement_seed)
    path = RESULTS / "runs" / tag / "result.json"
    if not path.exists():
        return None
    row = json.loads(path.read_text(encoding="utf-8"))
    if (row.get("result_schema_version") != RESULT_SCHEMA_VERSION or
            row.get("source_route", False) != source_route or
            row.get("source_route_policy", 0) != source_route_policy or
            row.get("random_placement_seed", 1) != random_placement_seed or
            row.get("escape_enabled", True) != (not no_escape) or
            row.get("warmup_cycles") != warmup_cycles or
            row.get("measurement_cycles") != measurement_cycles or
            row.get("garnet_deadlock_threshold") != deadlock_threshold):
        return None
    return row


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--warmup-cycles", type=int, default=20_000)
    parser.add_argument("--measurement-cycles", type=int, default=100_000)
    parser.add_argument("--garnet-deadlock-threshold", type=int,
                        default=50_000)
    parser.add_argument("--rates", type=float, nargs="+",
                        default=[0.02, 0.05, 0.08, 0.12, 0.16])
    parser.add_argument("--seeds", type=int, nargs="+", default=[1, 2, 3])
    parser.add_argument("--topologies", nargs="+", choices=TOPOLOGIES,
                        default=list(TOPOLOGIES))
    parser.add_argument("--routings", nargs="+", choices=ROUTINGS,
                        default=list(ROUTINGS))
    parser.add_argument("--traffics", nargs="+", choices=TRAFFICS,
                        default=list(TRAFFICS))
    parser.add_argument("--resume", action="store_true",
                        help="Reuse per-run results with matching windows")
    parser.add_argument("--source-route", action="store_true",
                        help="Commit the static source route at injection")
    parser.add_argument("--source-route-policy", type=int, choices=[0, 1, 2, 3], default=0,
                        help="0 static, 1 global q, 2 global q+r, 3 random candidate")
    parser.add_argument("--no-escape", action="store_true",
                        help="use all four VCs as adaptive VCs")
    parser.add_argument("--random-placement-seed", type=int, default=1,
                        help="placement seed for the random topology")
    args = parser.parse_args()

    RESULTS.mkdir(parents=True, exist_ok=True)
    specs = [(t, r, f, rate, seed) for t in args.topologies
             for r in args.routings for f in args.traffics
             for rate in args.rates for seed in args.seeds]
    rows = []
    failures = []
    pending = []
    for spec in specs:
        cached = cached_result(
            spec, args.warmup_cycles, args.measurement_cycles,
            args.garnet_deadlock_threshold, args.source_route,
            args.source_route_policy, args.no_escape,
            args.random_placement_seed) if args.resume else None
        if cached is None:
            pending.append(spec)
        else:
            rows.append(cached)
    if rows:
        print(f"Reused {len(rows)} completed runs", flush=True)
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(
            run_one, spec, args.warmup_cycles, args.measurement_cycles,
            args.garnet_deadlock_threshold, args.source_route,
            args.source_route_policy, args.no_escape,
            args.random_placement_seed): spec for spec in pending}
        for index, future in enumerate(as_completed(futures), 1):
            spec = futures[future]
            try:
                row = future.result()
            except Exception as error:
                failure = {
                    "topology": spec[0], "routing": spec[1],
                    "traffic": spec[2], "configured_injection_rate": spec[3],
                    "seed": spec[4], "error": str(error),
                }
                failures.append(failure)
                print(f"[FAILED] {failure}", flush=True)
                continue
            rows.append(row)
            print(f"[{len(rows)}/{len(specs)}] {row['run_dir']}", flush=True)

    rows.sort(key=lambda row: (row["traffic"], row["topology"], row["routing"],
                               row["configured_injection_rate"], row["seed"]))
    (RESULTS / "main_results.json").write_text(
        json.dumps(rows, indent=2) + "\n", encoding="utf-8")
    (RESULTS / "failed_results.json").write_text(
        json.dumps(failures, indent=2) + "\n", encoding="utf-8")
    with (RESULTS / "main_results.csv").open("w", newline="", encoding="utf-8") as output:
        fieldnames = list(rows[0].keys()) if rows else ["source_route", "error"]
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    main()
