"""Rerun the Phase 3 matrix with warmup and explicit measurement stats."""

import argparse
import csv
import json
import math
import re
import subprocess
import os
import sysconfig
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GEM5 = ROOT
GEM5_BINARY = GEM5 / "build/Garnet_standalone/gem5.opt"
RESULTS = Path(__file__).resolve().parent / "results" / "phase3_measurement_v2"
TOPOLOGIES = {
    "mesh": "mesh.json",
    "random": "random.json",
    "aspl": "aspl.json",
    "bitcomp_aspl": "bitcomp_aspl.json",
    "tornado_aspl": "tornado_aspl.json",
}
TRAFFICS = ("uniform_random", "cutstress", "cutstress_bidirectional",
            "hotspot", "bit_complement", "tornado", "soc_heterogeneous")
ROUTINGS = ("committed",)

# Garnet runs at the default 2 GHz Ruby clock while gem5 ticks are 1 ps.
# GarnetNetwork latency statistics are accumulated in ticks, so one network
# cycle is 500 ticks. Keep this explicit so latency and throughput use the
# same network-cycle unit.
RUBY_CLOCK_PERIOD_TICKS = 500.0
RESULT_SCHEMA_VERSION = 10


def traffic_mapping_stats(text, traffic, node_count=64):
    """Summarize the source/destination router matrix seen by Garnet.

    Deterministic synthetic traffic must remain deterministic after Ruby's
    physical-address-to-directory mapping.  The generic gem5 memory-channel
    XOR hash must be disabled for this tester, otherwise changing address tag
    bits silently change the requested destination over time.
    """
    pattern = re.compile(
        r"^system\.ruby\.network\.ctrl_traffic_distribution\.n(\d+)\.n(\d+)"
        r"\s+(\S+)", re.M)
    radix = round(node_count ** 0.5)
    if radix * radix != node_count:
        raise ValueError("ExpressMesh synthetic traffic requires a square grid")
    matrix = [[0.0 for _ in range(node_count)]
              for _ in range(node_count)]
    for source, destination, value in pattern.findall(text):
        matrix[int(source)][int(destination)] = float(value)

    active_sources = [source for source in range(node_count)
                      if sum(matrix[source]) > 0]
    active_destinations = [
        sum(value > 0 for value in matrix[source])
        for source in active_sources
    ]
    expected = {}
    if traffic == "bit_complement":
        expected = {source: node_count - 1 - source
                    for source in range(node_count)}
    elif traffic == "tornado":
        expected = {
            source: (source // radix) * radix +
                    (source % radix + math.ceil(radix / 2) - 1) % radix
            for source in range(node_count)
        }
    elif traffic == "cutstress":
        expected = {
            source: (source // radix) * radix +
                    (radix - 1 - source % radix)
            for source in range(node_count) if source % radix < radix // 2
        }
    elif traffic == "cutstress_bidirectional":
        expected = {
            source: (source // radix) * radix +
                    (radix - 1 - source % radix)
            for source in range(node_count)
        }

    expected_total = sum(sum(matrix[source]) for source in expected)
    expected_hits = sum(matrix[source][destination]
                        for source, destination in expected.items())
    unexpected_source_packets = sum(
        sum(matrix[source]) for source in range(node_count)
        if source not in expected
    ) if expected else 0.0
    return {
        "traffic_matrix_packet_total": sum(map(sum, matrix)),
        "traffic_matrix_active_source_count": len(active_sources),
        "traffic_matrix_mean_active_destinations": (
            sum(active_destinations) / len(active_destinations)
            if active_destinations else 0.0
        ),
        "traffic_expected_destination_fraction": (
            expected_hits / expected_total if expected_total else None
        ),
        "traffic_unexpected_source_packets": unexpected_source_packets,
    }


def run_tag(spec, source_route=False, source_route_policy=0, no_escape=False,
            random_placement_seed=1, dimension=8,
            source_route_candidates=8, router_latency=1,
            mesh_link_latency=1, vcs_per_vnet=4,
            buffers_per_data_vc=4, buffers_per_ctrl_vc=1,
            inj_vnet=0, escape_timeout=32, source_mesh_routing="xy",
            retain_mesh_candidate=False, packet_flits=1, drain_cycles=0):
    topology, routing, traffic, rate, seed = spec
    mode = "_source_route" if source_route else ""
    if source_route and source_route_policy:
        mode += {3: "_random_candidate",
                 4: "_pressure",
                 5: "_express_dijkstra",
                 6: "_global_dijkstra"}[source_route_policy]
    if no_escape:
        mode += "_no_escape"
    if dimension != 8:
        mode += f"_n{dimension}"
    if source_route_candidates != 8:
        mode += f"_k{source_route_candidates}"
    if source_mesh_routing != "xy":
        mode += f"_mesh{source_mesh_routing}"
    if retain_mesh_candidate:
        mode += "_keepmesh"
    if packet_flits != 1:
        mode += f"_pf{packet_flits}"
    if drain_cycles:
        mode += f"_drain{drain_cycles}"
    hardware = (
        router_latency, mesh_link_latency, vcs_per_vnet,
        buffers_per_data_vc, buffers_per_ctrl_vc, inj_vnet, escape_timeout,
    )
    if hardware != (1, 1, 4, 4, 1, 0, 32):
        mode += (
            f"_rl{router_latency}_ml{mesh_link_latency}_vc{vcs_per_vnet}"
            f"_bd{buffers_per_data_vc}_bc{buffers_per_ctrl_vc}"
            f"_vn{inj_vnet}_et{escape_timeout}"
        )
    placement = (f"_p{random_placement_seed}"
                 if topology == "random" else "")
    return f"{topology}_{routing}_{traffic}_r{rate:.3f}_s{seed}{placement}{mode}"


def configurable_suffix(
        reservation_weight, vc_pressure_weight, express_budget,
        express_max_degree, express_min_wire_length, express_info_mode,
        express_info_period, express_info_delay, express_info_bits,
        express_admission_fraction, express_reservation_mode):
    """Give every behavior-affecting CLI setting a distinct run directory."""
    return (
        f"_qw{vc_pressure_weight:g}_rw{reservation_weight:g}"
        f"_b{express_budget}_deg{express_max_degree}"
        f"_min{express_min_wire_length}_info{express_info_mode}"
        f"_p{express_info_period}_d{express_info_delay}"
        f"_bits{express_info_bits}_adm{express_admission_fraction:g}"
        f"_res{express_reservation_mode}"
    )


def stat_values(text, name):
    match = re.search(rf"^{re.escape(name)}\s+(.+)$", text, re.M)
    if not match:
        raise ValueError(f"missing statistic {name}")
    payload = match.group(1).split("#", 1)[0]
    payload = payload.rsplit("(", 1)[0]
    return [float(value) for value in payload.replace("|", " ").split()]


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


def sum_scalar_suffix(text, suffix, allow_missing=False, node_count=64):
    pattern = rf"^system\.cpu\d+\.{re.escape(suffix)}\s+(\S+)"
    values = [float(value) for value in re.findall(pattern, text, re.M)]
    if not values and allow_missing:
        return 0.0
    if len(values) != node_count:
        raise ValueError(
            f"expected {node_count} {suffix} statistics, found {len(values)}"
        )
    return sum(values)


def run_one(spec, warmup_cycles, measurement_cycles, deadlock_threshold,
            source_route=False, source_route_policy=0, no_escape=False,
            random_placement_seed=1, topology_dir=None,
            reservation_weight=0.5, vc_pressure_weight=1.0,
            express_budget=16, express_max_degree=1,
            express_min_wire_length=3, express_info_mode="instant",
            express_info_period=1, express_info_delay=0,
            express_info_bits=0, express_admission_fraction=1.0,
            express_reservation_mode="instant", dimension=8,
            source_route_candidates=8, router_latency=1,
            mesh_link_latency=1, vcs_per_vnet=4,
            buffers_per_data_vc=4, buffers_per_ctrl_vc=1,
            inj_vnet=0, escape_timeout=32, source_mesh_routing="xy",
            retain_mesh_candidate=False, packet_flits=1,
            drain_cycles=0, route_cache_dir=None):
    if packet_flits < 1 or 64 % packet_flits:
        raise ValueError("packet_flits must be a positive divisor of 64")
    topology, routing, traffic, rate, seed = spec
    tag = run_tag(spec, source_route, source_route_policy, no_escape,
                  random_placement_seed, dimension,
                  source_route_candidates, router_latency,
                  mesh_link_latency, vcs_per_vnet, buffers_per_data_vc,
                  buffers_per_ctrl_vc, inj_vnet, escape_timeout,
                  source_mesh_routing, retain_mesh_candidate, packet_flits,
                  drain_cycles)
    tag += configurable_suffix(
        reservation_weight, vc_pressure_weight, express_budget,
        express_max_degree, express_min_wire_length, express_info_mode,
        express_info_period, express_info_delay, express_info_bits,
        express_admission_fraction, express_reservation_mode,
    )
    outdir = RESULTS / "runs" / tag
    outdir.mkdir(parents=True, exist_ok=True)
    placement_dir = topology_dir
    if placement_dir is None:
        placement_dir = (Path(__file__).resolve().parent / "results" /
                         (f"phase1_random_seed{random_placement_seed}"
                          if topology == "random" and random_placement_seed != 1
                          else "phase1"))
    topology_file = placement_dir / TOPOLOGIES[topology]
    command = [
        str(GEM5_BINARY),
        f"--outdir={outdir}",
        "configs/example/garnet_synth_traffic.py",
        "--network=garnet", f"--num-cpus={dimension * dimension}",
        f"--num-dirs={dimension * dimension}",
        # The synthetic tester embeds the requested directory in address bits
        # 6--11.  gem5's general memory-controller default XORs those bits
        # with changing tag bits and destroys deterministic traffic patterns.
        "--xor-low-bit=0",
        "--topology=ExpressMesh", f"--mesh-rows={dimension}",
        "--routing-algorithm=2", f"--router-latency={router_latency}",
        f"--link-latency={mesh_link_latency}",
        f"--vcs-per-vnet={vcs_per_vnet}",
        f"--link-width-bits={128 if packet_flits == 1 else 64 // packet_flits}",
        f"--buffers-per-data-vc={buffers_per_data_vc}",
        f"--buffers-per-ctrl-vc={buffers_per_ctrl_vc}",
        f"--inj-vnet={inj_vnet}",
        f"--garnet-deadlock-threshold={deadlock_threshold}",
        f"--warmup-cycles={warmup_cycles}",
        f"--measurement-cycles={measurement_cycles}",
        f"--drain-cycles={drain_cycles}",
        f"--synthetic={traffic}", f"--injectionrate={rate}",
        f"--traffic-seed={seed}", f"--express-links-file={topology_file}",
        f"--express-budget={express_budget}",
        f"--express-max-degree={express_max_degree}",
        f"--express-min-wire-length={express_min_wire_length}",
        f"--express-source-route-candidates={source_route_candidates}",
        f"--express-source-mesh-routing={source_mesh_routing}",
        f"--express-escape-timeout={escape_timeout}",
        f"--express-reservation-weight={reservation_weight}",
        f"--express-vc-pressure-weight={vc_pressure_weight}",
        f"--express-info-mode={express_info_mode}",
        f"--express-info-period={express_info_period}",
        f"--express-info-delay={express_info_delay}",
        f"--express-info-bits={express_info_bits}",
        f"--express-admission-fraction={express_admission_fraction}",
        f"--express-reservation-mode={express_reservation_mode}",
    ]
    if source_route:
        command.append("--express-source-route")
        command.extend(["--express-source-route-policy", str(source_route_policy)])
    if no_escape:
        command.append("--express-no-escape")
    if retain_mesh_candidate:
        command.append("--express-retain-mesh-candidate")
    if route_cache_dir is not None:
        command.append(f"--express-route-cache-dir={Path(route_cache_dir).resolve()}")
    if drain_cycles:
        # Tester and Ruby clocks are 1 GHz and 2 GHz respectively.
        command.append(
            f"--injection-stop-cycles={(warmup_cycles + measurement_cycles) // 2}"
        )
    with (outdir / "run.log").open("w", encoding="utf-8") as log:
        env = os.environ.copy()
        python_lib = sysconfig.get_config_var("LIBDIR")
        if python_lib:
            env["LD_LIBRARY_PATH"] = (
                python_lib + ":" + env.get("LD_LIBRARY_PATH", "")
            )
        result = subprocess.run(command, cwd=GEM5, stdout=log,
                                stderr=subprocess.STDOUT, check=False, env=env)
    stats = (outdir / "stats.txt").read_text(encoding="utf-8")
    run_log = (outdir / "run.log").read_text(encoding="utf-8")
    tick_matches = re.findall(r"Exiting @ tick (\d+)", run_log)
    end_tick = int(tick_matches[-1]) if tick_matches else None
    deadlock_match = re.search(r"Possible network deadlock .*? at time: (\d+)", run_log)
    deadlock_tick = int(deadlock_match.group(1)) if deadlock_match else None
    deadlock_vcs = []
    for match in re.finditer(
        r"^EXPRESS_DEADLOCK_VC router=(\d+) inport=(\d+) "
        r"in_dir=(\S+) vc=(\d+) outport=(-?\d+) out_dir=(\S+) "
        r"outvc=(-?\d+) src=(-?\d+) dest=(-?\d+) escape=(\d+)$",
        run_log, re.M,
    ):
        values = [int(value) if index not in (2, 5) else value
                  for index, value in enumerate(match.groups())]
        deadlock_vcs.append(dict(zip(
            ("router", "inport", "in_direction", "vc", "outport",
             "out_direction", "outvc", "source", "destination", "escape"),
            values,
        )))
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
    node_count = dimension * dimension
    offered_requests = sum_scalar_suffix(
        stats, "offeredRequests", allow_missing_stats, node_count)
    generated_requests = sum_scalar_suffix(
        stats, "generatedRequests", allow_missing_stats, node_count)
    source_blocked_offers = sum_scalar_suffix(
        stats, "sourceBlockedOffers", allow_missing_stats, node_count)
    attempts = sum_scalar_suffix(
        stats, "injectionAttempts", allow_missing_stats, node_count)
    initial_retries = sum_scalar_suffix(
        stats, "initialSendRetries", allow_missing_stats, node_count)
    link_util = stat_values_or_zero(stats, prefix + "express_mesh_int_link_utilization")
    mean = sum(link_util) / len(link_util)
    variance = sum((value - mean) ** 2 for value in link_util) / len(link_util)
    ordered = sorted(link_util)
    denominator = float(node_count) * measurement_cycles
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
    info_queries = stat_scalar_or_zero(stats, prefix + "express_info_queries")
    info_queries_before_event = stat_scalar_or_zero(
        stats, prefix + "express_info_queries_before_periodic_event")
    info_queries_after_entry_router = stat_scalar_or_zero(
        stats, prefix + "express_info_queries_after_entry_router_wakeup")
    info_snapshots_before_event = stat_scalar_or_zero(
        stats, prefix + "express_info_snapshots_before_periodic_event")
    info_snapshot_router_wakeups = stat_scalar_or_zero(
        stats, prefix + "express_info_snapshot_router_wakeups_sum")
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
    mapping_stats = traffic_mapping_stats(stats, traffic, node_count)
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
        "deadlock_vcs": deadlock_vcs,
        "topology": topology, "random_placement_seed": random_placement_seed,
        "dimension": dimension,
        "source_route_candidates": source_route_candidates,
        "source_mesh_routing": source_mesh_routing,
        "retain_mesh_candidate": retain_mesh_candidate,
        "packet_flits": packet_flits,
        "drain_cycles": drain_cycles,
        "router_latency": router_latency,
        "mesh_link_latency": mesh_link_latency,
        "vcs_per_vnet": vcs_per_vnet,
        "buffers_per_data_vc": buffers_per_data_vc,
        "buffers_per_ctrl_vc": buffers_per_ctrl_vc,
        "inj_vnet": inj_vnet,
        "escape_timeout": escape_timeout,
        "topology_file": str(topology_file),
        "express_budget": express_budget,
        "express_max_degree": express_max_degree,
        "express_min_wire_length": express_min_wire_length,
        "reservation_weight": reservation_weight,
        "express_vc_weight": vc_pressure_weight,
        "express_info_mode": express_info_mode,
        "express_info_period": express_info_period,
        "express_info_delay": express_info_delay,
        "express_info_bits": express_info_bits,
        "express_admission_fraction": express_admission_fraction,
        "express_reservation_mode": express_reservation_mode,
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
        "express_info_queries": info_queries,
        "express_info_queries_before_periodic_event":
            info_queries_before_event,
        "express_info_queries_before_periodic_fraction":
            info_queries_before_event / info_queries if info_queries else 0.0,
        "express_info_queries_after_entry_router_wakeup":
            info_queries_after_entry_router,
        "express_info_queries_after_entry_router_fraction":
            info_queries_after_entry_router / info_queries
            if info_queries else 0.0,
        "express_info_snapshots_before_periodic_event":
            info_snapshots_before_event,
        "express_info_snapshot_router_wakeups_per_measurement_cycle":
            info_snapshot_router_wakeups / measurement_cycles,
        "express_info_true_q_mean": stat_scalar_or_zero(
            stats, prefix + "express_info_true_q_sum") / info_queries
        if info_queries else 0.0,
        "express_info_observed_q_mean": stat_scalar_or_zero(
            stats, prefix + "express_info_observed_q_sum") / info_queries
        if info_queries else 0.0,
        "express_info_true_r_mean": stat_scalar_or_zero(
            stats, prefix + "express_info_true_r_sum") / info_queries
        if info_queries else 0.0,
        "express_info_observed_r_mean": stat_scalar_or_zero(
            stats, prefix + "express_info_observed_r_sum") / info_queries
        if info_queries else 0.0,
        "express_info_local_pending_mean": stat_scalar_or_zero(
            stats, prefix + "express_info_local_pending_sum") / info_queries
        if info_queries else 0.0,
        "reservation_registration_messages": stat_scalar_or_zero(
            stats, prefix + "reservation_registration_messages"),
        "reservation_registration_acks": stat_scalar_or_zero(
            stats, prefix + "reservation_registration_acks"),
        "reservation_registration_cancels": stat_scalar_or_zero(
            stats, prefix + "reservation_registration_cancels"),
        "reservation_late_registrations": stat_scalar_or_zero(
            stats, prefix + "reservation_late_registrations"),
        "max_link_utilization": max(link_util),
        "p95_link_utilization": ordered[int(0.95 * (len(ordered) - 1))],
        "link_utilization_cv": variance ** 0.5 / mean if mean else 0.0,
        "run_dir": str(outdir),
        "host_seconds": stat_scalar_or_zero(stats, "hostSeconds"),
    }
    row.update(mapping_stats)
    if (traffic in {"bit_complement", "tornado", "cutstress",
                    "cutstress_bidirectional"} and
            termination_reason != "deadlock_panic" and
            (mapping_stats["traffic_expected_destination_fraction"] != 1.0 or
             mapping_stats["traffic_unexpected_source_packets"] != 0.0)):
        raise RuntimeError(
            f"{tag} did not preserve the requested deterministic traffic; "
            f"mapping stats: {mapping_stats}"
        )
    (outdir / "result.json").write_text(
        json.dumps(row, indent=2) + "\n", encoding="utf-8")
    return row


def cached_result(spec, warmup_cycles, measurement_cycles, deadlock_threshold,
                  source_route=False, source_route_policy=0, no_escape=False,
                  random_placement_seed=1, topology_dir=None,
                  reservation_weight=0.5, vc_pressure_weight=1.0,
                  express_budget=16, express_max_degree=1,
                  express_min_wire_length=3, express_info_mode="instant",
                  express_info_period=1, express_info_delay=0,
                  express_info_bits=0, express_admission_fraction=1.0,
                  express_reservation_mode="instant", dimension=8,
                  source_route_candidates=8, router_latency=1,
                  mesh_link_latency=1, vcs_per_vnet=4,
                  buffers_per_data_vc=4, buffers_per_ctrl_vc=1,
                  inj_vnet=0, escape_timeout=32,
                  source_mesh_routing="xy", retain_mesh_candidate=False,
                  packet_flits=1, drain_cycles=0, route_cache_dir=None):
    topology, routing, traffic, rate, seed = spec
    tag = run_tag(spec, source_route, source_route_policy, no_escape,
                  random_placement_seed, dimension,
                  source_route_candidates, router_latency,
                  mesh_link_latency, vcs_per_vnet, buffers_per_data_vc,
                  buffers_per_ctrl_vc, inj_vnet, escape_timeout,
                  source_mesh_routing, retain_mesh_candidate, packet_flits,
                  drain_cycles)
    tag += configurable_suffix(
        reservation_weight, vc_pressure_weight, express_budget,
        express_max_degree, express_min_wire_length, express_info_mode,
        express_info_period, express_info_delay, express_info_bits,
        express_admission_fraction, express_reservation_mode,
    )
    placement_dir = topology_dir
    if placement_dir is None:
        placement_dir = (Path(__file__).resolve().parent / "results" /
                         (f"phase1_random_seed{random_placement_seed}"
                          if topology == "random" and
                          random_placement_seed != 1 else "phase1"))
    topology_file = placement_dir / TOPOLOGIES[topology]
    path = RESULTS / "runs" / tag / "result.json"
    if not path.exists():
        return None
    row = json.loads(path.read_text(encoding="utf-8"))
    if (row.get("result_schema_version") != RESULT_SCHEMA_VERSION or
            row.get("source_route", False) != source_route or
            row.get("source_route_policy", 0) != source_route_policy or
            row.get("dimension", 8) != dimension or
            row.get("source_route_candidates", 8) !=
                source_route_candidates or
            row.get("router_latency", 1) != router_latency or
            row.get("mesh_link_latency", 1) != mesh_link_latency or
            row.get("vcs_per_vnet", 4) != vcs_per_vnet or
            row.get("buffers_per_data_vc", 4) != buffers_per_data_vc or
            row.get("buffers_per_ctrl_vc", 1) != buffers_per_ctrl_vc or
            row.get("inj_vnet", 0) != inj_vnet or
            row.get("escape_timeout", 32) != escape_timeout or
            row.get("source_mesh_routing", "xy") != source_mesh_routing or
            row.get("retain_mesh_candidate", False) != retain_mesh_candidate or
            row.get("packet_flits", 1) != packet_flits or
            row.get("drain_cycles", 0) != drain_cycles or
            row.get("random_placement_seed", 1) != random_placement_seed or
            row.get("topology_file") != str(topology_file) or
            row.get("reservation_weight", 0.5) != reservation_weight or
            row.get("express_vc_weight", 1.0) != vc_pressure_weight or
            row.get("express_budget", 16) != express_budget or
            row.get("express_max_degree", 1) != express_max_degree or
            row.get("express_min_wire_length", 3) != express_min_wire_length or
            row.get("express_info_mode", "instant") != express_info_mode or
            row.get("express_info_period", 1) != express_info_period or
            row.get("express_info_delay", 0) != express_info_delay or
            row.get("express_info_bits", 0) != express_info_bits or
            row.get("express_admission_fraction", 1.0) !=
                express_admission_fraction or
            row.get("express_reservation_mode", "instant") !=
                express_reservation_mode or
            row.get("escape_enabled", True) != (not no_escape) or
            row.get("warmup_cycles") != warmup_cycles or
            row.get("measurement_cycles") != measurement_cycles or
            row.get("garnet_deadlock_threshold") != deadlock_threshold):
        return None
    return row


def main():
    global RESULTS
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
    parser.add_argument(
        "--source-route", action=argparse.BooleanOptionalAction, default=True,
        help="commit one selected top-K route at injection (required)",
    )
    parser.add_argument("--source-route-policy", type=int,
                        choices=[0, 3, 4, 5, 6], default=4,
                        help=("0 static, 3 random top-K candidate, "
                              "4 q/r pressure-aware, 5 express Dijkstra, "
                              "6 global-pressure Dijkstra"))
    parser.add_argument("--source-route-candidates", type=int, default=8,
                        help="number K of source-route candidates per pair")
    parser.add_argument("--source-mesh-routing", choices=["xy", "adaptive"],
                        default="xy")
    parser.add_argument("--retain-mesh-candidate", action="store_true")
    parser.add_argument("--no-escape", action="store_true",
                        help="use all four VCs as adaptive VCs")
    parser.add_argument("--random-placement-seed", type=int, default=1,
                        help="placement seed for the random topology")
    parser.add_argument("--topology-dir", type=Path,
                        help="directory containing the selected topology JSON files")
    parser.add_argument("--results", type=Path, default=RESULTS,
                        help="output directory (defaults to phase3_measurement_v2)")
    parser.add_argument("--r-weight", "--reservation-weight",
                        dest="reservation_weight", type=float, default=0.6)
    parser.add_argument("--q-weight", "--express-vc-weight",
                        dest="express_vc_weight", type=float, default=1.0)
    parser.add_argument("--dimension", type=int, default=8,
                        help="side length of the square ExpressMesh")
    parser.add_argument("--express-budget", type=int, default=32)
    parser.add_argument("--express-max-degree", type=int, default=1)
    parser.add_argument("--express-min-wire-length", type=int, default=3)
    parser.add_argument(
        "--express-info-mode",
        choices=["instant", "distance-gossip"],
        default="distance-gossip",
    )
    parser.add_argument("--express-info-period", type=int, default=1)
    parser.add_argument("--express-info-delay", type=int, default=1)
    parser.add_argument("--express-info-bits", type=int, default=4)
    parser.add_argument("--express-admission-fraction", type=float, default=1.0)
    parser.add_argument(
        "--express-reservation-mode",
        choices=["instant", "registered"],
        default="registered",
    )
    parser.add_argument("--router-latency", type=int, default=1)
    parser.add_argument("--mesh-link-latency", type=int, default=1)
    parser.add_argument("--vcs-per-vnet", type=int, default=4)
    parser.add_argument("--buffers-per-data-vc", type=int, default=4)
    parser.add_argument("--buffers-per-ctrl-vc", type=int, default=1)
    parser.add_argument("--inj-vnet", type=int, choices=[0, 1, 2], default=0,
                        help="vnet 0/1 uses one-flit packets; vnet 2 uses five")
    parser.add_argument("--escape-timeout", type=int, default=32)
    parser.add_argument("--packet-flits", type=int, default=1)
    parser.add_argument("--drain-cycles", type=int, default=0)
    parser.add_argument("--route-cache-dir", type=Path)
    args = parser.parse_args()
    if args.dimension <= 1:
        parser.error("--dimension must be greater than one")
    if args.source_route_candidates <= 0:
        parser.error("--source-route-candidates must be positive")
    if not args.source_route:
        parser.error("the cleaned ExpressMesh Garnet path requires source routing")

    RESULTS = args.results.resolve()
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
            args.random_placement_seed, args.topology_dir,
            args.reservation_weight, args.express_vc_weight,
            args.express_budget, args.express_max_degree,
            args.express_min_wire_length, args.express_info_mode,
            args.express_info_period, args.express_info_delay,
            args.express_info_bits,
            args.express_admission_fraction,
            args.express_reservation_mode, args.dimension,
            args.source_route_candidates, args.router_latency,
            args.mesh_link_latency, args.vcs_per_vnet,
            args.buffers_per_data_vc, args.buffers_per_ctrl_vc,
            args.inj_vnet, args.escape_timeout, args.source_mesh_routing,
            args.retain_mesh_candidate, args.packet_flits,
            args.drain_cycles, args.route_cache_dir) if args.resume else None
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
            args.random_placement_seed, args.topology_dir,
            args.reservation_weight, args.express_vc_weight,
            args.express_budget, args.express_max_degree,
            args.express_min_wire_length, args.express_info_mode,
            args.express_info_period, args.express_info_delay,
            args.express_info_bits,
            args.express_admission_fraction,
            args.express_reservation_mode, args.dimension,
            args.source_route_candidates, args.router_latency,
            args.mesh_link_latency, args.vcs_per_vnet,
            args.buffers_per_data_vc, args.buffers_per_ctrl_vc,
            args.inj_vnet, args.escape_timeout, args.source_mesh_routing,
            args.retain_mesh_candidate, args.packet_flits,
            args.drain_cycles, args.route_cache_dir): spec for spec in pending}
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
