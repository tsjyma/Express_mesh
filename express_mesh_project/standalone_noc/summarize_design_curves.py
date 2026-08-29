#!/usr/bin/env python3
"""Aggregate the final standalone design sweep and draw dependency-free SVGs."""

from __future__ import annotations

import csv
from collections import defaultdict
import json
import math
from pathlib import Path
import statistics


HERE = Path(__file__).resolve().parent
INPUT = HERE / "results" / "final_b64_curves"
OUTPUT = HERE / "results" / "final_b64_summary"
ORDER = ["mesh", "stride_random", "aspl", "stride_aspl"]
LABEL = {
    "mesh": "Mesh XY",
    "stride_random": "Stride Random",
    "aspl": "ASPL Greedy",
    "stride_aspl": "Stride-ASPL Greedy",
}
COLOR = {
    "mesh": "#333333",
    "stride_random": "#e69f00",
    "aspl": "#56b4e9",
    "stride_aspl": "#009e73",
}


def mean_sd(values):
    return statistics.fmean(values), statistics.pstdev(values)


def aggregate():
    rows = []
    for path in INPUT.glob("*/main_results.json"):
        rows.extend(json.loads(path.read_text(encoding="utf-8")))
    selected = [r for r in rows if r["topology"] in ORDER]
    bad = [r for r in selected if r["termination_reason"] != "simulate_limit"]
    if bad:
        raise RuntimeError(f"{len(bad)} selected final runs terminated early")

    groups = defaultdict(list)
    for row in selected:
        groups[(row["traffic"], row["topology"],
                row["configured_injection_rate"])].append(row)
    summary = []
    for (traffic, topology, rate), samples in sorted(groups.items()):
        if len(samples) != 3:
            raise RuntimeError(f"expected 3 seeds for {(traffic, topology, rate)}")
        throughput, throughput_sd = mean_sd(
            [r["accepted_throughput"] for r in samples])
        latency, latency_sd = mean_sd(
            [r["average_packet_latency_cycles"] for r in samples])
        express = [
            r["express_traversals"] / r["packets_received"]
            if r["packets_received"] else 0.0 for r in samples
        ]
        summary.append({
            "traffic": traffic,
            "topology": topology,
            "configured_injection_rate": rate,
            "seeds": [r["seed"] for r in samples],
            "accepted_throughput_mean": throughput,
            "accepted_throughput_sd": throughput_sd,
            "average_packet_latency_cycles_mean": latency,
            "average_packet_latency_cycles_sd": latency_sd,
            "express_traversals_per_packet_mean": statistics.fmean(express),
            "delivered_escape_fraction_mean": statistics.fmean(
                r["delivered_escape_fraction"] for r in samples),
            "max_link_utilization_mean": statistics.fmean(
                r["max_link_utilization"] for r in samples),
        })
    return rows, summary


