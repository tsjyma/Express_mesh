#!/usr/bin/env python3
"""Create compact figures/tables and an informal report for the 20260831 suite."""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
import math
from pathlib import Path
import re
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import Normalize
from matplotlib.lines import Line2D
from matplotlib.patches import FancyArrowPatch, Rectangle
import numpy as np
import pandas as pd

from express_mesh_project.candidates import all_candidates
from express_mesh_project.model import (
    GridGraph,
    ExpressEdge,
    public_metrics,
    soc_heterogeneous_demand,
    uniform_demand,
)


ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "express_mesh_project"
DEFAULT_INPUT = PROJECT / "results" / "20260831" / "standalone_suite"
DEFAULT_REPORT = PROJECT / "standalone_noc" / "EXPERIMENT_SUITE_20260831_RESULTS_ZH.md"
COLORS = {"mesh": "#4c78a8", "random": "#9c9c9c",
          "greedy": "#f58518", "sa": "#54a24b"}
LABELS = {"mesh": "Mesh", "random": "Random expectation",
          "greedy": "Greedy", "sa": "SA"}
MARKERS = {"mesh": "o", "random": "s", "greedy": "^", "sa": "D"}
TRAFFIC_LABELS = {
    "uniform_random": "Uniform", "tornado": "Tornado",
    "bit_complement": "Bit-complement",
    "cutstress_bidirectional": "CutStress-bidir",
    "soc_heterogeneous": "SoC-heterogeneous",
}
SCALING_LABELS = {
    0: "8x8-B32",
    1: "8x8-B64",
    2: "8x8-B128",
    3: "16x16-B256",
    4: "16x16-B256-L4",
    5: "8x8-B32-VC8",
    6: "8x8-B64-L2",
    7: "8x8-B64-2flit",
    8: "16x16-B256-SoC",
}
SCALING_ORDER = (0, 1, 2, 5, 6, 7, 3, 4, 8)


def load_edges(path: Path):
    data = json.loads(path.read_text(encoding="utf-8"))
    dimension = int(data.get("dimension", round(math.sqrt(data["node_count"]))))
    edges = [ExpressEdge(item["u"], item["v"], item["wire_length"],
                         item["latency"]) for item in data["express_links"]]
    return dimension, edges


def static_row(label: str, path: Path):
    dimension, edges = load_edges(path)
    graph = GridGraph(dimension, tuple(edges))
    lengths = [edge.wire_length for edge in edges]
    horizontal = vertical = diagonal = 0
    for edge in edges:
        ux, uy = edge.u % dimension, edge.u // dimension
        vx, vy = edge.v % dimension, edge.v // dimension
        if uy == vy: horizontal += 1
        elif ux == vx: vertical += 1
        else: diagonal += 1
    center = dimension // 2
    vertical_cut = dimension
    horizontal_cut = dimension
    for edge in edges:
        ux, uy = edge.u % dimension, edge.u // dimension
        vx, vy = edge.v % dimension, edge.v // dimension
        vertical_cut += int((ux < center) != (vx < center))
        horizontal_cut += int((uy < center) != (vy < center))
    metrics = public_metrics(graph, uniform_demand(dimension * dimension))
    distances = np.full((graph.node_count, graph.node_count), np.inf)
    np.fill_diagonal(distances, 0)
    for source, neighbors in enumerate(graph.adjacency):
        for destination, latency in neighbors.items():
            distances[source, destination] = latency
    for middle in range(graph.node_count):
        distances = np.minimum(
            distances, distances[:, middle, None] + distances[None, middle, :])
    mask = ~np.eye(graph.node_count, dtype=bool)
    efficiency = float(np.mean(1.0 / distances[mask]))
    routes = all_candidates(graph, k=8)
    express_counts = [route.express_count for candidates in routes.values()
                      for route in candidates]
    return {
        "Topology": label, "links": len(edges), "wire": sum(lengths),
        "length_mean": float(np.mean(lengths)) if lengths else 0,
        "length_min": min(lengths, default=0), "length_max": max(lengths, default=0),
        "horizontal": horizontal, "vertical": vertical, "diagonal": diagonal,
        "ASPL": metrics["aspl"], "diameter": metrics["diameter"],
        "efficiency": efficiency,
        "cut_v": vertical_cut, "cut_h": horizontal_cut,
        "top8_express_mean": float(np.mean(express_counts)) if express_counts else 0,
    }


def topology_paths():
    base = PROJECT / "results" / "20260831" / "placements"
    return {
        "Mesh": PROJECT / "results" / "phase1" / "mesh.json",
        "Uniform ASPL Greedy": base / "b32" / "uniform_random_aspl.json",
        "Uniform SA": (
            PROJECT / "results" / "20260902" / "sa_curve_unified_v2" /
            "uniform_random" / "best.json"
        ),
    }


def curved_link(axis, start, end, *, color, linewidth, radius,
                alpha=1.0, arrow=False, zorder=2):
    """Draw a link as a visible arc instead of hiding it under mesh edges."""
    patch = FancyArrowPatch(
        start, end,
        arrowstyle="-|>" if arrow else "-",
        mutation_scale=6,
        connectionstyle=f"arc3,rad={radius}",
        color=color, linewidth=linewidth, alpha=alpha,
        shrinkA=1.5, shrinkB=1.5, zorder=zorder,
    )
    axis.add_patch(patch)
    return patch


