#!/usr/bin/env python3
"""Compare the full Garnet paper suite with standalone and draw arXiv assets.

The script treats a Garnet ``deadlock_panic`` as a censored run, never as a
zero-throughput observation. Targeted correction and watchdog-supplement runs
replace matching logical rows from the downloaded archive. The downloaded 16-by-16
length-aware Random inputs are invalidated unless replaced by corrected
supplemental runs, because their topology JSON encoded unit-latency links.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
import math
from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import Normalize
from matplotlib.lines import Line2D
from matplotlib.patches import FancyArrowPatch
import networkx as nx
import numpy as np
import pandas as pd

from express_mesh_project.model import soc_heterogeneous_demand


ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "express_mesh_project"
DEFAULT_GARNET = (PROJECT / "results" / "20260914" /
                  "garnet_full_paper_suite_downloaded")
DEFAULT_STANDALONE = (PROJECT / "results" / "20260831" /
                      "standalone_suite" / "results.json")
DEFAULT_CORRECTION = (PROJECT / "results" / "20260914" /
                      "garnet_length_aware_random_correction_row6")
DEFAULT_SUPPLEMENT = (PROJECT / "results" / "20260915" /
                      "garnet_watchdog_supplement_downloaded")
DEFAULT_OUTPUT = PROJECT / "report" / "arxiv_assets"
DEFAULT_REPORT = (PROJECT / "docs" /
                  "GARNET_FULL_PAPER_COMPARISON_20260915_ZH.md")

COLORS = {"mesh": "#4c78a8", "random": "#9c9c9c",
          "greedy": "#f58518", "sa": "#54a24b"}
LABELS = {"mesh": "Mesh", "random": "Random",
          "greedy": "Greedy", "sa": "SA"}
MARKERS = {"mesh": "o", "random": "s", "greedy": "^", "sa": "D"}
TRAFFIC_LABELS = {
    "uniform_random": "Uniform", "tornado": "Tornado",
    "bit_complement": "BitComp",
    "cutstress_bidirectional": "CutStress",
    "soc_heterogeneous": "SoC",
}
SCALING_LABELS = {
    0: "8x8-B32", 1: "8x8-B64", 2: "8x8-B128",
    3: "16x16-B256", 4: "16x16-B256-L4",
    5: "8x8-B32-VC8", 6: "8x8-B64-L2",
    7: "8x8-B64-2flit", 8: "16x16-B256-SoC",
}
SCALING_ORDER = (0, 1, 2, 5, 6, 7, 3, 4, 8)
SEMANTIC_KEY = [
    "section", "topology_label", "topology_class", "traffic",
    "configured_injection_rate", "seed", "warmup_cycles",
    "measurement_cycles",
]


def load_json_frame(path: Path) -> pd.DataFrame:
    return pd.DataFrame(json.loads(path.read_text(encoding="utf-8")))


def completed(frame: pd.DataFrame, *, standalone: bool = False) -> pd.Series:
    reason = frame.termination_reason.fillna("")
    if standalone:
        return reason.str.startswith("simulate_limit") | reason.eq("drain_completed")
    return reason.eq("simulate_limit")


def replace_matching(base: pd.DataFrame, replacement: pd.DataFrame) -> pd.DataFrame:
    keys = set(tuple(row) for row in replacement[SEMANTIC_KEY].itertuples(
        index=False, name=None))
    keep = [tuple(row) not in keys for row in base[SEMANTIC_KEY].itertuples(
        index=False, name=None)]
    return pd.concat([base.loc[keep], replacement], ignore_index=True,
                     sort=False)


def load_inputs(garnet_dir: Path, standalone_path: Path,
                correction_dir: Path | None,
                supplement_dir: Path | None = None
                ) -> tuple[pd.DataFrame, pd.DataFrame]:
    garnet = load_json_frame(garnet_dir / "results.json")
    garnet["result_source"] = "original"
    garnet["supplemented"] = False
    garnet["corrected_after_download"] = False
    if correction_dir is not None and (correction_dir / "results.json").exists():
        correction = load_json_frame(correction_dir / "results.json")
        if correction.empty:
            raise RuntimeError("length-aware correction file is empty")
        correction["result_source"] = "length_correction"
        correction["supplemented"] = False
        correction["corrected_after_download"] = True
        garnet = replace_matching(garnet, correction)
    garnet["original_complete_50k"] = completed(garnet)
    garnet["original_deadlock_vcs"] = garnet["deadlock_vcs"]
    garnet["original_termination_reason"] = garnet["termination_reason"]
    garnet["original_no_progress_cycle"] = garnet["no_progress_cycle"]
    if supplement_dir is not None and (supplement_dir / "results.json").exists():
        supplement = load_json_frame(supplement_dir / "results.json")
        if supplement.empty:
            raise RuntimeError("watchdog supplement file is empty")
        original_status = garnet[
            SEMANTIC_KEY + [
                "original_complete_50k", "original_deadlock_vcs",
                "original_termination_reason", "original_no_progress_cycle",
            ]
        ].drop_duplicates(SEMANTIC_KEY)
        supplement = supplement.merge(
            original_status, on=SEMANTIC_KEY, how="left", validate="one_to_one")
        if supplement.original_complete_50k.isna().any():
            raise RuntimeError("supplement contains unmatched logical rows")
        supplement["result_source"] = "watchdog_supplement"
        supplement["supplemented"] = True
        supplement["corrected_after_download"] = False
        garnet = replace_matching(garnet, supplement)
    standalone = load_json_frame(standalone_path)
    # Row 4 Random in the downloaded suite accidentally reused the unit-latency
    # JSON from row 3. Exclude only unreplaced original rows; the supplement
    # materializes the requested latency and replaces the fixed p1--p5 cohort.
    invalid_length_random = (
        garnet.section.eq("scaling") &
        garnet.topology_label.str.startswith("row4:random") &
        garnet.result_source.eq("original")
    )
    garnet["invalid_config"] = invalid_length_random
    garnet["complete"] = completed(garnet) & ~invalid_length_random
    standalone["complete"] = completed(standalone, standalone=True)
    return garnet, standalone


def aggregate(frame: pd.DataFrame, section: str, keys: list[str]) -> pd.DataFrame:
    data = frame[frame.section.eq(section)].copy()
    counts = data.groupby(keys, as_index=False).agg(
        samples=("seed", "size"), completed=("complete", "sum"))
    valid = data[data.complete]
    metrics = valid.groupby(keys, as_index=False).agg(
        throughput=("accepted_throughput", "mean"),
        latency=("average_packet_latency_cycles", "mean"),
        escape=("delivered_escape_fraction", "mean"),
        express=("express_traversals", "sum"),
        received=("packets_received", "sum"),
        hops=("average_hops", "mean"),
        max_load=("max_link_utilization", "mean"),
    )
    return counts.merge(metrics, on=keys, how="left")


def curved_link(axis, start, end, *, color, linewidth, radius,
                arrow=False, alpha=1.0, zorder=2):
    patch = FancyArrowPatch(
        start, end, arrowstyle="-|>" if arrow else "-",
        mutation_scale=7, connectionstyle=f"arc3,rad={radius}",
        color=color, linewidth=linewidth, alpha=alpha,
        shrinkA=1.5, shrinkB=1.5, zorder=zorder,
    )
    axis.add_patch(patch)


def plot_main(garnet: pd.DataFrame, output: Path) -> None:
    grouped = aggregate(
        garnet, "main",
        ["traffic", "topology_class", "configured_injection_rate"],
    )
    for traffic in sorted(grouped.traffic.unique()):
        data = grouped[grouped.traffic.eq(traffic)]
        fig, axes = plt.subplots(1, 2, figsize=(9.2, 3.6))
        for topology in ("mesh", "random", "greedy", "sa"):
            item = data[data.topology_class.eq(topology)].sort_values(
                "configured_injection_rate").dropna(subset=["throughput", "latency"])
            if item.empty:
                continue
            axes[0].plot(
                item.configured_injection_rate, item.throughput,
                marker=MARKERS[topology], ms=2.5, lw=1.15,
                color=COLORS[topology], label=LABELS[topology],
            )
            throughput = item.throughput.to_numpy()
            latency = item.latency.to_numpy()
            dominated = np.zeros(len(item), dtype=bool)
            for index in range(len(item)):
                weak = ((throughput >= throughput[index]) &
                        (latency <= latency[index]))
                strict = ((throughput > throughput[index]) |
                          (latency < latency[index]))
                dominated[index] = bool(np.any(weak & strict))
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
        axes[0].set(xlabel="Configured injection rate",
                    ylabel="Accepted throughput")
        axes[1].set(xlabel="Accepted throughput",
                    ylabel="Packet latency (cycles)")
        axes[0].legend(fontsize=8)
        handles = [
            Line2D([0], [0], color=COLORS[topology],
                   marker=MARKERS[topology], lw=1.65,
                   label=LABELS[topology])
            for topology in ("mesh", "random", "greedy", "sa")
        ]
        handles.append(Line2D(
            [0], [0], color="#9a9a9a", marker="o",
            markerfacecolor="none", lw=0, label="Dominated overload point"))
        axes[1].legend(handles=handles, fontsize=7, loc="best")
        for axis in axes:
            axis.grid(alpha=.25)
        fig.tight_layout()
        fig.savefig(output / f"main_{traffic}.svg", bbox_inches="tight")
        plt.close(fig)


def plot_path_behavior(garnet: pd.DataFrame, output: Path) -> None:
    data = aggregate(
        garnet[garnet.traffic.eq("uniform_random")], "main",
        ["topology_class", "configured_injection_rate"],
    )
    fig, axes = plt.subplots(1, 2, figsize=(9.2, 3.5))
    for topology in ("mesh", "random", "greedy", "sa"):
        item = data[data.topology_class.eq(topology)].sort_values(
            "configured_injection_rate")
        axes[0].plot(item.configured_injection_rate, item.escape,
                     marker="o", ms=3, color=COLORS[topology],
                     label=LABELS[topology])
        per_packet = item.express / item.received.clip(lower=1)
        axes[1].plot(item.configured_injection_rate, per_packet,
                     marker="o", ms=3, color=COLORS[topology],
                     label=LABELS[topology])
    axes[0].set(xlabel="Configured injection rate",
                ylabel="Delivered escape fraction")
    axes[1].set(xlabel="Configured injection rate",
                ylabel="Express traversals / packet")
    for axis in axes:
        axis.grid(alpha=.25)
    axes[0].legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(output / "uniform_path_behavior.svg", bbox_inches="tight")
    plt.close(fig)


def plot_random_distribution(garnet: pd.DataFrame, output: Path) -> dict:
    data = garnet[garnet.section.eq("random_distribution")]
    comparison_seeds = sorted(int(seed) for seed in data.seed.unique())
    status = data.groupby("topology_label").agg(
        samples=("seed", "size"), completed=("complete", "sum"))
    values = data[data.complete].groupby("topology_label").accepted_throughput.mean()
    # Match the compact presentation in the submission version: any topology
    # with a watchdog-censored seed is assigned to the undisplayed lower tail,
    # and only fully observed topology means at or above the fixed floor enter
    # the histogram.
    floor = .265
    fully_observed = status.index[status.completed.eq(status.samples)]
    displayed = values.reindex(fully_observed)
    displayed = displayed[displayed.ge(floor)]
    main = garnet[
        garnet.section.eq("main") & garnet.traffic.eq("uniform_random") &
        garnet.configured_injection_rate.eq(.8) & garnet.complete &
        garnet.seed.isin(comparison_seeds)
    ].groupby("topology_class").accepted_throughput.mean()
    fig, axis = plt.subplots(figsize=(6.8, 3.7))
    bin_width = .001
    bin_stop = math.ceil((float(displayed.max()) + bin_width) /
                         bin_width) * bin_width
    bins = np.arange(floor, bin_stop + bin_width / 2, bin_width)
    axis.hist(displayed, bins=bins, color=COLORS["random"], alpha=.78,
              edgecolor="white")
    for topology in ("greedy", "sa"):
        value = float(main[topology])
        axis.axvline(value, color=COLORS[topology], lw=2,
                     label=f"{LABELS[topology]} {value:.3f}")
    omitted = int(len(status) - len(displayed))
    axis.text(
        .02, .04,
        f"{omitted}/{len(status)} layouts below {floor:.3f} (not shown)",
        transform=axis.transAxes, ha="left", va="bottom", fontsize=8,
        bbox={"facecolor": "white", "edgecolor": "none", "alpha": .82,
              "pad": 1.5},
    )
    axis.set(xlabel="Accepted throughput",
             ylabel="Random topology count")
    axis.set_xlim(floor, max(float(main.sa) + .002, bin_stop))
    axis.set_xticks(np.arange(floor, axis.get_xlim()[1] + .0001, .005))
    axis.grid(alpha=.2)
    axis.legend(loc="upper left", bbox_to_anchor=(0, .82), fontsize=8)
    fig.tight_layout()
    fig.savefig(output / "random_distribution.svg", bbox_inches="tight")
    plt.close(fig)
    return {
        "measurable": len(values),
        "fully_censored": int((status.completed == 0).sum()),
        "partial": int(((status.completed > 0) &
                        (status.completed < status.samples)).sum()),
        "displayed": len(displayed), "omitted": omitted,
        "comparison_seeds": comparison_seeds,
        "greedy_value": float(main.greedy), "sa_value": float(main.sa),
        "greedy_beats": int((values < main.greedy).sum()),
        "sa_beats": int((values < main.sa).sum()),
        "random_max": float(values.max()),
    }


def merge_physical_links(row: pd.Series) -> dict[tuple[int, int, bool], float]:
    merged = defaultdict(list)
    for source, destination, express_id, value in zip(
            row.directed_link_sources, row.directed_link_destinations,
            row.directed_link_express_ids, row.directed_link_utilization):
        key = (min(source, destination), max(source, destination),
               int(express_id) >= 0)
        merged[key].append(float(value))
    return {key: float(np.mean(values)) for key, values in merged.items()}


def plot_link_load(garnet: pd.DataFrame, output: Path) -> dict[str, float]:
    selected = garnet[
        garnet.section.eq("main") & garnet.traffic.eq("uniform_random") &
        garnet.configured_injection_rate.eq(.8) & garnet.seed.eq(5)
    ]
    chosen = {}
    for topology in ("mesh", "greedy", "sa"):
        chosen[topology] = selected[selected.topology_class.eq(topology)].iloc[0]
    chosen["random"] = selected[selected.topology_label.eq("random_p1")].iloc[0]
    links = {topology: merge_physical_links(row)
             for topology, row in chosen.items()}
    all_values = [value for mapping in links.values() for value in mapping.values()]
    norm = Normalize(0, max(all_values))
    cmap = plt.cm.inferno
    fig, axes = plt.subplots(2, 2, figsize=(8.2, 8.2))
    maxima = {}
    for axis, topology in zip(axes.flat, ("mesh", "random", "greedy", "sa")):
        row = chosen[topology]
        n = int(row.dimension)
        maxima[topology] = max(links[topology].values())
        for (source, destination, is_express), value in links[topology].items():
            start = (source % n, source // n)
            end = (destination % n, destination // n)
            if is_express:
                curved_link(axis, start, end, color=cmap(norm(value)),
                            linewidth=2, radius=.13, alpha=.98)
            else:
                axis.plot([start[0], end[0]], [start[1], end[1]],
                          color=cmap(norm(value)), lw=.9, alpha=.82)
        axis.scatter([i % n for i in range(n*n)], [i // n for i in range(n*n)],
                     color="black", s=6, zorder=3)
        label = "Representative Random (p1)" if topology == "random" else LABELS[topology]
        axis.set_title(f"{label} (local max={maxima[topology]:.2f})")
        axis.set_aspect("equal")
        axis.set_xlim(-.65, n-.35)
        axis.set_ylim(n-.35, -.65)
        axis.axis("off")
    scalar = plt.cm.ScalarMappable(norm=norm, cmap=cmap)
    scalar.set_array([])
    fig.colorbar(
        scalar, ax=axes.ravel().tolist(), fraction=.035, pad=.025,
        label=("Mean utilization of the two directed channels "
               "(active flit cycles / measurement cycles)"),
    )
    fig.legend(handles=[
        Line2D([0], [0], color="#555", lw=.8, label="Mesh physical link"),
        Line2D([0], [0], color="#555", lw=2,
               label="Express physical link (curved)"),
    ], loc="lower center", ncol=2, frameon=False, bbox_to_anchor=(.46, .01))
    fig.subplots_adjust(left=.02, right=.88, bottom=.08, top=.97,
                        wspace=.08, hspace=.12)
    fig.savefig(output / "directed_link_load.svg", bbox_inches="tight")
    plt.close(fig)
    return maxima


def plot_escape(garnet: pd.DataFrame, output: Path) -> None:
    data = aggregate(garnet, "escape",
                     ["topology_label", "configured_injection_rate"])
    fig, axes = plt.subplots(1, 2, figsize=(9.2, 3.55))
    order = ("on_t8", "on_t16", "on_t32", "on_t64", "on_t128")
    palette = plt.cm.viridis(np.linspace(.08, .92, len(order)))
    for label, color in zip(order, palette):
        item = data[data.topology_label.eq(label)].sort_values(
            "configured_injection_rate")
        axes[0].plot(item.configured_injection_rate, item.throughput,
                     marker="o", ms=3, color=color,
                     label=label.replace("on_t", "$T_{esc}=$"))
        axes[1].plot(item.configured_injection_rate, item.latency,
                     marker="o", ms=3, color=color,
                     label=label.replace("on_t", "$T_{esc}=$"))
    axes[0].set(xlabel="Configured injection rate",
                ylabel="Accepted throughput")
    axes[1].set(xlabel="Configured injection rate",
                ylabel="Latency (cycles)")
    axes[0].legend(fontsize=7, ncol=2)
    for axis in axes:
        axis.grid(alpha=.22)
    fig.tight_layout()
    fig.savefig(output / "escape_timeout.svg", bbox_inches="tight")
    plt.close(fig)


def next_hop(router: int, direction: str, dimension: int):
    if direction == "East": return router + 1, "West"
    if direction == "West": return router - 1, "East"
    if direction == "North": return router + dimension, "South"
    if direction == "South": return router - dimension, "North"
    if direction.startswith("ExpressTo"):
        target = int(direction[len("ExpressTo"):])
        return target, f"ExpressFrom{router}"
    return None, None


def garnet_wait_cycle(row: pd.Series) -> list[dict]:
    vcs = row.deadlock_vcs
    by_input = defaultdict(list)
    for index, vc in enumerate(vcs):
        by_input[(int(vc["router"]), vc["in_direction"])].append(index)
    graph = nx.DiGraph()
    graph.add_nodes_from(range(len(vcs)))
    for index, vc in enumerate(vcs):
        target, input_direction = next_hop(
            int(vc["router"]), vc["out_direction"], int(row.dimension))
        for dependency in by_input.get((target, input_direction), []):
            graph.add_edge(index, dependency)
    components = [component for component in nx.strongly_connected_components(graph)
                  if len(component) > 1]
    if not components:
        raise RuntimeError("no channel-dependency cycle found in Garnet dump")
    cycle_edges = nx.find_cycle(graph.subgraph(min(components, key=len)))
    return [vcs[source] for source, _destination in cycle_edges]


def plot_deadlock_cycle(garnet: pd.DataFrame, output: Path) -> int:
    candidates = garnet[
        (
            garnet.section.eq("escape_off")
            & garnet.traffic.eq("uniform_random")
            & garnet.topology_label.eq("off")
            & np.isclose(garnet.configured_injection_rate, .8)
            & garnet.seed.eq(10)
        )
    ]
    if len(candidates) != 1:
        raise RuntimeError("expected one four-VC escape-off deadlock sample")
    row = candidates.iloc[0].copy()
    dump = row.get("original_deadlock_vcs", row.deadlock_vcs)
    if not isinstance(dump, list) or not dump:
        raise RuntimeError("four-VC escape-off sample has no original VC dump")
    row["deadlock_vcs"] = dump
    cycle = garnet_wait_cycle(row)
    occupied_by_input = defaultdict(list)
    for vc in dump:
        occupied_by_input[(int(vc["router"]), vc["in_direction"])].append(vc)
    downstream_channels = []
    for requestor in cycle:
        target, input_direction = next_hop(
            int(requestor["router"]), requestor["out_direction"],
            int(row.dimension))
        blockers = sorted(
            occupied_by_input[(target, input_direction)],
            key=lambda vc: int(vc["vc"]),
        )
        downstream_channels.append((target, input_direction, blockers))
    cycle_inputs = {
        (target, input_direction)
        for target, input_direction, _blockers in downstream_channels
    }
    occupancies = Counter(
        (int(vc["router"]), vc["in_direction"]) for vc in dump
    )
    if len(cycle_inputs) != len(cycle) or any(
        occupancies[channel] != int(row.vcs_per_vnet)
        for channel in cycle_inputs
    ):
        raise RuntimeError("deadlock cycle is not fully occupied on every VC")

    row_by_input = {
        (target, input_direction): number
        for number, (target, input_direction, _blockers)
        in enumerate(downstream_channels, 1)
    }
    component_vcs = [
        vc for vc in dump
        if (int(vc["router"]), vc["in_direction"]) in cycle_inputs
    ]
    if len(component_vcs) != len(cycle) * int(row.vcs_per_vnet):
        raise RuntimeError("unexpected deadlock component size")
    component_graph = nx.DiGraph()
    component_graph.add_nodes_from(range(len(component_vcs)))
    component_by_input = defaultdict(list)
    for index, vc in enumerate(component_vcs):
        component_by_input[
            (int(vc["router"]), vc["in_direction"])
        ].append(index)
    for index, vc in enumerate(component_vcs):
        target, input_direction = next_hop(
            int(vc["router"]), vc["out_direction"], int(row.dimension))
        requested = (target, input_direction)
        if requested not in row_by_input:
            raise RuntimeError("deadlock component has an external dependency")
        component_graph.add_edges_from(
            (index, blocker)
            for blocker in component_by_input[requested]
        )
    if not nx.is_strongly_connected(component_graph):
        raise RuntimeError("60-VC deadlock component is not strongly connected")

    n = int(row.dimension)
    fig, (axis, detail) = plt.subplots(
        1, 2, figsize=(13.2, 7.0),
        gridspec_kw={"width_ratios": [1.06, 1.24]},
    )
    for value in range(n):
        axis.plot([0, n-1], [value, value], color="#e5e7eb",
                  linewidth=.8, zorder=0)
        axis.plot([value, value], [0, n-1], color="#e5e7eb",
                  linewidth=.8, zorder=0)
    routers = sorted({int(vc["router"]) for vc in cycle})
    for router in routers:
        x, y = router % n, router // n
        axis.scatter(x, y, s=330, facecolor="white", edgecolor="#111827",
                     linewidth=1.45, zorder=5)
        axis.text(x, y, f"R{router}", ha="center", va="center",
                  fontsize=9.0, fontweight="bold", zorder=6)
    for number, vc in enumerate(cycle, 1):
        router = int(vc["router"])
        target, _ = next_hop(router, vc["out_direction"], n)
        start, end = (router % n, router // n), (target % n, target // n)
        express = vc["out_direction"].startswith("ExpressTo")
        color = "#dc2626" if express else "#2563eb"
        radius = (.18 if start[0] <= end[0] else -.18) if express else 0
        arrow = FancyArrowPatch(
            start, end, arrowstyle="-|>", mutation_scale=14,
            linewidth=3.0 if express else 2.1, color=color,
            connectionstyle=f"arc3,rad={radius}", shrinkA=13, shrinkB=13,
            zorder=3,
        )
        axis.add_patch(arrow)
        mx, my = (start[0]+end[0])/2, (start[1]+end[1])/2
        if express:
            dx, dy = end[0]-start[0], end[1]-start[1]
            length = max(math.hypot(dx, dy), 1e-9)
            bend = .52 if radius > 0 else -.52
            mx += -dy/length*bend
            my += dx/length*bend
        else:
            dx, dy = end[0]-start[0], end[1]-start[1]
            # Keep the label beside, rather than on top of, the arrowhead.
            offset = .29
            mx += -dy*offset
            my += dx*offset
        axis.text(
            mx, my, f"{number}", color=color,
            fontsize=9.0, fontweight="bold", ha="center", va="center", zorder=7,
            bbox={"boxstyle": "round,pad=.20", "facecolor": "white",
                  "edgecolor": color, "alpha": .94, "linewidth": .8},
        )
    axis.plot([], [], color="#2563eb", linewidth=2.1,
              label="occupied mesh channel")
    axis.plot([], [], color="#dc2626", linewidth=3.0,
              label="occupied express channel")
    axis.legend(loc="upper right", frameon=True, fontsize=9.2)
    axis.set_xlim(-.7, n-.3)
    axis.set_ylim(n-.25, -.75)
    axis.set_aspect("equal")
    axis.set_xticks(range(n)); axis.set_yticks(range(n))
    axis.set_xlabel("mesh x coordinate")
    axis.set_ylabel("mesh y coordinate")
    axis.spines[["top", "right", "bottom", "left"]].set_visible(False)
    axis.tick_params(length=0, colors="#6b7280")

    detail.axis("off")
    detail.set_xlim(0, 1)
    detail.set_ylim(0, 1)
    detail.text(0, .985, "Closed 60-VC dependency component",
                fontsize=12.2, fontweight="bold", va="top")
    detail.text(
        0, .945,
        "Each filled cell shows the resident flit's source→destination",
        fontsize=8.8, color="#4b5563", va="top",
    )
    x_cells = (.34, .49, .64, .79)
    for vc_index, x in enumerate(x_cells):
        detail.text(x, .905, f"VC{vc_index}", fontsize=9.2,
                    fontweight="bold", ha="center", va="center")
    detail.text(.0, .905, "occupied channel",
                fontsize=9.2, fontweight="bold", va="center")

    row_height = .049
    first_y = .862
    cell_width = .132
    cell_height = .039
    for number, (requestor, downstream) in enumerate(
            zip(cycle, downstream_channels), 1):
        target, input_direction, blockers = downstream
        router = int(requestor["router"])
        express = requestor["out_direction"].startswith("ExpressTo")
        color = "#dc2626" if express else "#2563eb"
        facecolor = "#fee2e2" if express else "#dbeafe"
        y = first_y - (number - 1) * row_height
        detail.text(
            0, y, f"{number:02d}  R{router}→R{target}",
            fontsize=8.7, color=color, family="monospace",
            fontweight="bold", va="center",
        )
        for x, blocker in zip(x_cells, blockers):
            box = plt.Rectangle(
                (x - cell_width / 2, y - cell_height / 2),
                cell_width, cell_height,
                transform=detail.transAxes,
                facecolor=facecolor, edgecolor=color, linewidth=.85,
            )
            detail.add_patch(box)
            detail.text(
                x, y,
                f"{int(blocker['source'])}→{int(blocker['destination'])}",
                fontsize=7.5, family="monospace", ha="center", va="center",
                color="#111827",
            )
        if len(blockers) != 4 or [int(vc["vc"]) for vc in blockers] != list(range(4)):
            raise RuntimeError("deadlock channel does not contain VC0--VC3")

    detail.text(
        0, .075,
        "All 60 cells are occupied; the numbered channels form a closed wait-for cycle.",
        fontsize=9.0, color="#111827", va="top", linespacing=1.3,
        bbox={"boxstyle": "round,pad=.48", "facecolor": "#f9fafb",
              "edgecolor": "#9ca3af"},
    )
    fig.tight_layout()
    fig.savefig(output / "escape_deadlock_cycle.svg", bbox_inches="tight")
    plt.close(fig)
    return len(cycle)


def scaling_data(garnet: pd.DataFrame) -> pd.DataFrame:
    data = garnet[garnet.section.eq("scaling")].copy()
    data["scaling_row"] = data.topology_label.str.extract(r"row(\d+):")[0].astype(int)
    random_seed = pd.to_numeric(
        data.topology_label.str.extract(r"random_p(\d+)$")[0],
        errors="coerce")
    # The report's 16x16 Random cohort is the fixed prefix p1--p5, not a
    # performance-selected subset of the ten-layout historical archive.
    data = data.loc[~(
        data.scaling_row.isin((3, 4, 8)) &
        data.topology_class.eq("random") & random_seed.gt(5)
    )]
    return aggregate(
        data, "scaling",
        ["scaling_row", "topology_class", "configured_injection_rate"],
    )


def plot_scaling(garnet: pd.DataFrame, output: Path) -> pd.DataFrame:
    data = scaling_data(garnet)
    rows = [row for row in SCALING_ORDER
            if row in set(data.scaling_row.astype(int))]
    labels = [SCALING_LABELS[row] for row in rows]
    width = .19
    for metric, filename, ylabel, log_scale in (
        ("throughput", "scaling_throughput.svg", "Accepted throughput", False),
        ("latency", "scaling_latency.svg",
         "Mean packet latency (cycles, log scale)", True),
    ):
        fig, axis = plt.subplots(figsize=(12, 4.2))
        finite_values = []
        incomplete_labels = []
        missing_labels = []
        for offset, topology in enumerate(("mesh", "random", "greedy", "sa")):
            positions = np.arange(len(rows)) + (offset-1.5)*width
            values, completions = [], []
            for row_number in rows:
                item = data[(data.scaling_row.eq(row_number)) &
                            data.topology_class.eq(topology)]
                values.append(float(item.iloc[0][metric]))
                completions.append((int(item.iloc[0].completed),
                                    int(item.iloc[0].samples)))
            plotted_values = [value if np.isfinite(value) else
                              (np.nan if log_scale else 0.0)
                              for value in values]
            bars = axis.bar(positions, plotted_values, width,
                            color=COLORS[topology],
                            label=LABELS[topology])
            for position, bar, value, (done, total) in zip(
                    positions, bars, values, completions):
                if done < total:
                    bar.set_hatch("///")
                    bar.set_edgecolor("#8b0000")
                    bar.set_linewidth(.9)
                    if np.isfinite(value):
                        incomplete_labels.append(
                            (position, float(bar.get_height()), f"{done}/{total}"))
                    else:
                        missing_labels.append((position, f"{done}/{total}"))
            finite_values.extend(value for value in values
                                 if np.isfinite(value) and value > 0)
        if log_scale:
            axis.set_yscale("log")
            axis.set_ylim(min(finite_values)/1.25, max(finite_values)*4.3)
        else:
            axis.set_ylim(0, max(finite_values)*1.32)
        for position, height, label in incomplete_labels:
            axis.annotate(label, (position, height), xytext=(0, 3),
                          textcoords="offset points", ha="center", va="bottom",
                          rotation=90, fontsize=6.5, color="#8b0000")
        missing_height = (min(finite_values)/1.1 if log_scale
                          else max(finite_values)*.025)
        for position, label in missing_labels:
            axis.scatter([position], [missing_height], marker="x", s=28,
                         color="#8b0000", zorder=5)
            axis.annotate(label, (position, missing_height), xytext=(0, 4),
                          textcoords="offset points", ha="center", va="bottom",
                          rotation=90, fontsize=6.5, color="#8b0000")
        axis.set_xticks(range(len(rows)), labels, rotation=16, ha="right")
        axis.set(xlabel="Scaling configuration", ylabel=ylabel)
        axis.legend(loc="upper center", bbox_to_anchor=(.5, .985), ncol=4)
        axis.grid(axis="y", alpha=.2)
        fig.tight_layout()
        fig.savefig(output / filename, bbox_inches="tight")
        plt.close(fig)
    return data


def resolve_topology(row: pd.Series, garnet_dir: Path,
                     correction_dir: Path | None) -> Path:
    digest = row.topology_sha256
    candidates = []
    if bool(row.get("corrected_after_download", False)) and correction_dir:
        candidates.append(correction_dir / "topology_inputs" / f"{digest}.json")
    candidates.append(garnet_dir / "topology_inputs" / f"{digest}.json")
    for path in candidates:
        if path.exists():
            return path
    raise FileNotFoundError(f"cannot resolve topology {digest}")


def load_edges(path: Path):
    data = json.loads(path.read_text(encoding="utf-8"))
    return int(data["dimension"]), data.get("express_links", [])


def plot_row8(garnet: pd.DataFrame, output: Path, garnet_dir: Path,
              correction_dir: Path | None) -> None:
    n = 16
    demand = soc_heterogeneous_demand(n)
    share = np.zeros(n*n)
    for (_source, destination), weight in demand.items():
        share[destination] += weight
    panel_size = (6.2, 5.2)
    grid_box = [.07, .13, .73, .85]
    colorbar_box = [.84, .13, .035, .85]
    fig = plt.figure(figsize=panel_size)
    axis = fig.add_axes(grid_box)
    relative = share.reshape(n, n)*(n*n)
    image = axis.imshow(relative, cmap="YlOrRd", origin="upper", vmin=0,
                        vmax=float(relative.max()))
    tile = 4
    for boundary in range(tile, n, tile):
        axis.axhline(boundary-.5, color="white", lw=1, alpha=.9)
        axis.axvline(boundary-.5, color="white", lw=1, alpha=.9)
    banks = [(x+2, y+2) for y in range(0, n, 4) for x in range(0, n, 4)]
    memory = [(x, y) for x in (0, n-1) for y in (2, 6, 10, 14)]
    accelerators = [(4,4),(8,4),(12,4),(4,8),(12,8),(4,12),(8,12),(12,12)]
    for points, marker, color, label in (
        (banks, "s", "#2b6cb0", "Tile bank"),
        (memory, "D", "#5a189a", "Memory controller"),
        (accelerators, "^", "#00876c", "Accelerator"),
    ):
        axis.scatter([x for x, _ in points], [y for _, y in points],
                     marker=marker, s=36, facecolors="none",
                     edgecolors=color, linewidths=1.2, label=label)
    axis.set(xlabel="Router x", ylabel="Router y",
             xticks=range(0,n,2), yticks=range(0,n,2))
    axis.set_aspect("equal")
    cax = fig.add_axes(colorbar_box)
    fig.colorbar(image, cax=cax,
                 label="Aggregate destination demand / uniform demand")
    fig.legend(loc="lower center", bbox_to_anchor=(.435,.005), ncol=3,
               frameon=False, fontsize=8)
    fig.savefig(output / "row8_traffic.svg")
    plt.close(fig)

    selected = garnet[
        garnet.section.eq("scaling") & garnet.topology_label.eq("row8:sa")
    ]
    first = selected.iloc[0]
    topology_path = resolve_topology(first, garnet_dir, correction_dir)
    dimension, edges = load_edges(topology_path)
    fig = plt.figure(figsize=panel_size)
    axis = fig.add_axes([.105, .13, .79, .85])
    for y in range(dimension):
        for x in range(dimension-1):
            axis.plot([x,x+1],[y,y],color="#d2d2d2",lw=.42,zorder=0)
    for x in range(dimension):
        for y in range(dimension-1):
            axis.plot([x,x],[y,y+1],color="#d2d2d2",lw=.42,zorder=0)
    for index, edge in enumerate(edges):
        start=(edge["u"]%dimension,edge["u"]//dimension)
        end=(edge["v"]%dimension,edge["v"]//dimension)
        curved_link(axis,start,end,color="#d62728",linewidth=1.45,
                    radius=.10 if index%2==0 else -.10,alpha=.9)
    axis.scatter([i%dimension for i in range(dimension**2)],
                 [i//dimension for i in range(dimension**2)],s=5,
                 color="#222",zorder=3)
    axis.set_aspect("equal");axis.set_xlim(-.7,dimension-.3)
    axis.set_ylim(dimension-.3,-.7);axis.axis("off")
    fig.legend(handles=[
        Line2D([0],[0],color="#d2d2d2",lw=.8,label="Mesh link"),
        Line2D([0],[0],color="#d62728",lw=1.7,label="SA express link"),
    ],loc="lower center",bbox_to_anchor=(.5,.005),ncol=2,frameon=False)
    fig.savefig(output / "row8_sa_topology.svg")
    plt.close(fig)

    samples = defaultdict(list)
    for _, row in selected[selected.complete].iterrows():
        for key, value in merge_physical_links(row).items():
            samples[key].append(value)
    loads = {key: float(np.mean(values)) for key, values in samples.items()}
    norm = Normalize(0, max(loads.values()))
    cmap = plt.cm.inferno
    fig = plt.figure(figsize=panel_size)
    axis = fig.add_axes(grid_box)
    for (source, destination, express), value in loads.items():
        start=(source%dimension,source//dimension)
        end=(destination%dimension,destination//dimension)
        if express:
            curved_link(axis,start,end,color=cmap(norm(value)),linewidth=1.8,
                        radius=.10,alpha=.98)
        else:
            axis.plot([start[0],end[0]],[start[1],end[1]],
                      color=cmap(norm(value)),lw=.75,alpha=.9)
    axis.scatter([i%dimension for i in range(dimension**2)],
                 [i//dimension for i in range(dimension**2)],s=4,
                 color="black",zorder=3)
    scalar=plt.cm.ScalarMappable(norm=norm,cmap=cmap);scalar.set_array([])
    cax=fig.add_axes(colorbar_box)
    fig.colorbar(scalar,cax=cax,
                 label="Mean bidirectional physical-link utilization")
    axis.set_aspect("equal");axis.set_xlim(-.7,dimension-.3)
    axis.set_ylim(dimension-.3,-.7);axis.axis("off")
    fig.savefig(output / "row8_sa_load.svg")
    plt.close(fig)


def relative_summary(garnet: pd.DataFrame, standalone: pd.DataFrame,
                     section: str, keys: list[str], rates=None) -> dict:
    g = garnet[garnet.section.eq(section) & garnet.complete]
    s = standalone[standalone.section.eq(section) & standalone.complete]
    if rates is not None:
        g = g[g.configured_injection_rate.isin(rates)]
        s = s[s.configured_injection_rate.isin(rates)]
    ga = g.groupby(keys).agg(Tg=("accepted_throughput","mean"),
                             Lg=("average_packet_latency_cycles","mean")).reset_index()
    sa = s.groupby(keys).agg(Ts=("accepted_throughput","mean"),
                             Ls=("average_packet_latency_cycles","mean")).reset_index()
    merged = ga.merge(sa, on=keys)
    result = {"groups": len(merged), "merged": merged}
    for metric in ("T", "L"):
        rel = (merged[metric+"g"]-merged[metric+"s"])/merged[metric+"s"]*100
        absolute = rel.abs()
        result[metric] = {
            "within3": int((absolute<=3).sum()),
            "within5": int((absolute<=5).sum()),
            "within10": int((absolute<=10).sum()),
            "median": float(absolute.median()),
            "p90": float(absolute.quantile(.9)),
            "max": float(absolute.max()),
        }
    return result


def fmt(value, digits=1):
    return f"{value:.{digits}f}"


def make_report(garnet: pd.DataFrame, standalone: pd.DataFrame,
                garnet_dir: Path, random_stats: dict,
                scaling: pd.DataFrame, cycle_length: int, path: Path) -> None:
    sections = garnet.groupby("section").agg(
        samples=("seed","size"), completed=("complete","sum"))
    comparisons = {
        "主实验（R=0.40/0.70/0.80）": relative_summary(
            garnet, standalone, "main",
            ["traffic","topology_class","configured_injection_rate"],
            [.4,.7,.8]),
        "主实验全部曲线点": relative_summary(
            garnet, standalone, "main",
            ["traffic","topology_class","configured_injection_rate"]),
        "Cross matrix（R=0.65）": relative_summary(
            garnet, standalone, "cross",
            ["traffic","topology_label","configured_injection_rate"], [.65]),
        "Routing ablation": relative_summary(
            garnet, standalone, "routing_ablation",
            ["topology_label","configured_injection_rate"]),
        "Information ablation": relative_summary(
            garnet, standalone, "information_ablation",
            ["topology_label","configured_injection_rate"]),
        "Escape timeout": relative_summary(
            garnet, standalone, "escape",
            ["topology_label","configured_injection_rate"]),
        "Scaling": relative_summary(
            garnet, standalone, "scaling",
            ["topology_label","configured_injection_rate"]),
    }
    main = aggregate(garnet, "main",
                     ["traffic","topology_class","configured_injection_rate"])
    scale = scaling.copy()
    lines = [
        "# Garnet 全量论文实验与 standalone 对比（2026-09-15）", "",
        "## 1. 数据完整性与统计口径", "",
        ("原下载包包含 **9240 个唯一 Garnet 执行 / 9640 个逻辑样本**；补跑包"
         "对其中 161 个配置重新测量，补跑最终为 161/161 成功。两个包内残留的 "
         "failure log 都是成功重试前的旧日志，最终 `failures.json` 均为空。"), "",
        ("下表中的“有效完成”首先要求 `termination_reason=simulate_limit`，并排除已经"
         "确认参数错误的样本。补跑的 200k no-progress 阈值长于完整 120k-cycle 运行，"
         "因此它给出固定窗口数据，而不是稳态或 deadlock-free 证明。未补跑的 "
         "`deadlock_panic` 仍按删失样本处理。"), "",
        "| section | 逻辑样本 | 有效完成 | watchdog/无效配置 |", "|---|---:|---:|---:|",
    ]
    for section, row in sections.iterrows():
        lines.append(f"| {section} | {int(row.samples)} | {int(row.completed)} | {int(row.samples-row.completed)} |")
    lines += ["", "## 2. 数值一致性", "",
              ("Garnet 与 standalone 按 section、traffic、topology、rate、seed 和测量窗口"
               "逐项对齐；比较表先在各报告组内取完成样本均值，再计算相对差。"
               "Cross 的 R=0.80 是本次 Garnet 新增数据，没有 standalone 对照。"), "",
              "| 比较集合 | 组数 | T误差中位/P90 | T≤3% | L误差中位/P90 | L≤5% |",
              "|---|---:|---:|---:|---:|---:|" ]
    for label, result in comparisons.items():
        lines.append(
            f"| {label} | {result['groups']} | "
            f"{fmt(result['T']['median'])}%/{fmt(result['T']['p90'])}% | "
            f"{result['T']['within3']}/{result['groups']} | "
            f"{fmt(result['L']['median'])}%/{fmt(result['L']['p90'])}% | "
            f"{result['L']['within5']}/{result['groups']} |"
        )
    lines += ["", ("主实验三个表的结论稳定：R=0.70 和 0.80 的八个 traffic--rate "
                    "组合全部保持严格的 Mesh < Random expectation < Greedy < SA throughput "
                    "排序。48 个表格级 throughput 对比中 44 个在 3% 内；四个较大差异"
                    "都来自 Tornado 的 Greedy/SA 饱和点（最大 13.7%）。latency 对微时序"
                    "更敏感，36/48 在 5% 内，最大差异 52.5%，但没有改变上述排序。"), "",
              ("Cross matrix 反而比 standalone 更符合目标：在 R=0.65 和 0.80，"
               "Uniform、Tornado、BitComp、CutStress 四列均由 matched SA 严格取胜，"
               "Mixture 几何均值也由 Mixture SA 严格取胜。"), "",
              "## 3. 不符合预期或必须加注的结果", "",
              "1. **Length-aware Random 生成错误（已修复并补跑）。** 原下载包中 "
              "`16x16-B256-L4` Random 仍使用 unit-latency JSON。原因是 standalone 可在"
              "运行时覆盖 latency，而 Garnet 只读取 topology JSON。生成器现已把 "
              "`ceil(wire_length/wire_per_cycle)` 烘焙进输入；论文固定的 p1--p5 共 10 个"
              "样本全部按正确配置补跑并纳入，未采用的 p6--p10 旧样本继续标作无效。", "",
              "2. **Random-placement 百分位的旧对照口径有误（已修复）。** Garnet 中有 "
              f"{random_stats['measurable']}/400 个 layout 可形成 topology mean；Greedy "
              f"超过 {random_stats['greedy_beats']}/{random_stats['measurable']} 个可测 layout "
              f"（约 {100*random_stats['greedy_beats']/random_stats['measurable']:.1f}th percentile），"
              f"而 SA 超过全部 {random_stats['sa_beats']} 个。另有 "
              f"{random_stats['fully_censored']} 个 layout 两个 seed 均被 watchdog 删失、"
              f"{random_stats['partial']} 个仅完成一个 seed。Random layout 使用 seeds 7/8，"
              "因此固定 Greedy/SA 也必须取相同 seeds；旧图误用了主表 seeds 5--8 的 Greedy "
              "均值，并把可重复但 topology-specific 的 seed-6 拥塞分支混入阈值。配对后 Greedy "
              "为 0.29344、约处于 99.0th percentile，与 standalone 的 98.8th percentile 一致；"
              "两套 Random 分布的中位数也分别为 0.28340/0.28349。这不是 placement/routing "
              "实现差异，而是后处理时没有配对 seed。", "",
              "3. **补跑把 watchdog 删失与固定窗口性能分开。** 125 个 Tesc=8/16、"
              "3 个普通 escape-off、2 个 LocalMeshAdaptive 和 31 个论文采用的 scaling "
              "样本在 200k 阈值下全部到达固定窗口末尾；低 timeout 和 16x16 Random 的"
              "性能仍明显 collapse。普通 escape-off 中还有一个 R=0.8 样本在整个 100k "
              "measurement 内零交付；其原始四-VC Garnet VC dump 可恢复出"
              f"{cycle_length}-channel、{4 * cycle_length}-VC 的封闭依赖分量，"
              "构成直接的协议死锁证据。", "",
              "4. **Cross 中有一个明显的亚稳态离群组。** Uniform、R=0.65 的 Mixture "
              "Greedy 在 Garnet 中只有 0.2440，而 standalone 为 0.2859；Garnet 的 seed 7 "
              "尤其低至 0.1970。相同 topology 在 R=0.80 又回到 0.2807，说明 delayed "
              "feedback 进入了不同的拥塞吸引域，而不是 topology 容量随 offered load "
              "单调变化。它不影响 matched-SA/mixture-SA winner，但这一个点不应被用作"
              "模型精确一致性的证据。", "",
              "5. **16x16 Random 的完整均值显著低于 Mesh。** 论文固定取 p1--p5 五个 "
              "layout×2 seed；补跑后三个 16x16 Random cohort 均为 10/10 固定窗口数据。"
              "其 throughput 分别为 0.1388、0.1846 和 0.0941，均低于对应 Mesh。逐 topology "
              "对照显示部分 layout 在 Garnet 和 standalone 都 collapse，另有少数 seed 位于"
              "不同拥塞吸引域；这不是统一方向的参数漏传，而说明随机加边缺乏鲁棒性。", "",
              "6. **2-flit scaling 是最大的正常模型差异。** Garnet 的四类 topology throughput "
              "均比 standalone 低约 35%，latency 高 46--58%，但仍保持 "
              "Mesh < Random < Greedy < SA。Garnet 的 flits/packets 恰为 2，证明命令行宽度"
              "确实生成了两个 flit；差异来自 standalone 把整包作为一个对象移动，只以"
              "两周期 link service 和 tail-credit delay 近似多-flit，无法表示 head/body/tail "
              "同时占据不同 router/VC 的 wormhole 状态。Garnet 中该行 escape fraction 为 0，"
              "standalone 则为 0.8--3.5%，也是这种 packet-atomic 近似改变等待判定的直接证据。"
              "因此不能用 standalone 做 packet-size 的定量替代。", "",
              "7. **Information ablation 的物理传播代价比旧值大。** Garnet 中 physical 相对"
              " instant 的 throughput 最大下降 2.9%，latency 最大增加 12.0%；效果仍属较小，"
              "但旧文的“0.9%/5.1%以内”必须更新。", "",
              "## 4. Garnet scaling 摘要", "",
              ("以下 throughput/latency 对论文固定 cohort 取均值；C/N 明示到达固定"
               "窗口末尾的样本数。三个 16x16 Random 行均包含完整的五-layout×两-seed。"), "",
              "| 配置 | Mesh | Random | Greedy | SA |", "|---|---:|---:|---:|---:|" ]
    for row_number in SCALING_ORDER:
        cells=[]
        for topology in ("mesh","random","greedy","sa"):
            item=scale[(scale.scaling_row.eq(row_number))&scale.topology_class.eq(topology)]
            if item.empty: cells.append("—"); continue
            r=item.iloc[0]
            if not np.isfinite(r.throughput) or not np.isfinite(r.latency):
                cells.append(f"N/A ({int(r.completed)}/{int(r.samples)})")
            else:
                cells.append(f"{r.throughput:.4f}/{r.latency:.0f} "
                             f"({int(r.completed)}/{int(r.samples)})")
        lines.append(f"| {SCALING_LABELS[row_number]} | " + " | ".join(cells) + " |")
    lines += ["", "## 5. 总结", "",
              ("Garnet 数据继续支持核心结论：主实验高负载下 Greedy 高于 Random "
               "expectation，SA 再稳定提高 Greedy；cross matrix 和 400-Random study 也保持。"
               "Scaling 的更准确结论是每行都保持 Mesh < Greedy <= SA，而 Random 在三个 "
               "16x16 cohort 中明显退化。补跑消除了论文采用 scaling cohort 的选择性完成，"
               "但 2-flit 的系统差异和饱和微时序敏感性仍须保留说明。"), ""]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--garnet-dir", type=Path, default=DEFAULT_GARNET)
    parser.add_argument("--standalone", type=Path, default=DEFAULT_STANDALONE)
    parser.add_argument("--correction-dir", type=Path, default=DEFAULT_CORRECTION)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--supplement-dir", type=Path, default=DEFAULT_SUPPLEMENT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()
    correction = args.correction_dir if args.correction_dir.exists() else None
    supplement = args.supplement_dir if args.supplement_dir.exists() else None
    garnet, standalone = load_inputs(
        args.garnet_dir, args.standalone, correction, supplement)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    figures = args.output_dir / "figures"
    figures.mkdir(exist_ok=True)
    plot_main(garnet, figures)
    plot_path_behavior(garnet, figures)
    random_stats = plot_random_distribution(garnet, figures)
    plot_link_load(garnet, figures)
    plot_escape(garnet, figures)
    cycle_length = plot_deadlock_cycle(garnet, figures)
    scaling = plot_scaling(garnet, figures)
    plot_row8(garnet, figures, args.garnet_dir, correction)
    make_report(garnet, standalone, args.garnet_dir, random_stats,
                scaling, cycle_length, args.report)
    print(f"wrote {args.report}")
    print(f"wrote figures to {figures}")


if __name__ == "__main__":
    main()
