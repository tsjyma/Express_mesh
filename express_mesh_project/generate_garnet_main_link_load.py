#!/usr/bin/env python3
"""Render Figure 9 from the completed Garnet main-validation telemetry."""

from __future__ import annotations

from collections import defaultdict
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import Normalize
from matplotlib.lines import Line2D
import numpy as np

from run_phase3_measurement_v2 import stat_values
from summarize_20260831_standalone_suite import curved_link


ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "express_mesh_project"
VALIDATION = PROJECT / "results" / "20260906" / "garnet_main_validation"
OUTPUT = (PROJECT / "results" / "20260831" / "standalone_suite" /
          "figures")
LABELS = {
    "mesh": "Mesh", "random": "Representative Random (p1)",
    "greedy": "ASPL Greedy", "sa": "SA",
}


def directed_endpoints(dimension: int, topology: dict) -> list[tuple[int, int, bool]]:
    """Return endpoints in Garnet's runtime NetworkLink order.

    ExpressMesh.py supplies IntLinks in mesh-then-express order, but Garnet's
    Topology::createLinks() walks its ordered (source, destination) link map.
    Consequently, the vector emitted by GarnetNetwork::collateStats() is
    source/destination sorted rather than Python construction ordered.
    """
    endpoints = []
    for row in range(dimension):
        for col in range(dimension - 1):
            west = col + row * dimension
            east = west + 1
            endpoints.extend(((west, east, False), (east, west, False)))
    for col in range(dimension):
        for row in range(dimension - 1):
            south = col + row * dimension
            north = south + dimension
            endpoints.extend(((south, north, False), (north, south, False)))
    for edge in topology.get("express_links", []):
        u, v = int(edge["u"]), int(edge["v"])
        endpoints.extend(((u, v, True), (v, u, True)))
    return sorted(endpoints, key=lambda link: (link[0], link[1]))


def select_rows() -> dict[str, dict]:
    rows = json.loads((VALIDATION / "results.json").read_text(encoding="utf-8"))
    selected = {}
    for topology_class in LABELS:
        matches = [
            row for row in rows
            if row["traffic"] == "uniform_random"
            and row["configured_injection_rate"] == 0.8
            and row["topology_class"] == topology_class
            and row["seed"] == 5
            and (topology_class != "random" or row["topology"] == "random_p1")
        ]
        if len(matches) != 1:
            raise RuntimeError(
                f"expected one Garnet row for {topology_class}, found {len(matches)}"
            )
        selected[topology_class] = matches[0]
    return selected


def physical_loads(row: dict) -> dict[tuple[int, int, bool], float]:
    topology = json.loads(Path(row["topology_file"]).read_text(encoding="utf-8"))
    dimension = int(topology["dimension"])
    endpoints = directed_endpoints(dimension, topology)
    stats = (Path(row["run_dir"]) / "stats.txt").read_text(encoding="utf-8")
    values = stat_values(
        stats, "system.ruby.network.express_mesh_int_link_utilization"
    )
    if len(values) != len(endpoints):
        raise RuntimeError(
            f"link-vector mismatch for {row['topology']}: "
            f"{len(values)} values versus {len(endpoints)} links"
        )
    merged = defaultdict(list)
    for (source, destination, is_express), value in zip(endpoints, values):
        merged[(min(source, destination), max(source, destination),
                is_express)].append(float(value))
    if any(len(pair) != 2 for pair in merged.values()):
        raise RuntimeError(f"directed pair missing for {row['topology']}")
    return {key: float(np.mean(pair)) for key, pair in merged.items()}


def main() -> None:
    selected = select_rows()
    loads = {name: physical_loads(row) for name, row in selected.items()}
    all_values = [value for topology in loads.values() for value in topology.values()]
    shared_maximum = max(all_values)
    norm = Normalize(vmin=0.0, vmax=shared_maximum)
    cmap = plt.cm.inferno
    figure, axes = plt.subplots(2, 2, figsize=(8.2, 8.2))
    summary = {}
    for axis, name in zip(axes.flat, LABELS):
        row = selected[name]
        topology = json.loads(
            Path(row["topology_file"]).read_text(encoding="utf-8")
        )
        dimension = int(topology["dimension"])
        local_maximum = max(loads[name].values())
        summary[name] = {
            "topology": row["topology"], "traffic": row["traffic"],
            "rate": row["configured_injection_rate"], "seed": row["seed"],
            "physical_link_maximum": local_maximum,
        }
        for (source, destination, is_express), value in loads[name].items():
            x0, y0 = source % dimension, source // dimension
            x1, y1 = destination % dimension, destination // dimension
            color = cmap(norm(value))
            if is_express:
                curved_link(axis, (x0, y0), (x1, y1), color=color,
                            linewidth=2.0, radius=.13, alpha=.98,
                            arrow=False, zorder=2)
            else:
                axis.plot([x0, x1], [y0, y1], color=color, lw=.9,
                          alpha=.82, zorder=1)
        axis.scatter(
            [i % dimension for i in range(dimension * dimension)],
            [i // dimension for i in range(dimension * dimension)],
            color="black", s=6, zorder=3,
        )
        axis.set_title(f"{LABELS[name]} (local max={local_maximum:.2f})")
        axis.set_aspect("equal")
        axis.set_xlim(-.65, dimension - .35)
        axis.set_ylim(dimension - .35, -.65)
        axis.axis("off")
    scalar = plt.cm.ScalarMappable(norm=norm, cmap=cmap)
    scalar.set_array([])
    figure.colorbar(
        scalar, ax=axes.ravel().tolist(), fraction=.035, pad=.025,
        label=("Mean utilization of the two directed channels "
               "(active flit cycles / measurement cycles)"),
    )
    figure.legend(
        handles=[
            Line2D([0], [0], color="#555555", lw=.8,
                   label="Mesh physical link"),
            Line2D([0], [0], color="#555555", lw=2.0,
                   label="Express physical link (curved)"),
        ],
        loc="lower center", ncol=2, frameon=False, bbox_to_anchor=(.46, .01),
    )
    figure.subplots_adjust(left=.02, right=.88, bottom=.08, top=.97,
                           wspace=.08, hspace=.12)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    figure.savefig(OUTPUT / "directed_link_load.svg", bbox_inches="tight")
    figure.savefig(OUTPUT / "directed_link_load.png", dpi=180,
                   bbox_inches="tight")
    plt.close(figure)
    (VALIDATION / "link_load_figure_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
