#!/usr/bin/env python3
"""Aggregate the V4 ideal-routing, placement, and traffic-aware experiments."""

from __future__ import annotations

import csv
from collections import defaultdict
import json
import math
from pathlib import Path
import statistics


HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
OUTPUT = RESULTS / "v4_summary"
PROJECT_RESULTS = HERE.parent / "results"


def load(relative_paths):
    rows = []
    for relative in relative_paths:
        rows.extend(json.loads((RESULTS / relative / "main_results.json").read_text()))
    return rows


def aggregate(rows, label_key="topology"):
    groups = defaultdict(list)
    for row in rows:
        groups[(row["traffic"], row[label_key],
                row["configured_injection_rate"])].append(row)
    result = []
    for (traffic, label, rate), samples in sorted(groups.items()):
        throughput = [row["accepted_throughput"] for row in samples]
        latency = [row["average_packet_latency_cycles"] for row in samples]
        express = [row["express_traversals"] / max(1, row["packets_received"])
                   for row in samples]
        result.append({
            "traffic": traffic,
            "series": label,
            "configured_injection_rate": rate,
            "sample_count": len(samples),
            "seeds": sorted(row["seed"] for row in samples),
            "accepted_throughput_mean": statistics.fmean(throughput),
            "accepted_throughput_sd": statistics.pstdev(throughput),
            "average_packet_latency_cycles_mean": statistics.fmean(latency),
            "average_packet_latency_cycles_sd": statistics.pstdev(latency),
            "express_traversals_per_packet_mean": statistics.fmean(express),
            "escape_fraction_mean": statistics.fmean(
                row["delivered_escape_fraction"] for row in samples),
            "max_link_utilization_mean": statistics.fmean(
                row["max_link_utilization"] for row in samples),
            "no_progress_runs": sum(
                bool(row.get("global_no_progress_detected",
                             row.get("no_progress", False)))
                for row in samples),
        })
    return result


def write_table(name, rows):
    (OUTPUT / f"{name}.json").write_text(
        json.dumps(rows, indent=2) + "\n", encoding="utf-8")
    with (OUTPUT / f"{name}.csv").open("w", newline="", encoding="utf-8") as out:
        writer = csv.DictWriter(out, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)


