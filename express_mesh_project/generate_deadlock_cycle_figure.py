#!/usr/bin/env python3
"""Draw an observed packet/channel wait-for cycle from a standalone trace."""

from __future__ import annotations

import argparse
from collections import deque
import json
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch


def shortest_express_cycle(trace):
    waits = {item["packet"]: item for item in trace["waits"]}
    links = {item["id"]: item for item in trace["links"]}
    adjacency = {
        packet: [
            blocker["packet"] for blocker in item["blockers"]
            if blocker["packet"] in waits
        ]
        for packet, item in waits.items()
    }
    best = None
    for start in adjacency:
        queue = deque([(start, [start])])
        seen = {start}
        while queue:
            packet, path = queue.popleft()
            for neighbor in adjacency[packet]:
                if neighbor == start:
                    cycle = path
                    has_express = any(
                        links[waits[item]["requested_link"]]["express_id"] >= 0
                        for item in cycle
                        if waits[item]["requested_link"] >= 0
                    )
                    if has_express and (best is None or len(cycle) < len(best)):
                        best = cycle
                    queue.clear()
                    break
                if neighbor not in seen:
                    seen.add(neighbor)
                    queue.append((neighbor, path + [neighbor]))
    if best is None:
        raise ValueError("trace has no packet wait cycle containing an express link")
    return best, waits, links


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("trace", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--dimension", type=int, default=8)
    args = parser.parse_args()

    trace = json.loads(args.trace.read_text(encoding="utf-8"))
    cycle, waits, links = shortest_express_cycle(trace)
    dimension = args.dimension

    def coordinate(router):
        return router % dimension, router // dimension

    fig, (axis, detail) = plt.subplots(
        1, 2, figsize=(11.4, 6.6), gridspec_kw={"width_ratios": [1.28, 1.0]}
    )
    for value in range(dimension):
        axis.plot([0, dimension - 1], [value, value], color="#e5e7eb",
                  linewidth=0.8, zorder=0)
        axis.plot([value, value], [0, dimension - 1], color="#e5e7eb",
                  linewidth=0.8, zorder=0)

    cycle_routers = []
    for packet in cycle:
        router = waits[packet]["router"]
        cycle_routers.append(router)
        x, y = coordinate(router)
        axis.scatter(x, y, s=420, facecolor="white", edgecolor="#111827",
                     linewidth=1.6, zorder=5)
        axis.text(x, y, f"R{router}", ha="center", va="center",
                  fontsize=9, fontweight="bold", zorder=6)

    for index, packet in enumerate(cycle):
        item = waits[packet]
        next_packet = cycle[(index + 1) % len(cycle)]
        blocker = next(
            entry for entry in item["blockers"]
            if entry["packet"] == next_packet
        )
        link = links[item["requested_link"]]
        source = coordinate(link["source"])
        destination = coordinate(link["destination"])
        express = link["express_id"] >= 0
        color = "#dc2626" if express else "#2563eb"
        radius = 0.18 if express and source[0] < 5 else -0.18 if express else 0
        arrow = FancyArrowPatch(
            source, destination, arrowstyle="-|>", mutation_scale=15,
            linewidth=3.2 if express else 2.2, color=color,
            connectionstyle=f"arc3,rad={radius}", shrinkA=14, shrinkB=14,
            zorder=3,
        )
        axis.add_patch(arrow)
        mx = (source[0] + destination[0]) / 2
        my = (source[1] + destination[1]) / 2
        if express:
            mx += 0.52 if radius > 0 else -0.52
        else:
            dx, dy = destination[0] - source[0], destination[1] - source[1]
            mx += -0.30 * dy
            my += 0.30 * dx
        axis.text(
            mx, my, f"{index + 1} · VC{blocker['vc']}", color=color,
            fontsize=8.2, ha="center", va="center", zorder=7,
            bbox={"boxstyle": "round,pad=0.18", "facecolor": "white",
                  "edgecolor": color, "alpha": 0.94, "linewidth": 0.8},
        )

    axis.plot([], [], color="#2563eb", linewidth=2.2,
              label="occupied mesh channel")
    axis.plot([], [], color="#dc2626", linewidth=3.2,
              label="occupied express channel")
    axis.legend(loc="upper right", frameon=True, fontsize=9)
    axis.set_xlim(-0.7, dimension - 0.3)
    axis.set_ylim(dimension - 0.25, -0.75)
    axis.set_aspect("equal")
    axis.set_xticks(range(dimension))
    axis.set_yticks(range(dimension))
    axis.set_xlabel("mesh x coordinate")
    axis.set_ylabel("mesh y coordinate")
    axis.spines[["top", "right", "bottom", "left"]].set_visible(False)
    axis.tick_params(length=0, colors="#6b7280")

    detail.axis("off")
    detail.text(0, 0.98, "One simple cycle in the wait-for SCC",
                fontsize=11.5, fontweight="bold", va="top")
    y = 0.91
    for index, packet in enumerate(cycle):
        item = waits[packet]
        next_packet = cycle[(index + 1) % len(cycle)]
        blocker = next(
            entry for entry in item["blockers"]
            if entry["packet"] == next_packet
        )
        link = links[item["requested_link"]]
        kind = "EXPRESS" if link["express_id"] >= 0 else "mesh"
        color = "#dc2626" if link["express_id"] >= 0 else "#2563eb"
        detail.text(
            0.0, y,
            f"{index + 1:>2}. {link['source']}→{link['destination']}  "
            f"VC{blocker['vc']}  held by P{next_packet}  [{kind}]",
            fontsize=9.0, color=color, family="monospace", va="top",
        )
        y -= 0.061
    detail.text(
        0, y - 0.015,
        "P on channel i requests channel i+1.\n"
        "At every request both output VCs are occupied.\n"
        "The full closed SCC contains 20 packets; this plot\n"
        "highlights its shortest express-containing cycle.",
        fontsize=9.5, color="#111827", va="top", linespacing=1.45,
        bbox={"boxstyle": "round,pad=0.5", "facecolor": "#f9fafb",
              "edgecolor": "#9ca3af"},
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.output, bbox_inches="tight")
    plt.close(fig)


if __name__ == "__main__":
    main()