def draw_topologies(paths, output):
    fig, axes = plt.subplots(1, len(paths), figsize=(3.2 * len(paths), 3.3))
    axes = np.atleast_1d(axes)
    for axis, (label, path) in zip(axes, paths.items()):
        n, edges = load_edges(path)
        for y in range(n):
            for x in range(n - 1):
                axis.plot([x, x + 1], [y, y], color="#c7c7c7", lw=.65,
                          zorder=0)
        for x in range(n):
            for y in range(n - 1):
                axis.plot([x, x], [y, y + 1], color="#c7c7c7", lw=.65,
                          zorder=0)
        for index, edge in enumerate(edges):
            start = (edge.u % n, edge.u // n)
            end = (edge.v % n, edge.v // n)
            # Alternate sides so multiple nearby express wires remain legible.
            radius = (.13 if index % 2 == 0 else -.13)
            curved_link(axis, start, end, color="#d62728", linewidth=2.2,
                        radius=radius, alpha=.95)
            axis.scatter([start[0], end[0]], [start[1], end[1]], s=28,
                         facecolors="white", edgecolors="#d62728",
                         linewidths=1.1, zorder=3)
        axis.scatter([i % n for i in range(n*n)], [i // n for i in range(n*n)],
                     s=9, color="#222222", zorder=4)
        axis.set_title(label); axis.set_aspect("equal")
        axis.set_xlim(-.65, n - .35); axis.set_ylim(n - .35, -.65)
        axis.axis("off")
    handles = [
        Line2D([0], [0], color="#c7c7c7", lw=.9, label="Mesh link"),
        Line2D([0], [0], color="#d62728", lw=2.2,
               label="Express link"),
    ]
    fig.legend(handles=handles, loc="lower center", ncol=2,
               frameon=False, bbox_to_anchor=(.5, -.01))
    fig.tight_layout(rect=(0, .06, 1, 1))
    fig.savefig(output / "topologies.svg", bbox_inches="tight")
    fig.savefig(output / "topologies.png", dpi=180, bbox_inches="tight")
    plt.close(fig)


def grouping(frame, section, keys):
    data = frame[frame.section == section].copy()
    if data.empty:
        return data
    if "termination_reason" in data:
        # A watchdog observation is diagnostic, not automatically censoring:
        # escape-on runs explicitly continue and remain valid when they reach
        # simulate_limit_after_ni_watchdog.
        reasons = data.termination_reason.fillna("")
        data["_completed"] = (reasons.str.startswith("simulate_limit") |
                              reasons.eq("drain_completed"))
    elif "no_progress" in data:
        data["_completed"] = ~data.no_progress.fillna(False).astype(bool)
    else:
        data["_completed"] = True
    counts = data.groupby(keys, as_index=False).agg(
        samples=("seed", "size"), completed=("_completed", "sum"))
    valid = data[data._completed]
    metrics = valid.groupby(keys, as_index=False).agg(
        throughput=("accepted_throughput", "mean"),
        latency=("average_packet_latency_cycles", "mean"),
        throughput_sd=("accepted_throughput", "std"),
        escape=("delivered_escape_fraction", "mean"),
        express=("express_traversals", "sum"),
        received=("packets_received", "sum"),
        hops=("average_hops", "mean"),
        max_load=("max_link_utilization", "mean"),
        fairness=("per_source_throughput_jain", "mean"),
    )
    return counts.merge(metrics, on=keys, how="left")


def scaling_grouping(frame):
    data = frame[frame.section == "scaling"].copy()
    if data.empty:
        return data
    data["scaling_row"] = data.topology_label.str.extract(
        r"row(\d+):"
    )[0].astype(int)
    # Ten independent 16x16 Random layouts are one expectation, not ten bars.
    return grouping(
        data, "scaling",
        ["scaling_row", "topology_class", "configured_injection_rate"],
    )


def plot_main(frame, output):
    main = grouping(frame, "main", ["traffic", "topology_class",
                                     "configured_injection_rate"])
    for traffic in sorted(main.traffic.unique()):
        data = main[main.traffic == traffic]
        fig, axes = plt.subplots(1, 2, figsize=(9.2, 3.6))
        for topology in ("mesh", "random", "greedy", "sa"):
            item = data[data.topology_class == topology].sort_values(
                "configured_injection_rate").dropna(
                    subset=["throughput", "latency"])
            if item.empty: continue
            axes[0].plot(item.configured_injection_rate, item.throughput,
                         marker=MARKERS[topology], ms=2.5, lw=1.15,
                         color=COLORS[topology], label=LABELS[topology])

            # A point is dominated when another measured operating point of
            # the same topology has no lower throughput and no higher latency,
            # with at least one strict improvement.  Keep every raw overload
            # sample visible, but connect only the non-dominated envelope.
            throughput = item.throughput.to_numpy()
            latency = item.latency.to_numpy()
            dominated = np.zeros(len(item), dtype=bool)
            for index in range(len(item)):
                weakly_better = ((throughput >= throughput[index]) &
                                 (latency <= latency[index]))
                strictly_better = ((throughput > throughput[index]) |
                                   (latency < latency[index]))
                dominated[index] = bool(np.any(weakly_better & strictly_better))
            overload = item.iloc[np.flatnonzero(dominated)]
            frontier = item.iloc[np.flatnonzero(~dominated)].sort_values(
                ["throughput", "latency"])
            if not overload.empty:
                axes[1].scatter(
                    overload.throughput, overload.latency,
                    marker=MARKERS[topology], s=20, facecolors="none",
                    edgecolors="#9a9a9a", linewidths=.8, alpha=.85,
                    zorder=2,
                )
            axes[1].plot(
                frontier.throughput, frontier.latency,
                marker=MARKERS[topology], ms=3.4, lw=1.65,
                color=COLORS[topology], label=LABELS[topology], zorder=3,
            )
        axes[0].set(xlabel="Configured injection rate", ylabel="Accepted throughput")
        axes[1].set(xlabel="Accepted throughput", ylabel="Packet latency (cycles)")
        axes[0].legend(fontsize=8)
        frontier_handles = [
            Line2D([0], [0], color=COLORS[topology],
                   marker=MARKERS[topology], lw=1.65,
                   label=LABELS[topology])
            for topology in ("mesh", "random", "greedy", "sa")
        ]
        frontier_handles.append(Line2D(
            [0], [0], color="#9a9a9a", marker="o", markerfacecolor="none",
            lw=0, label="Dominated overload point",
        ))
        axes[1].legend(handles=frontier_handles, fontsize=7, loc="best")
        axes[0].grid(alpha=.25); axes[1].grid(alpha=.25)
        fig.tight_layout()
        fig.savefig(output / f"main_{traffic}.svg", bbox_inches="tight")
        fig.savefig(output / f"main_{traffic}.png", dpi=180,
                    bbox_inches="tight")
        plt.close(fig)
    uniform = main[main.traffic == "uniform_random"]
    fig, axes = plt.subplots(1, 2, figsize=(9.2, 3.5))
    for topology in ("mesh", "random", "greedy", "sa"):
        item = uniform[uniform.topology_class == topology].sort_values(
            "configured_injection_rate")
        if item.empty: continue
        axes[0].plot(item.configured_injection_rate, item.escape, marker="o", ms=3,
                     color=COLORS[topology], label=LABELS[topology])
        express_per_packet = item.express / item.received.clip(lower=1)
        axes[1].plot(item.configured_injection_rate, express_per_packet,
                     marker="o", ms=3, color=COLORS[topology], label=LABELS[topology])
    axes[0].set(xlabel="Configured injection rate", ylabel="Delivered escape fraction")
    axes[1].set(xlabel="Configured injection rate", ylabel="Express traversals / packet")
    for axis in axes: axis.grid(alpha=.25)
    axes[0].legend(fontsize=8); fig.tight_layout()
    fig.savefig(output / "uniform_path_behavior.svg", bbox_inches="tight"); plt.close(fig)
    return main


def plot_cross(frame, output):
    data = grouping(frame, "cross", ["traffic", "topology_label"])
    if data.empty: return pd.DataFrame()
    mesh = data[data.topology_label == "mesh"].set_index("traffic").throughput
    traffics = [item for item in ("uniform_random", "tornado", "bit_complement",
                                  "cutstress_bidirectional") if item in set(data.traffic)]
    training = [(traffic, TRAFFIC_LABELS[traffic]) for traffic in traffics]
    training.append(("mixture", "Mixture"))
    labels = [label for _key, label in training] + ["Random expectation"]
    greedy = np.full((len(training), len(traffics) + 1), np.nan)
    sa = np.full_like(greedy, np.nan)
    random = data[data.topology_label.str.startswith("random_p")].groupby(
        "traffic", as_index=True
    ).throughput.mean()
    for row, (trained, _label) in enumerate(training):
        for col, traffic in enumerate(traffics):
            for algorithm, matrix in (("greedy", greedy), ("sa", sa)):
                found = data[
                    (data.topology_label == f"{trained}_{algorithm}")
                    & (data.traffic == traffic)
                ]
                if not found.empty:
                    matrix[row, col] = found.iloc[0].throughput / mesh[traffic]
        greedy_values = greedy[row, :-1]
        sa_values = sa[row, :-1]
        greedy_finite = greedy_values[np.isfinite(greedy_values) & (greedy_values > 0)]
        sa_finite = sa_values[np.isfinite(sa_values) & (sa_values > 0)]
        if greedy_finite.size:
            greedy[row, -1] = float(np.exp(np.mean(np.log(greedy_finite))))
        if sa_finite.size:
            sa[row, -1] = float(np.exp(np.mean(np.log(sa_finite))))
    random_values = np.array([
        random.get(traffic, np.nan) / mesh[traffic] for traffic in traffics
    ] + [np.nan])
    random_finite = random_values[:-1][
        np.isfinite(random_values[:-1]) & (random_values[:-1] > 0)
    ]
    if random_finite.size:
        random_values[-1] = float(np.exp(np.mean(np.log(random_finite))))
    columns = [TRAFFIC_LABELS[t] for t in traffics] + ["Mixture mean"]
    fig, axes = plt.subplots(
        1, len(columns), sharey=True,
        figsize=(2.0 * len(columns) + 3.2, max(5.2, .43 * len(labels))),
        gridspec_kw={"wspace": .05},
    )
    cmap = plt.cm.RdYlGn
    for col, (axis, column) in enumerate(zip(axes, columns)):
        # Trained rows are colored by SA. Random is colored by its own value.
        # Greedy remains visible as the first number in every trained cell.
        values = np.concatenate((sa[:, col], [random_values[col]]))
        finite = values[np.isfinite(values)]
        low, high = float(finite.min()), float(finite.max())
        if math.isclose(low, high):
            low -= .01; high += .01
        image = axis.imshow(
            values[:, None], cmap=cmap, vmin=low, vmax=high, aspect="auto",
        )
        for row in range(len(training)):
            if np.isfinite(greedy[row, col]) and np.isfinite(sa[row, col]):
                axis.text(
                    0, row,
                    f"G {greedy[row, col]:.3f}\nSA {sa[row, col]:.3f}",
                    ha="center", va="center", fontsize=6.8,
                )
        if np.isfinite(random_values[col]):
            axis.text(0, len(training), f"{random_values[col]:.3f}",
                      ha="center", va="center", fontsize=7.2)
        matched_row = col if col < len(traffics) else len(training) - 1
        axis.add_patch(Rectangle(
            (-.48, matched_row - .48), .96, .96, fill=False,
            edgecolor="#222222", linewidth=1.25,
        ))
        axis.set_xticks([0], [column], fontsize=8)
        axis.tick_params(axis="x", length=0, pad=5)
        axis.set_yticks(range(len(labels)), labels, fontsize=8)
    axes[0].tick_params(axis="y", length=0)
    fig.subplots_adjust(left=.24, right=.99, bottom=.08, top=.97)
    fig.savefig(output / "placement_traffic_cross.svg", bbox_inches="tight")
    fig.savefig(output / "placement_traffic_cross.png", dpi=180,
                bbox_inches="tight")
    plt.close(fig)
    display = []
    for row, (_trained, label) in enumerate(training):
        record = {"placement training": label}
        for col, column in enumerate(columns):
            record[column] = (
                f"G {greedy[row, col]:.3f} / SA {sa[row, col]:.3f}"
                if np.isfinite(greedy[row, col]) and np.isfinite(sa[row, col])
                else "—"
            )
        display.append(record)
    display.append({
        "placement training": "Random expectation",
        **{
            column: (f"{random_values[col]:.3f}"
                     if np.isfinite(random_values[col]) else "—")
            for col, column in enumerate(columns)
        },
    })
    return pd.DataFrame(display)


def plot_random_distribution(frame, output):
    data = frame[(frame.section == "random_distribution") &
                 (frame.traffic == "uniform_random")].copy()
    if "termination_reason" in data:
        complete = data.termination_reason.fillna("").str.startswith(
            "simulate_limit"
        ) | data.termination_reason.fillna("").eq("drain_completed")
        data = data[complete]
    if data.empty: return
    random_means = data.groupby("topology_label").accepted_throughput.mean()
    main = frame[(frame.section == "main") & (frame.traffic == "uniform_random") &
                 (frame.configured_injection_rate == .8)]
    fig, hist_axis = plt.subplots(figsize=(6.8, 3.7))
    ordered = np.sort(random_means.to_numpy())
    # Zoom past the sparse failure tail so the central distribution is
    # legible, but state the omitted count directly in the panel.
    bulk_floor = min(.265, float(np.quantile(ordered, .05)))
    bulk = ordered[ordered >= bulk_floor]
    bins = max(16, min(24, int(round(math.sqrt(len(bulk)))) + 5))
    hist_axis.hist(
        bulk, bins=bins, color=COLORS["random"], alpha=.78,
        edgecolor="white",
    )
    for topology in ("greedy", "sa"):
        value = main[main.topology_class == topology].accepted_throughput.mean()
        hist_axis.axvline(value, color=COLORS[topology], lw=2,
                          label=f"{LABELS[topology]} {value:.3f}")
    hist_axis.set(
        xlabel="Accepted throughput", ylabel="Random topology count",
    )
    omitted = len(ordered) - len(bulk)
    hist_axis.text(
        .02, .04,
        f"{omitted}/{len(ordered)} layouts below {bulk_floor:.3f} (not shown)",
        transform=hist_axis.transAxes, ha="left", va="bottom", fontsize=8,
        bbox={"facecolor": "white", "edgecolor": "none", "alpha": .82,
              "pad": 1.5},
    )
    hist_axis.legend()
    hist_axis.grid(alpha=.2)
    fig.tight_layout()
    fig.savefig(output / "random_distribution.svg", bbox_inches="tight")
    fig.savefig(output / "random_distribution.png", dpi=180,
                bbox_inches="tight")
    plt.close(fig)


def plot_generic_ablations(frame, output):
    specs = [
        ("routing_ablation", "topology_label", "routing_ablation.svg"),
        ("information_ablation", "topology_label", "information_ablation.svg"),
        ("escape", "topology_label", "escape_timeout.svg"),
    ]
    for section, category, filename in specs:
        data = grouping(frame, section, [category, "configured_injection_rate"])
        if data.empty: continue
        fig, axes = plt.subplots(1, 2, figsize=(10.5, 3.8))
        for label, item in data.groupby(category):
            item = item.sort_values("configured_injection_rate")
            axes[0].plot(item.configured_injection_rate, item.throughput,
                         marker="o", ms=3, label=label)
            axes[1].plot(item.configured_injection_rate, item.latency,
                         marker="o", ms=3, label=label)
        axes[0].set(xlabel="Injection rate", ylabel="Accepted throughput")
        axes[1].set(xlabel="Injection rate", ylabel="Latency (cycles)")
        for axis in axes: axis.grid(alpha=.2)
        axes[0].legend(fontsize=6, ncol=2); fig.tight_layout()
        fig.savefig(output / filename, bbox_inches="tight"); plt.close(fig)


def plot_escape_off(frame, output):
    data = frame[frame.section == "escape_off"].copy()
    if data.empty: return
    data["completed"] = ~(data.ni_watchdog_triggered.fillna(False) |
                          data.global_no_progress_detected.fillna(False))
    grouped = data.groupby(["traffic", "configured_injection_rate"], as_index=False).agg(
        completion=("completed", "mean"),
        watchdog_cycle=("ni_watchdog_cycle", "mean"),
    )
    fig, axes = plt.subplots(1, 2, figsize=(9.5, 3.7))
    for traffic, item in grouped.groupby("traffic"):
        item = item.sort_values("configured_injection_rate")
        label = TRAFFIC_LABELS.get(traffic, traffic)
        axes[0].plot(item.configured_injection_rate, item.completion,
                     marker="o", ms=3, label=label)
        axes[1].plot(item.configured_injection_rate, item.watchdog_cycle,
                     marker="o", ms=3, label=label)
    axes[0].set(xlabel="Injection rate", ylabel="No-watchdog probability", ylim=(-.03, 1.03))
    axes[1].set(xlabel="Injection rate", ylabel="Mean time to watchdog (cycles)")
    for axis in axes: axis.grid(alpha=.2)
    axes[0].legend(fontsize=7); fig.tight_layout()
    fig.savefig(output / "escape_off_failures.svg", bbox_inches="tight"); plt.close(fig)


def plot_escape_deadlock_contrast(frame, output):
    data = frame[frame.section == "escape_deadlock_contrast"].copy()
    if data.empty:
        return
    data["completed"] = ~(data.global_no_progress_detected.fillna(False) |
                          data.ni_watchdog_triggered.fillna(False))
    values = data.groupby("topology_label").agg(
        completion=("completed", "mean"),
        drain_completion=("drain_completed", "mean"),
    ).reindex(["off", "on_t32"])
    fig, axis = plt.subplots(figsize=(5.2, 3.6))
    x = np.arange(len(values))
    axis.bar(x - .18, values.completion, .36, label="No deadlock/watchdog")
    axis.bar(x + .18, values.drain_completion, .36, label="Finite-injection drain")
    axis.set(xticks=x, xticklabels=["Escape off", "Escape on"],
             ylabel="Fraction of 20 seeds", ylim=(0, 1.08))
    axis.grid(axis="y", alpha=.2); axis.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(output / "escape_deadlock_contrast.svg", bbox_inches="tight")
    plt.close(fig)


def plot_link_load(frame, output):
    selected = frame[(frame.section == "main") &
                     (frame.traffic == "uniform_random") &
                     (frame.configured_injection_rate == .8) &
                     (frame.seed == 5)].copy()
    fig, axes = plt.subplots(2, 2, figsize=(8.2, 8.2))
    chosen = {}
    for topology in ("mesh", "random", "greedy", "sa"):
        data = selected[selected.topology_class == topology]
        if topology == "random" and not data.empty:
            preferred = data[data.topology_label == "random_p1"]
            if not preferred.empty:
                data = preferred
        if not data.empty:
            chosen[topology] = data.iloc[0]
    merged_by_topology = {}
    for topology, row in chosen.items():
        merged = defaultdict(list)
        for source, destination, express_id, value in zip(
                row.directed_link_sources, row.directed_link_destinations,
                row.directed_link_express_ids, row.directed_link_utilization):
            # Express IDs are directed, so the physical-link identity is the
            # unordered endpoint pair plus its mesh/express class.
            key = (min(source, destination), max(source, destination),
                   express_id >= 0)
            merged[key].append(float(value))
        merged_by_topology[topology] = {
            key: float(np.mean(values)) for key, values in merged.items()
        }
    all_values = [value for links in merged_by_topology.values()
                  for value in links.values()]
    shared_maximum = max(all_values, default=1.0)
    norm = Normalize(vmin=0.0, vmax=shared_maximum)
    cmap = plt.cm.inferno
    plotted = False
    for axis, topology in zip(axes.flat, ("mesh", "random", "greedy", "sa")):
        if topology not in chosen:
            axis.axis("off"); continue
        row = chosen[topology]; plotted = True
        n = int(row.dimension)
        links = merged_by_topology[topology]
        local_maximum = max(links.values(), default=0.0)
        for (source, destination, is_express), value in links.items():
            x0, y0 = source % n, source // n; x1, y1 = destination % n, destination // n
            color = cmap(norm(value))
            if is_express:
                curved_link(axis, (x0, y0), (x1, y1), color=color,
                            linewidth=2.0, radius=.13, alpha=.98,
                            arrow=False, zorder=2)
            else:
                axis.plot([x0, x1], [y0, y1], color=color, lw=.9,
                          alpha=.82, zorder=1)
        axis.scatter([i % n for i in range(n*n)], [i // n for i in range(n*n)],
                     color="black", s=6, zorder=3)
        panel_label = ("Representative Random (p1)" if topology == "random"
                       else LABELS[topology])
        axis.set_title(f"{panel_label} (local max={local_maximum:.2f})")
        axis.set_aspect("equal"); axis.set_xlim(-.65, n - .35)
        axis.set_ylim(n - .35, -.65); axis.axis("off")
    if plotted:
        scalar = plt.cm.ScalarMappable(norm=norm, cmap=cmap)
        scalar.set_array([])
        fig.colorbar(
            scalar, ax=axes.ravel().tolist(), fraction=.035, pad=.025,
            label=("Mean utilization of the two directed channels "
                   "(active flit cycles / measurement cycles)"),
        )
        legend = [
            Line2D([0], [0], color="#555555", lw=.8,
                   label="Mesh physical link"),
            Line2D([0], [0], color="#555555", lw=2.0,
                   label="Express physical link (curved)"),
        ]
        fig.legend(handles=legend, loc="lower center", ncol=2,
                   frameon=False, bbox_to_anchor=(.46, .01))
        fig.subplots_adjust(left=.02, right=.88, bottom=.08, top=.97,
                            wspace=.08, hspace=.12)
        fig.savefig(output / "directed_link_load.svg", bbox_inches="tight")
        fig.savefig(output / "directed_link_load.png", dpi=180,
                    bbox_inches="tight")
    plt.close(fig)


def plot_latency_histograms(frame, output):
    data = frame[(frame.section == "main") & (frame.traffic == "uniform_random") &
                 (frame.configured_injection_rate == .8)]
    if data.empty: return
    fig, axes = plt.subplots(1, 2, figsize=(9.5, 3.7))
    for axis, field, title in ((axes[0], "adaptive_latency_histogram", "Adaptive delivery"),
                               (axes[1], "escape_latency_histogram", "Escape delivery")):
        for topology in ("mesh", "random", "greedy", "sa"):
            rows = data[data.topology_class == topology]
            if rows.empty or field not in rows: continue
            histogram = np.sum(np.stack(rows[field].to_list()), axis=0)
            if histogram.sum() == 0: continue
            axis.plot(2 ** np.arange(len(histogram)), histogram / histogram.sum(),
                      marker="o", ms=2, color=COLORS[topology], label=LABELS[topology])
        axis.set_xscale("log", base=2); axis.set_yscale("log")
        axis.set(xlabel="Latency bucket upper bound (cycles)", ylabel="Packet fraction",
                 title=title); axis.grid(alpha=.2)
    axes[0].legend(fontsize=7); fig.tight_layout()
    fig.savefig(output / "latency_distribution.svg", bbox_inches="tight"); plt.close(fig)


def plot_scaling(frame, output):
    data = scaling_grouping(frame)
    if data.empty: return
    available = set(data.scaling_row.astype(int).unique())
    rows = [row for row in SCALING_ORDER if row in available]
    tick_labels = [SCALING_LABELS.get(int(row), str(row)) for row in rows]
    width = .19; fig, axis = plt.subplots(figsize=(12, 4))
    throughput_values = []
    for offset, topology in enumerate(("mesh", "random", "greedy", "sa")):
        values = [data[(data.scaling_row == row) &
                       (data.topology_class == topology)].throughput.mean()
                  for row in rows]
        throughput_values.extend(value for value in values if np.isfinite(value))
        axis.bar(np.arange(len(rows)) + (offset - 1.5) * width, values, width,
                 color=COLORS[topology], label=LABELS[topology])
    axis.set_xticks(range(len(rows)), tick_labels)
    axis.set(xlabel="Scaling configuration", ylabel="Accepted throughput")
    if throughput_values:
        axis.set_ylim(0, max(throughput_values) * 1.27)
    axis.legend(loc="upper center", bbox_to_anchor=(.5, .985), ncol=4)
    axis.grid(axis="y", alpha=.2); fig.tight_layout()
    fig.savefig(output / "scaling_throughput.svg", bbox_inches="tight"); plt.close(fig)

    fig, axis = plt.subplots(figsize=(12, 4))
    latency_values = []
    for offset, topology in enumerate(("mesh", "random", "greedy", "sa")):
        values = [data[(data.scaling_row == row) &
                       (data.topology_class == topology)].latency.mean()
                  for row in rows]
        latency_values.extend(value for value in values
                              if np.isfinite(value) and value > 0)
        axis.bar(np.arange(len(rows)) + (offset - 1.5) * width, values, width,
                 color=COLORS[topology], label=LABELS[topology])
    axis.set_xticks(range(len(rows)), tick_labels)
    axis.set_yscale("log")
    axis.set(xlabel="Scaling configuration",
             ylabel="Average packet latency (cycles, log scale)")
    if latency_values:
        axis.set_ylim(min(latency_values) / 1.25,
                      max(latency_values) * 4.0)
    axis.legend(loc="upper center", bbox_to_anchor=(.5, .985), ncol=4)
    axis.grid(axis="y", alpha=.2); fig.tight_layout()
    fig.savefig(output / "scaling_latency.svg", bbox_inches="tight"); plt.close(fig)


def plot_row8_explanations(frame, output):
    """Visualize the heterogeneous workload, selected SA, and its mean load."""
    n = 16
    demand = soc_heterogeneous_demand(n)
    destination_share = np.zeros(n * n)
    for (_source, destination), weight in demand.items():
        destination_share[destination] += weight

    # Keep panels (a) and (c) on an identical fixed canvas.  In particular,
    # their color bars share the same vertical extent when placed side by side.
    panel_size = (6.2, 5.2)
    grid_box = [.07, .13, .73, .85]
    colorbar_box = [.84, .13, .035, .85]
    fig = plt.figure(figsize=panel_size)
    axis = fig.add_axes(grid_box)
    relative = destination_share.reshape(n, n) * (n * n)
    image = axis.imshow(relative, cmap="YlOrRd", origin="upper", vmin=0,
                        vmax=float(relative.max()))
    tile = n // 4
    for boundary in range(tile, n, tile):
        axis.axhline(boundary - .5, color="white", lw=1.0, alpha=.9)
        axis.axvline(boundary - .5, color="white", lw=1.0, alpha=.9)
    banks = [(x0 + tile // 2, y0 + tile // 2)
             for y0 in range(0, n, tile) for x0 in range(0, n, tile)]
    memory = [(x, y) for x in (0, n - 1)
              for y in (n // 8, 3 * n // 8, 5 * n // 8, 7 * n // 8)]
    accelerators = [
        (n // 4, n // 4), (n // 2, n // 4),
        (3 * n // 4, n // 4), (n // 4, n // 2),
        (3 * n // 4, n // 2), (n // 4, 3 * n // 4),
        (n // 2, 3 * n // 4), (3 * n // 4, 3 * n // 4),
    ]
    for points, marker, color, label in (
        (banks, "s", "#2b6cb0", "Tile bank"),
        (memory, "D", "#5a189a", "Memory controller"),
        (accelerators, "^", "#00876c", "Accelerator"),
    ):
        axis.scatter([x for x, _y in points], [y for _x, y in points],
                     marker=marker, s=36, facecolors="none", edgecolors=color,
                     linewidths=1.2, label=label)
    axis.set(xlabel="Router x", ylabel="Router y", xticks=range(0, n, 2),
             yticks=range(0, n, 2))
    axis.set_aspect("equal")
    color_axis = fig.add_axes(colorbar_box)
    fig.colorbar(image, cax=color_axis,
                 label="Aggregate destination demand / uniform demand")
    fig.legend(loc="lower center", bbox_to_anchor=(.435, .005), ncol=3,
               frameon=False, fontsize=8)
    fig.savefig(output / "row8_traffic.svg")
    fig.savefig(output / "row8_traffic.png", dpi=180)
    plt.close(fig)

    selected = frame[(frame.section == "scaling") &
                     (frame.topology_label == "row8:sa")].copy()
    if selected.empty:
        return
    topology = Path(selected.iloc[0].topology_file)
    dimension, express_edges = load_edges(topology)

    fig = plt.figure(figsize=panel_size)
    # Unlike the two heat maps, this panel has no color bar.  Center the same
    # size square grid on the canvas instead of reserving phantom right space.
    axis = fig.add_axes([.105, .13, .79, .85])
    for y in range(dimension):
        for x in range(dimension - 1):
            axis.plot([x, x + 1], [y, y], color="#d2d2d2", lw=.42,
                      zorder=0)
    for x in range(dimension):
        for y in range(dimension - 1):
            axis.plot([x, x], [y, y + 1], color="#d2d2d2", lw=.42,
                      zorder=0)
    for index, edge in enumerate(express_edges):
        start = (edge.u % dimension, edge.u // dimension)
        end = (edge.v % dimension, edge.v // dimension)
        curved_link(axis, start, end, color="#d62728", linewidth=1.45,
                    radius=.10 if index % 2 == 0 else -.10, alpha=.9)
    axis.scatter([i % dimension for i in range(dimension * dimension)],
                 [i // dimension for i in range(dimension * dimension)],
                 s=5, color="#222222", zorder=3)
    axis.set_aspect("equal"); axis.set_xlim(-.7, dimension - .3)
    axis.set_ylim(dimension - .3, -.7); axis.axis("off")
    fig.legend(handles=[
        Line2D([0], [0], color="#d2d2d2", lw=.8, label="Mesh link"),
        Line2D([0], [0], color="#d62728", lw=1.7, label="SA express link"),
    ], loc="lower center", bbox_to_anchor=(.5, .005), ncol=2,
       frameon=False)
    fig.savefig(output / "row8_sa_topology.svg")
    fig.savefig(output / "row8_sa_topology.png", dpi=180)
    plt.close(fig)

    telemetry = selected[
        selected.get("directed_link_utilization", pd.Series(index=selected.index,
                                                              dtype=object)).notna()
    ]
    if telemetry.empty:
        return
    physical_samples = defaultdict(list)
    for _, row in telemetry.iterrows():
        within_run = defaultdict(list)
        for source, destination, express_id, value in zip(
                row.directed_link_sources, row.directed_link_destinations,
                row.directed_link_express_ids, row.directed_link_utilization):
            key = (min(source, destination), max(source, destination),
                   express_id >= 0)
            within_run[key].append(float(value))
        for key, values in within_run.items():
            physical_samples[key].append(float(np.mean(values)))
    loads = {key: float(np.mean(values))
             for key, values in physical_samples.items()}
    norm = Normalize(vmin=0.0, vmax=max(loads.values(), default=1.0))
    cmap = plt.cm.inferno
    fig = plt.figure(figsize=panel_size)
    axis = fig.add_axes(grid_box)
    for (source, destination, is_express), value in loads.items():
        start = (source % dimension, source // dimension)
        end = (destination % dimension, destination // dimension)
        if is_express:
            curved_link(axis, start, end, color=cmap(norm(value)),
                        linewidth=1.8, radius=.10, alpha=.98, zorder=2)
        else:
            axis.plot([start[0], end[0]], [start[1], end[1]],
                      color=cmap(norm(value)), lw=.75, alpha=.9, zorder=1)
    axis.scatter([i % dimension for i in range(dimension * dimension)],
                 [i // dimension for i in range(dimension * dimension)],
                 s=4, color="black", zorder=3)
    scalar = plt.cm.ScalarMappable(norm=norm, cmap=cmap); scalar.set_array([])
    color_axis = fig.add_axes(colorbar_box)
    fig.colorbar(scalar, cax=color_axis,
                 label="Mean bidirectional physical-link utilization")
    axis.set_aspect("equal"); axis.set_xlim(-.7, dimension - .3)
    axis.set_ylim(dimension - .3, -.7); axis.axis("off")
    fig.savefig(output / "row8_sa_load.svg")
    fig.savefig(output / "row8_sa_load.png", dpi=180)
    plt.close(fig)


def markdown_table(frame, digits=4):
    if frame.empty: return "（无数据）"
    display = frame.copy()
    columns = [str(column) for column in display.columns]
    lines = ["| " + " | ".join(columns) + " |",
             "|" + "|".join("---" for _ in columns) + "|"]
    for values in display.itertuples(index=False, name=None):
        rendered = []
        for value in values:
            if pd.isna(value):
                text = "—"
            elif isinstance(value, (float, np.floating)):
                text = f"{value:.{digits}f}"
            else:
                text = str(value)
            rendered.append(text.replace("|", "\\|"))
        lines.append("| " + " | ".join(rendered) + " |")
    return "\n".join(lines)


def main_rate_table(main, rate):
    """Traffic rows by topology columns; every cell is throughput / latency."""
    selected = main[np.isclose(main.configured_injection_rate, rate)]
    rows = []
    traffic_order = [
        traffic for traffic in (
            "uniform_random", "tornado", "bit_complement",
            "cutstress_bidirectional",
        ) if traffic in set(selected.traffic)
    ]
    for traffic in traffic_order:
        data = selected[selected.traffic == traffic].set_index("topology_class")
        record = {"Traffic": TRAFFIC_LABELS.get(traffic, traffic)}
        for topology in ("mesh", "random", "greedy", "sa"):
            if topology not in data.index:
                record[LABELS[topology]] = "—"
                continue
            item = data.loc[topology]
            record[LABELS[topology]] = (
                f"{item.throughput:.4f} / {item.latency:.0f}"
            )
        rows.append(record)
    return pd.DataFrame(rows)


def sa_high_load_table(main):
    rows = []
    high = main[main.configured_injection_rate == .8]
    for traffic, data in high.groupby("traffic"):
        by_topology = data.set_index("topology_class")
        if not {"greedy", "sa"}.issubset(by_topology.index):
            continue
        greedy = by_topology.loc["greedy"]
        sa = by_topology.loc["sa"]
        rows.append({
            "Traffic": TRAFFIC_LABELS.get(traffic, traffic),
            "selected SA": "Unified SA-reheat (curve objective)",
            "Greedy throughput": greedy.throughput,
            "SA throughput": sa.throughput,
            "throughput improvement": (
                f"{(sa.throughput / greedy.throughput - 1.0) * 100:+.2f}%"
            ),
            "Greedy latency": greedy.latency,
            "SA latency": sa.latency,
            "latency change": f"{(sa.latency / greedy.latency - 1.0) * 100:+.2f}%",
        })
    return pd.DataFrame(rows)


def sa_curve_score_table(main):
    rows = []
    for traffic, data in main.groupby("traffic"):
        # The extra near-saturation samples improve curve rendering and must
        # not silently reweight the already-defined 16-point SA objective.
        data = data[data.configured_injection_rate.round(2).isin(
            [round(index * .05, 2) for index in range(1, 17)])]
        scores = {}
        minima = {}
        for topology in ("greedy", "sa"):
            points = data[data.topology_class == topology].sort_values(
                "configured_injection_rate"
            )
            if points.empty:
                continue
            normalized = points.throughput / (
                0.5 * points.configured_injection_rate
            )
            scores[topology] = (
                normalized.mean() + 0.25 * normalized.min()
                - 0.006 * np.mean(
                    np.log1p(points.latency) / math.log1p(100000)
                )
            )
            minima[topology] = normalized.min()
        if not {"greedy", "sa"}.issubset(scores):
            continue
        rows.append({
            "Traffic": TRAFFIC_LABELS.get(traffic, traffic),
            "Greedy curve score": scores["greedy"],
            "SA curve score": scores["sa"],
            "score improvement": (
                f"{(scores['sa'] / scores['greedy'] - 1.0) * 100:+.2f}%"
            ),
            "Greedy worst T/offered": minima["greedy"],
            "SA worst T/offered": minima["sa"],
        })
    return pd.DataFrame(rows)


def anomalies(frame, main):
    notes = []
    high = main[main.configured_injection_rate == .8]
    for traffic, data in high.groupby("traffic"):
        values = data.set_index("topology_class").throughput
        expected = [item for item in ("mesh", "random", "greedy", "sa") if item in values]
        for left, right in zip(expected, expected[1:]):
            if values[right] <= values[left]:
                notes.append(
                    f"{TRAFFIC_LABELS.get(traffic, traffic)} rate=0.8: "
                    f"{LABELS[right]} throughput {values[right]:.4f} 不高于 "
                    f"{LABELS[left]} {values[left]:.4f}。")
    failures = frame[frame.get("global_no_progress_detected", False) == True]
    if len(failures): notes.append(f"检测到 {len(failures)} 个 global-no-progress 样本。")
    watchdogs = frame[frame.get("ni_watchdog_triggered", False) == True]
    if len(watchdogs):
        by_section = watchdogs.groupby("section").size().to_dict()
        rendered = "、".join(f"{key}={value}" for key, value in sorted(by_section.items()))
        continued = watchdogs[
            watchdogs.termination_reason.fillna("").str.startswith(
                "simulate_limit")]
        terminated = watchdogs.drop(index=continued.index)
        notes.append(
            f"检测到 {len(watchdogs)} 个 NI watchdog 事件（{rendered}）；其中 "
            f"{len(continued)} 个按配置继续运行到 simulation limit 并纳入统计，"
            f"{len(terminated)} 个提前终止样本不纳入 throughput/latency 均值。")
        random_watchdogs = watchdogs[
            watchdogs.section == "random_distribution"
        ]
        if len(random_watchdogs):
            affected = random_watchdogs.topology_label.nunique()
            notes.append(
                f"Random distribution 中 {affected}/200 个 topology 在两个 seed "
                f"共触发 {len(random_watchdogs)} 次 NI starvation watchdog；这些 case "
                "均继续运行到完整 measurement limit，图中的低吞吐长尾不是提前终止值。"
            )
        scaling_watchdogs = watchdogs[watchdogs.section == "scaling"]
        if len(scaling_watchdogs):
            labels = []
            for raw in sorted(scaling_watchdogs.topology_label.unique()):
                match = re.fullmatch(r"row(\d+):(.*)", str(raw))
                if match:
                    name = SCALING_LABELS.get(int(match.group(1)), match.group(1))
                    labels.append(f"{name}:{match.group(2)}")
                else:
                    labels.append(str(raw))
            notes.append(
                f"Scaling watchdog 来自 {', '.join(labels)}；这些 case 均按配置继续运行到 "
                "simulation limit，未把 watchdog 时刻的瞬时值当作最终结果。"
            )
    escape_off = watchdogs[watchdogs.section == "escape_off"]
    if len(escape_off):
        row = escape_off.iloc[0]
        notes.append(
            f"Escape-off 唯一 watchdog 是 {TRAFFIC_LABELS.get(row.traffic, row.traffic)} "
            f"rate={row.configured_injection_rate:.2f}, seed={int(row.seed)}，触发于 "
            f"cycle {int(row.ni_watchdog_cycle)}；未观察到全网 global-no-progress。")
    escape = grouping(frame, "escape",
                      ["topology_label", "configured_injection_rate"])
    escape_high = escape[escape.configured_injection_rate == .8]
    if not escape_high.empty:
        best = escape_high.loc[escape_high.throughput.idxmax()]
        standard = escape_high[escape_high.topology_label == "on_t32"]
        if best.topology_label != "on_t32" and not standard.empty:
            notes.append(
                f"Escape timeout 扫描在 rate=0.8 由 {best.topology_label} 取得最高 "
                f"throughput {best.throughput:.4f}，标准 timeout=32 仅 "
                f"{standard.iloc[0].throughput:.4f}；timeout=32 不是本轮性能最优值。")
    scaling = scaling_grouping(frame)
    incomplete = scaling[scaling.completed < scaling.samples]
    if not incomplete.empty:
        affected = [SCALING_LABELS.get(index, str(index)) for index in
                    sorted(set(incomplete.scaling_row.astype(int)))]
        notes.append(
            f"Scaling 配置 {affected} 存在未完整运行的样本，需要单独检查。")
    soc = frame[
        (frame.section == "scaling")
        & frame.topology_label.str.startswith("row8:")
    ]
    if not soc.empty:
        fixed = soc[soc.topology_class.isin(["mesh", "greedy", "sa"])]
        fixed_mean = fixed.groupby("topology_class").agg(
            throughput=("accepted_throughput", "mean"),
            latency=("average_packet_latency_cycles", "mean"),
        )
        if {"mesh", "greedy"}.issubset(fixed_mean.index):
            throughput_gain = 100.0 * (
                fixed_mean.loc["greedy", "throughput"]
                / fixed_mean.loc["mesh", "throughput"] - 1.0
            )
            reduction = 100.0 * (
                1.0
                - fixed_mean.loc["greedy", "latency"]
                / fixed_mean.loc["mesh", "latency"]
            )
            notes.append(
                "16x16-B256-SoC: ASPL Greedy 接受吞吐 "
                f"{fixed_mean.loc['greedy', 'throughput']:.4f}，比 Mesh 的 "
                f"{fixed_mean.loc['mesh', 'throughput']:.4f} "
                f"{'高' if throughput_gain >= 0 else '低'} "
                f"{abs(throughput_gain):.1f}%；"
                "平均延迟从 "
                f"{fixed_mean.loc['mesh', 'latency']:.1f} 降到 "
                f"{fixed_mean.loc['greedy', 'latency']:.1f}"
                f"（变化 {-reduction:+.1f}%）。"
            )
        if {"mesh", "greedy", "sa"}.issubset(fixed_mean.index):
            sa_latency_gain = 100.0 * (
                1.0
                - fixed_mean.loc["sa", "latency"]
                / fixed_mean.loc["greedy", "latency"]
            )
            sa_throughput_gain = 100.0 * (
                fixed_mean.loc["sa", "throughput"]
                / fixed_mean.loc["greedy", "throughput"] - 1.0
            )
            greedy_busy = int(fixed[
                fixed.topology_class == "greedy"
            ].max_ni_busy_streak.max())
            sa_busy = int(fixed[
                fixed.topology_class == "sa"
            ].max_ni_busy_streak.max())
            greedy_watchdogs = int(fixed[
                fixed.topology_class == "greedy"
            ].ni_watchdog_triggered.sum())
            sa_watchdogs = int(fixed[
                fixed.topology_class == "sa"
            ].ni_watchdog_triggered.sum())
            notes.append(
                "16x16-B256-SoC 的 Greedy/SA 各四个 holdout seeds 均运行到 "
                "simulation limit；NI watchdog 数分别为 "
                f"{greedy_watchdogs}/{sa_watchdogs}，最大 busy streak 为 "
                f"{greedy_busy}/{sa_busy}。SA throughput="
                f"{fixed_mean.loc['sa', 'throughput']:.4f}，相对 Greedy "
                f"{sa_throughput_gain:+.1f}%；SA latency="
                f"{fixed_mean.loc['sa', 'latency']:.1f}，相对 Greedy "
                f"{-sa_latency_gain:+.1f}%。"
            )
        sa_samples = fixed[fixed.topology_class == "sa"]
        if len(sa_samples) and (
            sa_samples.average_packet_latency_cycles.max()
            > 10.0 * sa_samples.average_packet_latency_cycles.median()
        ):
            worst = sa_samples.loc[
                sa_samples.average_packet_latency_cycles.idxmax()
            ]
            stable = sa_samples.drop(index=worst.name)
            notes.append(
                "16x16-B256-SoC SA 不符合稳健性预期：holdout seed "
                f"{int(worst.seed)} 的 latency="
                f"{worst.average_packet_latency_cycles:.1f}、throughput="
                f"{worst.accepted_throughput:.4f}、max NI busy streak="
                f"{int(worst.max_ni_busy_streak)}，而其余 seeds 的 latency "
                f"为 {stable.average_packet_latency_cycles.min():.1f}--"
                f"{stable.average_packet_latency_cycles.max():.1f}。300k-cycle 复测中"
                "该 seed 的 latency 继续升至 2346、throughput 降至 0.0920，故不是"
                "短窗口统计噪声；它说明当前 simulation-guided SA placement 在共享"
                "endpoint 饱和拐点附近仍有 seed-sensitive 拥塞风险。"
            )
        random_soc = soc[soc.topology_class == "random"]
        if not random_soc.empty:
            topology_latency = random_soc.groupby(
                "topology_label"
            ).average_packet_latency_cycles.mean()
            unstable = int((topology_latency > 100.0).sum())
            if unstable:
                if unstable == len(topology_latency):
                    notes.append(
                        "16x16-B256-SoC 的 10 个 Random topology 平均 latency "
                        "全部超过 100 cycles；Random expectation 在该非均匀流量下"
                        "系统性差于 Mesh，并非由少数离群布局单独造成。"
                    )
                else:
                    notes.append(
                        f"16x16-B256-SoC Random 的 10 个 topology 中有 {unstable} 个平均 "
                        "latency>100 cycles，导致 Random expectation 的 mean 明显高于"
                        "典型布局；该长尾是真实 topology sensitivity，不应只报 median "
                        "而隐藏。"
                    )
    cross = frame[frame.section == "cross"]
    if not cross.empty:
        sa = cross[cross.topology_label.str.endswith("_sa")].groupby(
            ["topology_label", "traffic"]).accepted_throughput.mean()
        for traffic in cross.traffic.unique():
            matched = f"{traffic}_sa"
            candidates = sa.xs(traffic, level="traffic") if traffic in sa.index.get_level_values("traffic") else None
            if candidates is not None and matched in candidates.index:
                winner = candidates.idxmax()
                if winner != matched:
                    notes.append(
                        f"Cross matrix {TRAFFIC_LABELS.get(traffic, traffic)}: matched SA "
                        f"{matched}={candidates[matched]:.4f} 不是最高，{winner}="
                        f"{candidates[winner]:.4f} 更高。")
    if not notes: notes.append("自动检查未发现主要次序反转或 no-progress 异常。")
    return notes


def build_report(frame, main, static, cross, output, report):
    sections = sorted(frame.section.unique())
    failures_path = output / "failures.json"
    failures = json.loads(failures_path.read_text()) if failures_path.exists() else []
    lines = [
        "# 20260831 standalone 全实验数据（工作版）", "",
        "> 本文是自动生成的数据工作报告，不是最终提交版。主长测采用 20k warmup + "
        "100k measurement；除特别标注外均使用文档中的 B=32 标准 routing。", "",
        "## 完成范围", "",
        f"已聚合 {len(frame)} 个样本；包含 section：`{', '.join(sections)}`；"
        f"命令失败 {len(failures)} 个。", "",
        "## 0. 静态数据", "", markdown_table(static), "",
        "![topologies](../results/20260831/standalone_suite/figures/topologies.svg)", "",
        "其中 cut_v/cut_h 是穿过中央纵/横二分面的物理双向边容量（表中按物理边计数）；"
        "top8_express_mean 是所有 source-destination 候选路径中的平均 express 条数。", "",
        "## 1. 主实验", "",
        "下列三张表每格均为 `accepted throughput / packet latency`，"
        "latency 按 cycle 四舍五入到整数。", "",
        "主曲线保留原始 rate=0.05--0.80、步长 0.05 的全范围扫描，并在饱和附近"
        "补充 0.01 粒度：Bit-complement 为 0.26--0.52，Tornado 与 "
        "CutStress-bidir 为 0.36--0.80。throughput--latency 图中彩色实线是各 "
        "topology 自身的 Pareto frontier；被同一 topology 另一工作点同时以更高"
        "吞吐和更低延迟支配的过载点仍保留为灰色空心点。", "",
        "### Injection rate = 0.4", "",
        markdown_table(main_rate_table(main, .4)), "",
        "### Injection rate = 0.7", "",
        markdown_table(main_rate_table(main, .7)), "",
        "### Injection rate = 0.8", "",
        markdown_table(main_rate_table(main, .8)), "",
        "> 报告中的动态 SA 均已统一为同一套 SA-reheat。四类 traffic 使用完全相同的 "
        "算法和参数，以 ASPL Greedy 为统一 incumbent；评分等权覆盖 rate=0.05--0.80 "
        "的 16 个点，并加入最差点权重和小幅延迟惩罚。短测搜索 seeds 1/2、"
        "独立复排 seeds 3/4；复排时使用统一的 1% 逐点吞吐回退保护线，"
        "主实验使用 holdout seeds 5--8；"
        "全交叉、routing/information ablation 和 8×8/B32 scaling 也使用同版 SA。", "",
        "统一 SA 的简洁定义是：四次 restart 均从 matched ASPL Greedy 开始，"
        "使用相同的单边/双边合法交换邻域，每 20 次 proposal reheat；单 workload "
        "topology 跑 16 个 rate，Mixture topology 跑四类 workload × 16 个 rate，"
        "评分为 `mean(T/T_offered) + 0.25*min(T/T_offered) "
        "- 0.006*mean(log(1+latency)/log(1+4000))`。搜索阶段为 80 proposal/restart，"
        "温度从 0.025 降到 0.0004；traffic 只作为已知 demand 输入，不改变 SA 规则或参数。", "",
        "用同一公式在 100k-cycle holdout 主曲线上重新计算的综合结果如下：", "",
        markdown_table(sa_curve_score_table(main)), "",
        "额外的 144-case 诊断将历史 top-8 与强制保留纯 mesh 的 "
        "top-7+mesh 比较：Greedy/SA 各主流量点的 throughput 变化最大 "
        "0.77%，latency 变化最大 5.27%；因此主实验保留历史 top-8，"
        "不为这一小差异重跑全套数据。", "",
    ]
    for traffic in ("uniform_random", "tornado", "bit_complement",
                    "cutstress_bidirectional"):
        if traffic in set(main.traffic):
            lines += [f"![{traffic}](../results/20260831/standalone_suite/figures/main_{traffic}.svg)", ""]
    lines += [
        "![path](../results/20260831/standalone_suite/figures/uniform_path_behavior.svg)", "",
        "![random](../results/20260831/standalone_suite/figures/random_distribution.svg)", "",
        "## 1.4 Placement–traffic 全交叉", "",
        (markdown_table(cross, 3) if not cross.empty else "（尚无数据）"), "",
        "前五行每格依次给出 Greedy 与 SA；图中颜色只由 SA 数值决定。"
        "Random expectation 为单一 baseline。", "",
        "![cross](../results/20260831/standalone_suite/figures/placement_traffic_cross.svg)", "",
        "## 2. 次要与解释性数据", "",
        "所有 raw JSON 已保存 per-source throughput/latency、Jain fairness、directed-link "
        "utilization、平均 hops、express/packet、escape fraction，以及 overall/escape/adaptive "
        "三组 log2 latency histogram。当前 standalone 尚未记录某一固定 (s,t) 的逐候选选择次数，"
        "因此报告没有伪造该项。", "",
        "![load](../results/20260831/standalone_suite/figures/directed_link_load.svg)", "",
        "![latency](../results/20260831/standalone_suite/figures/latency_distribution.svg)", "",
        "## 3. Ablation", "",
    ]
    for section in ("routing_ablation", "information_ablation"):
        table = grouping(frame, section, ["topology_label", "configured_injection_rate"])
        if not table.empty:
            lines += [f"### {section}", "",
                      markdown_table(table[["topology_label", "configured_injection_rate",
                                            "throughput", "latency", "completed",
                                            "samples"]]), ""]
    for filename in ("routing_ablation.svg", "information_ablation.svg",
                     "escape_timeout.svg",
                     "escape_off_failures.svg"):
        if (output / "figures" / filename).exists():
            lines += [f"![{filename}](../results/20260831/standalone_suite/figures/{filename})", ""]
    escape = grouping(frame, "escape",
                      ["topology_label", "configured_injection_rate"])
    if not escape.empty:
        escape = escape[escape.configured_injection_rate.isin([.4, .7, .8])]
        lines += ["### Escape 数值摘录", "",
                  markdown_table(escape[["topology_label",
                                          "configured_injection_rate",
                                          "throughput", "latency", "escape",
                                          "completed", "samples"]]), ""]
    escape_off = frame[frame.section == "escape_off"].copy()
    if not escape_off.empty:
        escape_off["completed"] = ~escape_off.no_progress.fillna(False).astype(bool)
        escape_off = escape_off[
            escape_off.configured_injection_rate.isin([.5, .7, .8])]
        escape_off = escape_off.groupby(
            ["traffic", "configured_injection_rate"], as_index=False).agg(
                completion_probability=("completed", "mean"),
                completed=("completed", "sum"), samples=("seed", "size"))
        escape_off["traffic"] = escape_off.traffic.map(TRAFFIC_LABELS)
        lines += ["### Escape-off completion 摘录", "",
                  markdown_table(escape_off), ""]
    contrast = frame[frame.section == "escape_deadlock_contrast"].copy()
    if not contrast.empty:
        contrast["global_deadlock"] = contrast.global_no_progress_detected.fillna(False)
        contrast = contrast.groupby("topology_label", as_index=False).agg(
            global_deadlocks=("global_deadlock", "sum"),
            ni_watchdogs=("ni_watchdog_triggered", "sum"),
            drain_completed=("drain_completed", "sum"),
            samples=("seed", "size"),
            mean_deadlock_cycle=("global_no_progress_cycle", "mean"),
            mean_drain_completion_cycle=("drain_completion_cycle", "mean"),
        )
        lines += ["### Escape deadlock 对照（Uniform Greedy，rate=0.4，2 VC）", "",
                  markdown_table(contrast), "",
                  "原 4-VC Escape-off sweep 没有观察到 global deadlock，只说明在给定"
                  "负载、seed 和测量窗口中，潜在的 cyclic channel dependency 没有同时"
                  "被占满，不构成 deadlock-free 证明。2-VC 对照压缩了 adaptive 资源，"
                  "使该依赖环在 20/20 seeds 中闭合；Escape on 虽然只剩 1 个 adaptive "
                  "VC，但阻塞包可不可逆地转入专用 XY escape VC，因此 20/20 seeds 都"
                  "没有 global-no-progress 或 NI watchdog，并在有限注入后完全 drain。", "",
                  "![escape deadlock contrast](../results/20260831/standalone_suite/figures/escape_deadlock_contrast.svg)", "",
                  "对 Escape-off seed 1 在 global-no-progress cycle 5896 导出了实际的"
                  "packet/channel wait-for graph；最后一次 flit move 是 cycle 895，当时"
                  "仍有 311 个 live packets。等待图中存在一个由 20 个 packet 构成的"
                  "闭合强连通分量：其中每个 packet 请求的两个 output VC 都被该分量内"
                  "的 packet 占用。下图抽取了其中最短的、包含 express channel 的"
                  "10-channel simple cycle；两条红边 `19→51` 和 `46→14` 是 express "
                  "channel，其余为 mesh channel。", "",
                  "![observed deadlock cycle](../results/20260831/standalone_suite/figures/escape_deadlock_cycle.svg)", ""]
    lines += ["## 4. Scaling", ""]
    scaling = scaling_grouping(frame)
    if not scaling.empty:
        scaling["Name"] = scaling.apply(
            lambda row: (SCALING_LABELS.get(int(row.scaling_row),
                                             str(int(row.scaling_row)))
                         + ":" + str(row.topology_class)), axis=1,
        )
        scaling["_order"] = scaling.scaling_row.map(
            {row: index for index, row in enumerate(SCALING_ORDER)})
        scaling = scaling.sort_values(["_order", "topology_class"])
        lines += [markdown_table(scaling[["Name",
                                         "configured_injection_rate",
                                         "throughput", "latency",
                                         "hops", "max_load", "fairness", "completed",
                                         "samples"]]), ""]
    if (output / "figures" / "scaling_throughput.svg").exists():
        lines += ["![scaling](../results/20260831/standalone_suite/figures/scaling_throughput.svg)", ""]
    if (output / "figures" / "scaling_latency.svg").exists():
        lines += ["![scaling latency](../results/20260831/standalone_suite/figures/scaling_latency.svg)", ""]
    lines += [
        "前八组 Uniform 配置中，SA 均匹配或提高 Greedy throughput，说明其优势"
        "能够跨 budget、VC、长度相关延迟、两 flit packet 和 16×16 网络保持。"
        "SoC 的 R=0.55 近饱和点更苛刻：Mesh/Random 进入长期拥塞；256-cycle "
        "local escape timeout 下 ASPL Greedy 保持为稳定的中间点，而按完整配置"
        "重搜的 SA 在四个 holdout seed 上进一步提高吞吐、降低延迟，且 "
        "Greedy/SA 均无 watchdog。", "",
    ]
    if (output / "figures" / "row8_traffic.svg").exists():
        lines += [
            "![16x16-B256-SoC traffic](../results/20260831/standalone_suite/figures/row8_traffic.svg)", "",
            "![16x16-B256-SoC SA topology](../results/20260831/standalone_suite/figures/row8_sa_topology.svg)", "",
            "![16x16-B256-SoC SA load](../results/20260831/standalone_suite/figures/row8_sa_load.svg)", "",
        ]
    lines += [
        "Scaling 配置使用与图横轴一致的语义名称：`8x8-B32` 是完整参考；"
        "`8x8-B64`/`8x8-B128` 只改变 wire budget；`8x8-B32-VC8` 只改为 8 VC；"
        "`8x8-B64-L2` 使用 B64 与 ceil(wire/2)；`8x8-B64-2flit` 使用 B64、"
        "2-flit packet 和 timeout64；`16x16-B256` 同比例扩展尺寸、budget、"
        "最短线长、K、VC 和 timeout；`16x16-B256-L4` 再令 express latency="
        "ceil(wire/4)。", "",
        "`16x16-B256-SoC` 保持 ideal-latency `16x16-B256` 的网络资源和 routing "
        "policy，但 traffic 改为 heterogeneous-SoC、rate=0.55，并将纯本地的 "
        "escape timeout 从 64 增至 256，以避免正常 adaptive congestion 被过早"
        "导入单一 escape VC；ASPL Greedy 和 SA 均针对完整配置重新评估/布线。", "",
        "16×16 的 B256 来自面积与线性尺寸共同缩放：B32×4 routers×2 wire length；"
        "minimum length 3→6、K 8→16、VC 4→8。Random 是 10 个独立 topology×2 traffic "
        "seed 的 expectation；`16x16-B256` 固定 rate=0.45 和 admission=1.0，只将 8×8 的 "
        "escape timeout=32 按线性尺寸翻倍为 64，K16 和 routing 算法不变。"
        "Random 保留为诊断数据，不用于选择该工作点。", "",
        "每个 scaling 配置的 SA 均按其完整参数独立搜索；8×8 hardware ablation "
        "统一扫 rate=0.05--0.80，两个 16×16 Uniform 配置扫 0.35/0.40/0.45。"
        "`16x16-B256-SoC` 的 SA 曲线目标覆盖 0.45/0.50/0.55；该 workload 最热 "
        "endpoint 负载为均匀端点均值的 3 倍，对应 configured-rate 硬上限为 2/3。"
        "它从原始 traffic-aware ASPL Greedy 初态开始，search 使用 traffic seeds 1/2，"
        "扩展 validation 使用 seeds 3/4/9--16，最终表对 Mesh/Greedy/SA 使用此前"
        "未查看的连续 holdout seeds 17--20；只调整离散 search-budget 参数，不使用"
        "手工 repair 或挑选 final traffic seed。SA 使用预先固定的 placement-search "
        "seeds 42--45、每链 4 restarts，并合并比较全部 16 个长测 elite。"
        "同一 holdout block 延长到 300k measurement 后，Mesh/Greedy/SA 的 throughput "
        "为 0.1742/0.2168/0.2444，latency 为 26425/14425/7280 cycles，严格排序"
        "保持且 Greedy/SA 均无 watchdog；三者均低于 offered=0.275，所以 latency "
        "表示过载积压而非有限稳态队列。"
        "两个 length-aware 配置的 ASPL Greedy 也按相应 edge cost 重新布线，"
        "不是固定 ideal-latency placement 后只改测量参数。"
        "注意：`8x8-B64-2flit` 将 escape timeout 从 32 按两 flit 的序列化比例扩展到 64；"
        "当前 packet_flits>1 是 packet-granular serialization 近似，不是逐 flit wormhole；"
        "8×8/B64/B128 已改用完整 ASPL Greedy 与统一 SA；16×16 Greedy 使用同一 ASPL "
        "目标的可扩展候选预筛，SA 仍使用相同的 simulation-guided procedure。", "",
        "## 异常和不符合预期的现象", "",
    ]
    lines += [f"- {note}" for note in anomalies(frame, main)]
    lines += ["", "## Garnet 一致性状态", "",
              "旧 V5 instant-info 校准的六组案例满足 throughput <1%、latency <2%。下表合并"
              "已有 formal V8、8×8/B=64、rate=0.8 长测和 9-seed B32/Uniform/ASPL/"
              "rate=0.7 长测。Garnet 的同周期诊断表明：1 GHz tester / 2 GHz Ruby 下，"
              "一半周期的首次 NI query 会在 periodic registration-control event 之前"
              "惰性快照 q/r。standalone 改为复现这一交替顺序后，七组 throughput 全部"
              "位于 Garnet 的 1.7% 内；四组 latency 在 3% 内，两组仅差 3.04%/3.13%，"
              "Tornado Greedy 饱和拐点仍高 12.8%。因此不能把 standalone 主结果直接"
              "冒充 Garnet 最终结果。同一 Tornado Greedy 的 200+2k instrumented 短测中，"
              "throughput/latency 仅差 -0.41%/-0.88%，observed q/r 均值仅差 "
              "-1.03%/+2.68%，说明长测 latency 差异是在饱和拐点累积放大的。候选表逐元素审计覆盖全部 "
              "4032 个非本地 source--destination pair，未发现差异；q=r=0 的静态候选隔离"
              "实验吞吐/延迟差异仅 0.02%/0.26%，q-only 吞吐也只差 0.18%。因此剩余误差"
              "不是 traffic、top-K、链路带宽或基本 VC 时序错误，而是 delayed r 与 q 联合"
              "反馈在 Tornado 饱和点进入不同拥塞分支。强制所有 snapshot 提前、提前 credit、"
              "以及去掉 tester-to-NI 一周期边界都会使多组校准明显恶化；没有把"
              "traffic-specific fudge 写入模型。另已修正 q 诊断曾误报发送队列长度的问题，当前统计与路由"
              "一样均报告 occupied adaptive express VCs。", ""]
    calibration_path = (PROJECT / "results" / "20260905" /
                        "garnet_calibration_event_order_fix" / "summary.json")
    if calibration_path.exists():
        calibration = pd.DataFrame(json.loads(calibration_path.read_text(encoding="utf-8")))
        lines += [markdown_table(calibration[[
            "calibration_set", "traffic", "topology", "sample_count",
            "garnet_throughput", "standalone_throughput",
            "throughput_delta_percent", "garnet_latency", "standalone_latency",
            "latency_delta_percent",
        ]], 3), ""]
    report.write_text("\n".join(lines), encoding="utf-8")


def main_cli():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()
    results = args.input_dir / "results.json"
    if not results.exists(): raise SystemExit(f"missing {results}")
    rows = json.loads(results.read_text(encoding="utf-8"))
    rows = [row for row in rows if "error" not in row]
    frame = pd.DataFrame(rows)
    diagnostic_path = args.input_dir / "diagnostics.json"
    diagnostics = pd.DataFrame(json.loads(diagnostic_path.read_text(
        encoding="utf-8"))) if diagnostic_path.exists() else frame
    figures = args.input_dir / "figures"; figures.mkdir(parents=True, exist_ok=True)
    paths = topology_paths()
    static = pd.DataFrame([static_row(label, path) for label, path in paths.items()])
    # Preserve the original 20-layout static-topology summary.  The dedicated
    # throughput-distribution figure deliberately uses 200 layouts instead.
    random_paths = sorted(
        (PROJECT / "results" / "20260831" / "placements" /
         "random_b32").glob("p*/random.json"),
        key=lambda path: int(path.parent.name.removeprefix("p")),
    )[:20]
    if random_paths:
        random_rows = pd.DataFrame([static_row("Random", path) for path in random_paths])
        mean = random_rows.select_dtypes(include="number").mean().to_dict()
        static = pd.concat([static, pd.DataFrame([{"Topology": "Random mean", **mean}])],
                           ignore_index=True)
    static.to_csv(args.input_dir / "static_topology.csv", index=False)
    visual_paths = {
        "Mesh": paths["Mesh"],
        "Representative Random (p1)": (
            PROJECT / "results" / "20260831" / "placements" /
            "random_b32" / "p1" / "random.json"
        ),
        "Uniform ASPL Greedy": paths["Uniform ASPL Greedy"],
        "Uniform SA": paths["Uniform SA"],
    }
    draw_topologies(visual_paths, figures)
    main = plot_main(frame, figures)
    cross = plot_cross(frame, figures)
    plot_random_distribution(frame, figures)
    plot_generic_ablations(frame, figures)
    plot_escape_off(frame, figures)
    plot_escape_deadlock_contrast(frame, figures)
    plot_link_load(diagnostics, figures)
    plot_latency_histograms(diagnostics, figures)
    plot_scaling(frame, figures)
    plot_row8_explanations(diagnostics, figures)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    build_report(frame, main, static, cross, args.input_dir, args.report)
    print(f"wrote {args.report} and {figures}")


if __name__ == "__main__":
    main_cli()