def make_svg(rows, order, labels, colors, title, traffic_titles):
    width, height = 1240, 780
    left, top, panel_w, panel_h = 75, 65, 520, 270
    x_gap, y_gap = 95, 95
    traffic_order = list(traffic_titles)
    panels = [
        (traffic_order[0], "accepted_throughput_mean", False,
         f"{traffic_titles[traffic_order[0]]}: accepted throughput"),
        (traffic_order[1], "accepted_throughput_mean", False,
         f"{traffic_titles[traffic_order[1]]}: accepted throughput"),
        (traffic_order[0], "average_packet_latency_cycles_mean", True,
         f"{traffic_titles[traffic_order[0]]}: latency (log scale)"),
        (traffic_order[1], "average_packet_latency_cycles_mean", True,
         f"{traffic_titles[traffic_order[1]]}: latency (log scale)"),
    ]
    by_key = defaultdict(list)
    for row in rows:
        by_key[(row["traffic"], row["series"])].append(row)
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="white"/>',
        '<style>text{font-family:Arial,sans-serif;fill:#222}.axis{stroke:#555}.grid{stroke:#ddd}.curve{fill:none;stroke-width:2.5}.point{stroke:white;stroke-width:1}</style>',
        f'<text x="620" y="28" text-anchor="middle" font-size="20" font-weight="bold">{title}</text>',
    ]
    for index, (traffic, metric, log_y, panel_title) in enumerate(panels):
        column, panel_row = index % 2, index // 2
        x0 = left + column * (panel_w + x_gap)
        y0 = top + panel_row * (panel_h + y_gap)
        series = {name: sorted(by_key[(traffic, name)],
                              key=lambda item: item["configured_injection_rate"])
                  for name in order}
        values = [item for items in series.values() for item in items]
        xmin = min(item["configured_injection_rate"] for item in values)
        xmax = max(item["configured_injection_rate"] for item in values)
        ymax_raw = max(item[metric] for item in values)
        ymin = min(item[metric] for item in values) if log_y else 0.0
        ymax = ymax_raw * (1.12 if log_y else 1.08)
        if log_y:
            ymin = 10 ** math.floor(math.log10(max(ymin, 1e-9)))
            ymax = 10 ** math.ceil(math.log10(ymax))

        def sx(value):
            return x0 + (value - xmin) / (xmax - xmin) * panel_w

        def sy(value):
            if log_y:
                fraction = ((math.log10(value) - math.log10(ymin)) /
                            (math.log10(ymax) - math.log10(ymin)))
            else:
                fraction = value / ymax
            return y0 + panel_h * (1 - fraction)

        out.append(f'<text x="{x0 + panel_w/2}" y="{y0-14}" text-anchor="middle" font-size="16" font-weight="bold">{panel_title}</text>')
        out.append(f'<line class="axis" x1="{x0}" y1="{y0}" x2="{x0}" y2="{y0+panel_h}"/>')
        out.append(f'<line class="axis" x1="{x0}" y1="{y0+panel_h}" x2="{x0+panel_w}" y2="{y0+panel_h}"/>')
        for tick in sorted({item["configured_injection_rate"] for item in values}):
            x = sx(tick)
            out.append(f'<line class="grid" x1="{x:.1f}" y1="{y0}" x2="{x:.1f}" y2="{y0+panel_h}"/>')
            out.append(f'<text x="{x:.1f}" y="{y0+panel_h+18}" text-anchor="middle" font-size="11">{tick:g}</text>')
        y_ticks = ([10 ** p for p in range(int(math.log10(ymin)),
                                           int(math.log10(ymax)) + 1)]
                   if log_y else [ymax * i / 5 for i in range(6)])
        for tick in y_ticks:
            y = sy(tick)
            out.append(f'<line class="grid" x1="{x0}" y1="{y:.1f}" x2="{x0+panel_w}" y2="{y:.1f}"/>')
            label = f'{tick:g}' if log_y else f'{tick:.2f}'
            out.append(f'<text x="{x0-9}" y="{y+4:.1f}" text-anchor="end" font-size="11">{label}</text>')
        for name in order:
            points = [(sx(item["configured_injection_rate"]), sy(item[metric]))
                      for item in series[name]]
            coords = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
            out.append(f'<polyline class="curve" stroke="{colors[name]}" points="{coords}"/>')
            for x, y in points:
                out.append(f'<circle class="point" fill="{colors[name]}" cx="{x:.1f}" cy="{y:.1f}" r="3.5"/>')
    legend_y = 752
    spacing = min(210, 1050 / len(order))
    start = (width - spacing * len(order)) / 2
    for index, name in enumerate(order):
        x = start + index * spacing
        out.append(f'<line x1="{x}" y1="{legend_y}" x2="{x+26}" y2="{legend_y}" stroke="{colors[name]}" stroke-width="3"/>')
        out.append(f'<text x="{x+32}" y="{legend_y+5}" font-size="12">{labels[name]}</text>')
    out.append('</svg>')
    return "\n".join(out) + "\n"


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)

    traffic_rows = load([
        "traffic_aware/final_mesh", "traffic_aware/final_uniform_baselines",
        "traffic_aware/final_stride", "traffic_aware/final_aware",
    ])
    traffic_aggregate = aggregate(traffic_rows)
    write_table("traffic_aware_aggregate", traffic_aggregate)
    traffic_order = ["mesh", "random", "aspl", "stride_aspl",
                     "bitcomp_aspl", "tornado_aspl"]
    # Only draw the topology matched to each traffic plus common baselines.
    traffic_plot_rows = [row for row in traffic_aggregate
                         if row["series"] in {"mesh", "random", "aspl",
                                              "stride_aspl"}
                         or (row["traffic"] == "bit_complement" and
                             row["series"] == "bitcomp_aspl")
                         or (row["traffic"] == "tornado" and
                             row["series"] == "tornado_aspl")]
    # Supply blank matched-series aliases so both panels share one five-entry legend.
    for row in traffic_plot_rows:
        if row["series"] in {"bitcomp_aspl", "tornado_aspl"}:
            row["series"] = "traffic_aware_aspl"
    (OUTPUT / "traffic_aware_curves.svg").write_text(make_svg(
        traffic_plot_rows,
        ["mesh", "random", "aspl", "stride_aspl", "traffic_aware_aspl"],
        {"mesh": "Mesh XY", "random": "Random", "aspl": "Uniform ASPL",
         "stride_aspl": "Stride ASPL", "traffic_aware_aspl": "Traffic-aware ASPL"},
        {"mesh": "#333333", "random": "#e69f00", "aspl": "#56b4e9",
         "stride_aspl": "#009e73", "traffic_aware_aspl": "#cc79a7"},
        "Traffic-aware express placement (3-seed standalone)",
        {"bit_complement": "Bit-complement", "tornado": "Tornado"},
    ), encoding="utf-8")

    routing_sets = {
        "K8_pressure": "ideal_path/corrected_k8",
        "Dijkstra_express": "ideal_path/long_p5_rw0.125_vw0.25",
        "Dijkstra_global": "ideal_path/long_p6_rw1.5_vw0.5",
    }
    routing_rows = []
    for label, relative in routing_sets.items():
        for row in load([relative]):
            row = dict(row)
            row["experiment_series"] = label
            routing_rows.append(row)
    routing_aggregate = aggregate(routing_rows, "experiment_series")
    write_table("ideal_routing_aggregate", routing_aggregate)
    (OUTPUT / "ideal_routing_curves.svg").write_text(make_svg(
        routing_aggregate,
        list(routing_sets),
        {"K8_pressure": "K=8 pressure", "Dijkstra_express": "Dijkstra express-only",
         "Dijkstra_global": "Dijkstra global oracle"},
        {"K8_pressure": "#333333", "Dijkstra_express": "#e69f00",
         "Dijkstra_global": "#0072b2"},
        "Path-planning upper bounds on Stride-ASPL (3-seed standalone)",
        {"uniform_random": "Uniform", "cutstress": "CutStress"},
    ), encoding="utf-8")

    garnet_rows = []
    garnet_inputs = [
        "garnet_v4_final_r05/mesh",
        "garnet_v4_final_r05/uniform_baselines",
        "garnet_v4_final_r05/stride",
        "garnet_v4_final_r05/tornado_aware",
        "garnet_v4_final/mesh",
        "garnet_v4_final/uniform_baselines",
        "garnet_v4_final/stride",
        "garnet_v4_final/bitcomp_aware",
        "garnet_v4_final/tornado_aware",
        "garnet_v4_final/tornado_hybrid",
    ]
    for relative in garnet_inputs:
        garnet_rows.extend(json.loads(
            (PROJECT_RESULTS / relative / "main_results.json").read_text()))
    garnet_aggregate = aggregate(garnet_rows)
    write_table("garnet_aggregate", garnet_aggregate)
    print(f"Wrote V4 aggregates and curves to {OUTPUT}")


if __name__ == "__main__":
    main()