def svg_plot(summary):
    width, height = 1240, 780
    left, top, panel_w, panel_h = 75, 65, 520, 270
    x_gap, y_gap = 95, 95
    panels = [
        ("uniform_random", "accepted_throughput_mean", False,
         "Uniform: accepted throughput"),
        ("cutstress", "accepted_throughput_mean", False,
         "CutStress: accepted throughput"),
        ("uniform_random", "average_packet_latency_cycles_mean", True,
         "Uniform: packet latency (log scale)"),
        ("cutstress", "average_packet_latency_cycles_mean", True,
         "CutStress: packet latency (log scale)"),
    ]
    by_key = defaultdict(list)
    for row in summary:
        by_key[(row["traffic"], row["topology"])].append(row)
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="white"/>',
        '<style>text{font-family:Arial,sans-serif;fill:#222}.axis{stroke:#555;stroke-width:1}.grid{stroke:#ddd;stroke-width:1}.curve{fill:none;stroke-width:2.5}.point{stroke:white;stroke-width:1}</style>',
        '<text x="620" y="28" text-anchor="middle" font-size="20" font-weight="bold">Budget-64 Express-Mesh: 3-seed standalone curves</text>',
    ]
    for index, (traffic, metric, log_y, title) in enumerate(panels):
        col, row_index = index % 2, index // 2
        x0 = left + col * (panel_w + x_gap)
        y0 = top + row_index * (panel_h + y_gap)
        series = {topology: sorted(by_key[(traffic, topology)],
                                  key=lambda r: r["configured_injection_rate"])
                  for topology in ORDER}
        all_rows = [r for values in series.values() for r in values]
        xmin = min(r["configured_injection_rate"] for r in all_rows)
        xmax = max(r["configured_injection_rate"] for r in all_rows)
        raw_y = [r[metric] for r in all_rows]
        ymin = min(raw_y) if log_y else 0.0
        ymax = max(raw_y) * (1.12 if log_y else 1.08)
        if log_y:
            ymin = 10 ** math.floor(math.log10(ymin))
            ymax = 10 ** math.ceil(math.log10(ymax))

        def sx(value):
            return x0 + (value - xmin) / (xmax - xmin) * panel_w

        def sy(value):
            if log_y:
                fraction = ((math.log10(value) - math.log10(ymin)) /
                            (math.log10(ymax) - math.log10(ymin)))
            else:
                fraction = (value - ymin) / (ymax - ymin)
            return y0 + panel_h * (1.0 - fraction)

        out.append(f'<text x="{x0 + panel_w/2}" y="{y0-14}" text-anchor="middle" font-size="16" font-weight="bold">{title}</text>')
        out.append(f'<line class="axis" x1="{x0}" y1="{y0}" x2="{x0}" y2="{y0+panel_h}"/>')
        out.append(f'<line class="axis" x1="{x0}" y1="{y0+panel_h}" x2="{x0+panel_w}" y2="{y0+panel_h}"/>')
        x_ticks = sorted({r["configured_injection_rate"] for r in all_rows})
        for tick in x_ticks:
            x = sx(tick)
            out.append(f'<line class="grid" x1="{x:.1f}" y1="{y0}" x2="{x:.1f}" y2="{y0+panel_h}"/>')
            out.append(f'<text x="{x:.1f}" y="{y0+panel_h+18}" text-anchor="middle" font-size="11">{tick:g}</text>')
        if log_y:
            y_ticks = [10 ** power for power in range(
                int(math.log10(ymin)), int(math.log10(ymax)) + 1)]
        else:
            step = 0.05 if ymax < 0.5 else ymax / 5
            y_ticks = [i * step for i in range(int(ymax / step) + 1)]
        for tick in y_ticks:
            y = sy(tick)
            out.append(f'<line class="grid" x1="{x0}" y1="{y:.1f}" x2="{x0+panel_w}" y2="{y:.1f}"/>')
            label = f'{tick:g}' if log_y else f'{tick:.2f}'
            out.append(f'<text x="{x0-9}" y="{y+4:.1f}" text-anchor="end" font-size="11">{label}</text>')
        out.append(f'<text x="{x0+panel_w/2}" y="{y0+panel_h+38}" text-anchor="middle" font-size="12">Configured injection rate</text>')
        for topology in ORDER:
            points = [(sx(r["configured_injection_rate"]), sy(r[metric]))
                      for r in series[topology]]
            coords = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
            out.append(f'<polyline class="curve" stroke="{COLOR[topology]}" points="{coords}"/>')
            for x, y in points:
                out.append(f'<circle class="point" fill="{COLOR[topology]}" cx="{x:.1f}" cy="{y:.1f}" r="3.5"/>')

    legend_y = 752
    for i, topology in enumerate(ORDER):
        x = 260 + i * 205
        out.append(f'<line x1="{x}" y1="{legend_y}" x2="{x+28}" y2="{legend_y}" stroke="{COLOR[topology]}" stroke-width="3"/>')
        out.append(f'<text x="{x+35}" y="{legend_y+5}" font-size="13">{LABEL[topology]}</text>')
    out.append('</svg>')
    return "\n".join(out) + "\n"


def main():
    raw, summary = aggregate()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "aggregate.json").write_text(
        json.dumps({"runs": len(raw), "summary": summary}, indent=2) + "\n",
        encoding="utf-8",
    )
    with (OUTPUT / "aggregate.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=summary[0].keys())
        writer.writeheader()
        writer.writerows(summary)
    (OUTPUT / "throughput_latency_curves.svg").write_text(
        svg_plot(summary), encoding="utf-8"
    )
    print(f"Wrote {len(summary)} aggregate points to {OUTPUT}")


if __name__ == "__main__":
    main()
