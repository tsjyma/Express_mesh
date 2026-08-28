#!/usr/bin/env python3
"""Generate the four requested validation figures as dependency-free SVG."""

import json
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parent / "results" / "phase3_measurement_v2"
OUT = ROOT / "figures"
COLORS = {"Mesh": "#333333", "Random": "#d47700", "Hybrid": "#16856b",
          "q+r": "#16856b", "RandomCandidate": "#b43c55"}
COLORS.update({"Escape": "#16856b", "NoEscape": "#b43c55"})


def svg_plot(path, title, xlabel, ylabel, series, note=""):
    w, h, left, right, top, bottom = 760, 460, 82, 24, 52, 70
    points = [p for _, values in series for p in values]
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    xmin, xmax = min(xs), max(xs); ymin, ymax = min(0, min(ys)), max(ys)
    if xmax == xmin: xmax += 1
    if ymax == ymin: ymax += 1
    px = lambda x: left + (x-xmin)/(xmax-xmin)*(w-left-right)
    py = lambda y: top + (ymax-y)/(ymax-ymin)*(h-top-bottom)
    lines = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}">',
             '<style>text{font-family:Arial,sans-serif;font-size:12px}.title{font-size:18px;font-weight:bold}.axis{stroke:#444}.grid{stroke:#ddd}</style>',
             f'<text x="{w/2}" y="26" text-anchor="middle" class="title">{title}</text>']
    for i in range(6):
        y = ymin + i*(ymax-ymin)/5; yy=py(y)
        lines += [f'<line x1="{left}" y1="{yy}" x2="{w-right}" y2="{yy}" class="grid"/>',
                  f'<text x="{left-8}" y="{yy+4}" text-anchor="end">{y:.3g}</text>']
    lines += [f'<line x1="{left}" y1="{top}" x2="{left}" y2="{h-bottom}" class="axis"/>',
              f'<line x1="{left}" y1="{h-bottom}" x2="{w-right}" y2="{h-bottom}" class="axis"/>']
    for name, values in series:
        color=COLORS[name]; coords=" ".join(f"{px(x):.1f},{py(y):.1f}" for x,y in values)
        lines.append(f'<polyline points="{coords}" fill="none" stroke="{color}" stroke-width="2.5"/>')
        for x,y in values: lines.append(f'<circle cx="{px(x):.1f}" cy="{py(y):.1f}" r="4" fill="{color}"/>')
    for i in range(6):
        x=xmin+i*(xmax-xmin)/5; lines.append(f'<text x="{px(x)}" y="{h-bottom+20}" text-anchor="middle">{x:.3g}</text>')
    lines += [f'<text x="{w/2}" y="{h-18}" text-anchor="middle">{xlabel}</text>',
              f'<text x="18" y="{h/2}" transform="rotate(-90 18 {h/2})" text-anchor="middle">{ylabel}</text>']
    for i,(name,_) in enumerate(series):
        x=left+i*145; lines += [f'<line x1="{x}" y1="{h-42}" x2="{x+24}" y2="{h-42}" stroke="{COLORS[name]}" stroke-width="3"/>', f'<text x="{x+30}" y="{h-38}">{name}</text>']
    if note: lines.append(f'<text x="{w-right}" y="42" text-anchor="end">{note}</text>')
    lines.append('</svg>'); path.write_text("\n".join(lines)+"\n", encoding="utf-8")


def load(name): return json.loads((ROOT / name).read_text(encoding="utf-8"))


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    agg=load("validation_aggregate.json")["groups"]
    # Directly use aggregate group means; offered rate is measured packet rate.
    topo=[]
    for label,key in (("Mesh","mesh"),("Random","random"),("Hybrid","hybrid")):
        gs=sorted((g for g in agg if g["topology"]==key and g["escape_enabled"] and g["rate"] in (.4,.5,.6)), key=lambda g:g["rate"])
        topo.append((label, [(g["actual_offered_throughput_mean"], g["throughput_mean_completed"]) for g in gs]))
    svg_plot(OUT/"figure1_throughput.svg", "Accepted throughput", "actual offered packet throughput", "accepted packet throughput", topo)
    lat=[]
    for label,key in (("Mesh","mesh"),("Random","random"),("Hybrid","hybrid")):
        gs=sorted((g for g in agg if g["topology"]==key and g["escape_enabled"] and g["rate"] in (.4,.5,.6)), key=lambda g:g["rate"])
        lat.append((label, [(g["actual_offered_throughput_mean"], g["latency_mean_completed"]) for g in gs]))
    svg_plot(OUT/"figure2_latency.svg", "Average packet latency", "actual offered packet throughput", "cycles", lat)
    # RandomCandidate successful-run means; failed seeds remain in the note.
    rc=[]
    for rate in (.4,.5):
        paths=(ROOT/"runs").glob(f"hybrid_deterministic_uniform_random_r{rate:.3f}_s*_source_route_random_candidate/result.json")
        rows=[json.loads(p.read_text()) for p in paths if json.loads(p.read_text()).get("measurement_cycles")==100000]
        ok=[r for r in rows if not r.get("no_progress")]
        if ok: rc.append((statistics.mean(r["actual_offered_throughput"] for r in ok), statistics.mean(r["accepted_throughput"] for r in ok)))
    qr=[(g["actual_offered_throughput_mean"],g["throughput_mean_completed"]) for g in agg if g["topology"]=="hybrid" and g["escape_enabled"] and g["rate"] in (.4,.5)]
    svg_plot(OUT/"figure3_routing.svg", "Hybrid routing-policy ablation", "actual offered packet throughput", "accepted packet throughput", [("q+r",sorted(qr)),("RandomCandidate",rc)], "RandomCandidate: 1/3 watchdog at each rate")
    # Progress comparison at rate 0.50; encode completed fraction as y.
    esc=[]; no=[]
    for i,key in enumerate(("mesh","hybrid")):
        e=[g for g in agg if g["topology"]==key and g["rate"]==.5 and g["escape_enabled"]][0]
        n=[g for g in agg if g["topology"]==key and g["rate"]==.5 and not g["escape_enabled"]][0]
        esc.append((i,e["completed"]/e["samples"])); no.append((i,n["completed"]/n["samples"]))
    svg_plot(OUT/"figure4_escape.svg", "Escape vs NoEscape progress at rate 0.50", "0=Mesh, 1=Hybrid", "completed-run fraction", [("Escape",esc),("NoEscape",no)])
    print(f"Wrote 4 SVG figures to {OUT}")

if __name__ == "__main__": main()
