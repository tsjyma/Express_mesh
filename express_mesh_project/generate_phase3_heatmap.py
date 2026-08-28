"""Generate the primary CutStress link-utilization heatmap as standalone SVG."""

import json
import re
from pathlib import Path


HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results" / "phase3"
PANELS = (
    ("mesh", "deterministic", "Mesh + Deterministic"),
    ("hybrid_cutstress", "deterministic", "CutStress-aware + Deterministic"),
    ("hybrid_cutstress", "adaptive", "CutStress-aware + Adaptive"),
)
RATE = 0.08
SEEDS = (1, 2, 3)


def stat_vector(path):
    text = path.read_text(encoding="utf-8")
    name = "system.ruby.network.express_mesh_int_link_utilization"
    match = re.search(rf"^{re.escape(name)}\s+(.+?)\s+\(Unspecified\)$", text, re.M)
    if not match:
        raise ValueError(f"missing utilization vector in {path}")
    return [float(value) for value in match.group(1).replace("|", " ").split()]


def averaged_vector(topology, routing):
    vectors = []
    for seed in SEEDS:
        tag = f"{topology}_{routing}_cutstress_r{RATE:.3f}_s{seed}"
        vectors.append(stat_vector(RESULTS / "runs" / tag / "stats.txt"))
    return [sum(values) / len(values) for values in zip(*vectors)]


def physical_edges(vector, express_file):
    topology = json.loads(express_file.read_text(encoding="utf-8"))
    physical = []
    for row in range(8):
        for col in range(7):
            u = row * 8 + col
            physical.append((u, u + 1, False))
    for col in range(8):
        for row in range(7):
            u = row * 8 + col
            physical.append((u, u + 8, False))
    for link in topology["express_links"]:
        physical.append((link["u"], link["v"], True))

    # Topology::createLinks walks its switch matrix by numeric source and
    # destination ID, so Garnet's internal-link vector is sorted by directed
    # (source, destination), not by Python topology construction order.
    directed = sorted(
        (source, destination)
        for u, v, _ in physical
        for source, destination in ((u, v), (v, u))
    )
    if len(directed) != len(vector):
        raise ValueError(
            f"mapped {len(directed)} links from a {len(vector)}-entry vector"
        )
    utilization = dict(zip(directed, vector))
    return [
        (u, v, max(utilization[u, v], utilization[v, u]), express)
        for u, v, express in physical
    ]


def color(value, maximum):
    stops = (
        (0.00, (36, 74, 147)),
        (0.25, (45, 157, 180)),
        (0.50, (93, 190, 103)),
        (0.75, (250, 204, 72)),
        (1.00, (204, 48, 45)),
    )
    ratio = min(max(value / maximum, 0.0), 1.0) if maximum else 0.0
    for (left, c0), (right, c1) in zip(stops, stops[1:]):
        if ratio <= right:
            t = (ratio - left) / (right - left)
            rgb = tuple(round(a + t * (b - a)) for a, b in zip(c0, c1))
            return f"rgb{rgb}"
    return "rgb(204,48,45)"


def point(router, origin_x, origin_y, spacing=43):
    return origin_x + (router % 8) * spacing, origin_y + (router // 8) * spacing


def main():
    panel_data = []
    for topology, routing, title in PANELS:
        vector = averaged_vector(topology, routing)
        topology_file = HERE / "results" / "phase1" / (
            "mesh.json" if topology == "mesh" else "hybrid_cutstress.json"
        )
        panel_data.append((title, physical_edges(vector, topology_file)))

    maximum = max(edge[2] for _, edges in panel_data for edge in edges)
    width, height = 1230, 500
    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        '<style>text{font-family:Arial,sans-serif;fill:#20242a;letter-spacing:0}.title{font-size:17px;font-weight:700}.label{font-size:12px}.small{font-size:11px;fill:#555}</style>',
        '<text x="615" y="25" text-anchor="middle" class="title">CutStress link utilization at offered rate 0.08 (3-seed mean)</text>',
    ]
    for panel_index, (title, edges) in enumerate(panel_data):
        ox = 48 + panel_index * 405
        oy = 75
        svg.append(f'<text x="{ox + 151}" y="55" text-anchor="middle" class="title">{title}</text>')
        for u, v, util, express in edges:
            x1, y1 = point(u, ox, oy)
            x2, y2 = point(v, ox, oy)
            stroke = color(util, maximum)
            if express:
                mid_x = (x1 + x2) / 2
                control_y = min(y1, y2) - 25 - abs(x2 - x1) * 0.12
                path = f"M{x1},{y1} Q{mid_x},{control_y} {x2},{y2}"
                svg.append(f'<path d="{path}" fill="none" stroke="{stroke}" stroke-width="7" stroke-linecap="round" opacity="0.92"><title>Express {u}-{v}: {util:.3f}</title></path>')
            else:
                svg.append(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{stroke}" stroke-width="6" stroke-linecap="round"><title>Mesh {u}-{v}: {util:.3f}</title></line>')
        for router in range(64):
            x, y = point(router, ox, oy)
            svg.append(f'<circle cx="{x}" cy="{y}" r="4" fill="#1d2329"/>')
        peak = max(edge[2] for edge in edges)
        svg.append(f'<text x="{ox + 151}" y="397" text-anchor="middle" class="label">panel max = {peak:.3f}</text>')

    legend_x, legend_y, legend_w = 377, 432, 476
    segments = 100
    for index in range(segments):
        value = maximum * index / (segments - 1)
        x = legend_x + legend_w * index / segments
        svg.append(f'<rect x="{x:.2f}" y="{legend_y}" width="{legend_w / segments + 0.4:.2f}" height="15" fill="{color(value, maximum)}"/>')
    svg.extend([
        f'<text x="{legend_x}" y="465" text-anchor="middle" class="small">0</text>',
        f'<text x="{legend_x + legend_w / 2}" y="465" text-anchor="middle" class="small">{maximum / 2:.3f}</text>',
        f'<text x="{legend_x + legend_w}" y="465" text-anchor="middle" class="small">{maximum:.3f}</text>',
        '<text x="615" y="488" text-anchor="middle" class="small">Physical-edge heat = max of the two measured directed-link utilizations; common scale across panels</text>',
        '</svg>',
    ])
    output = RESULTS / "cutstress_heatmap.svg"
    output.write_text("\n".join(svg) + "\n", encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
