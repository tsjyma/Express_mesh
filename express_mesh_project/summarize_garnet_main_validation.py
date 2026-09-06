#!/usr/bin/env python3
"""Compare Garnet headline main-result points with the standalone suite."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import statistics


ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "express_mesh_project"
DEFAULT_GARNET = (PROJECT / "results" / "20260906" /
                   "garnet_main_validation")
DEFAULT_STANDALONE = (PROJECT / "results" / "20260831" /
                      "standalone_suite" / "results.json")
DEFAULT_REPORT = (PROJECT / "standalone_noc" /
                  "GARNET_MAIN_VALIDATION_20260906_ZH.md")
LABELS = {
    "uniform_random": "Uniform",
    "tornado": "Tornado",
    "bit_complement": "BitComp",
    "cutstress_bidirectional": "CutStress",
    "mesh": "Mesh", "random": "Random", "greedy": "Greedy", "sa": "SA",
}


def aggregate_standalone(path: Path, wanted: set[tuple]) -> dict[tuple, dict]:
    rows = json.loads(path.read_text(encoding="utf-8"))
    groups: dict[tuple, list[dict]] = {}
    for row in rows:
        if row.get("section") != "main":
            continue
        key = (row["traffic"], row["configured_injection_rate"],
               row["topology_class"])
        if key in wanted:
            groups.setdefault(key, []).append(row)
    result = {}
    for key, samples in groups.items():
        result[key] = {
            "throughput": statistics.fmean(
                float(row["accepted_throughput"]) for row in samples),
            "latency": statistics.fmean(
                float(row["average_packet_latency_cycles"])
                for row in samples),
            "samples": len(samples),
        }
    return result


def percent(new: float, old: float) -> float:
    return 100.0 * (new / old - 1.0) if old else float("inf")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--garnet-dir", type=Path, default=DEFAULT_GARNET)
    parser.add_argument("--standalone-results", type=Path,
                        default=DEFAULT_STANDALONE)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()
    aggregate = json.loads(
        (args.garnet_dir / "aggregate.json").read_text(encoding="utf-8"))
    wanted = {
        (row["traffic"], row["rate"], row["topology_class"])
        for row in aggregate
    }
    standalone = aggregate_standalone(args.standalone_results, wanted)
    comparisons = []
    for row in aggregate:
        key = (row["traffic"], row["rate"], row["topology_class"])
        if key not in standalone or row["accepted_throughput_mean"] is None:
            continue
        old = standalone[key]
        g_t = float(row["accepted_throughput_mean"])
        g_l = float(row["average_packet_latency_cycles_mean"])
        comparisons.append({
            "traffic": key[0], "rate": key[1], "topology": key[2],
            "g_t": g_t, "s_t": old["throughput"],
            "dt": percent(g_t, old["throughput"]),
            "g_l": g_l, "s_l": old["latency"],
            "dl": percent(g_l, old["latency"]),
            "g_n": row["completed_count"], "expected_n": row["sample_count"],
            "s_n": old["samples"],
            "active": row["active_source_min"],
            "mapping": row["mapping_fraction_min"],
        })
    comparisons.sort(key=lambda row: (
        row["rate"], row["traffic"],
        ("mesh", "random", "greedy", "sa").index(row["topology"]),
    ))
    failures = json.loads(
        (args.garnet_dir / "failures.json").read_text(encoding="utf-8"))
    complete = len(comparisons) == len(wanted) and not failures
    within = complete and all(
        abs(row["dt"]) <= 3.0 and abs(row["dl"]) <= 3.0
        for row in comparisons
    )
    throughput_within = sum(abs(row["dt"]) <= 3.0 for row in comparisons)
    latency_within = sum(abs(row["dl"]) <= 3.0 for row in comparisons)
    high_load_groups: dict[tuple, dict[str, float]] = {}
    for row in comparisons:
        if row["rate"] in {0.70, 0.80}:
            high_load_groups.setdefault(
                (row["traffic"], row["rate"]),
                {},
            )[row["topology"]] = row["g_t"]
    high_load_ordered = sum(
        all(name in group for name in ("mesh", "random", "greedy", "sa"))
        and group["mesh"] < group["random"] < group["greedy"] < group["sa"]
        for group in high_load_groups.values()
    )
    worst_t = max(comparisons, key=lambda row: abs(row["dt"]))
    worst_l = max(comparisons, key=lambda row: abs(row["dl"]))
    cutstress = [row for row in comparisons
                 if row["traffic"] == "cutstress_bidirectional"]

    lines = [
        "# Garnet 主实验复测与 standalone 对比（2026-09-06）", "",
        "本报告复测 `main.tex` 4.4 主结果表中的配置点。固定 Mesh、matched "
        "Greedy 和 matched SA 各使用 holdout traffic seeds 5--8；Random 使用 "
        "10 个独立 topology、每个 topology 使用 seeds 5--6。所有样本均为 "
        "20k warmup + 100k measurement，routing、q/r、distance-gossip、registered "
        "reservation 和 escape 参数与主实验一致。SA placement 没有重跑，仍由 "
        "standalone 评价器搜索。", "",
        "复测范围是正文三张 headline 表的 384 个生产规格样本，而不是四张曲线"
        "的全部饱和区细扫点；后者按同一 seed 设计需 5,056 个 Garnet 样本。"
        "若 headline 点未通过替换判据，曲线继续保留 standalone 数据并明确标注，"
        "不把有限 Garnet 点插值成曲线。", "",
        "## 完整性与结论", "",
        f"- Garnet 成功形成 {len(comparisons)}/{len(wanted)} 个聚合单元；"
        f"subprocess failures={len(failures)}。",
        f"- 最大 throughput 相对差异为 {worst_t['dt']:+.2f}%："
        f"{LABELS[worst_t['traffic']]}/R={worst_t['rate']:.2f}/"
        f"{LABELS[worst_t['topology']]}。",
        f"- 最大 latency 相对差异为 {worst_l['dl']:+.2f}%："
        f"{LABELS[worst_l['traffic']]}/R={worst_l['rate']:.2f}/"
        f"{LABELS[worst_l['topology']]}。",
        f"- 48 个聚合单元中，throughput 有 {throughput_within} 个、latency 有 "
        f"{latency_within} 个落在 ±3% 内。",
        f"- Garnet 在 R=0.70/0.80 的 {len(high_load_groups)} 个 traffic-rate "
        f"组合中有 {high_load_ordered} 个保持严格 "
        "`Mesh < Random < Greedy < SA` throughput 顺序。",
        "- 判定规则：所有 throughput 和 latency 聚合值相对差异均不超过 3%，"
        "且没有缺失/失败，才允许用 Garnet 数字替换正文。",
        f"- 本轮判定：**{'可以替换' if within else '不应直接替换'}**。", "",
    ]
    if cutstress:
        lines += [
            "CutStress smoke/正式统计均要求全部 64 个 source 活跃。该组最小 "
            f"active-source count={min(row['active'] for row in cutstress)}，"
            f"最小正确目的映射比例={min(row['mapping'] for row in cutstress):.3f}。",
            "",
        ]
    lines += [
        "## 逐项结果", "",
        "每格延迟单位为 network cycles；差异定义为 "
        "`(Garnet / standalone - 1) × 100%`。", "",
        "| R | Traffic | Topology | Garnet T | Standalone T | ΔT | Garnet L | Standalone L | ΔL | Garnet samples |",
        "|---:|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in comparisons:
        lines.append(
            f"| {row['rate']:.2f} | {LABELS[row['traffic']]} | "
            f"{LABELS[row['topology']]} | {row['g_t']:.5f} | "
            f"{row['s_t']:.5f} | {row['dt']:+.2f}% | {row['g_l']:.1f} | "
            f"{row['s_l']:.1f} | {row['dl']:+.2f}% | "
            f"{row['g_n']}/{row['expected_n']} |"
        )
    if failures:
        lines += ["", "## 失败样本", "", "```json",
                  json.dumps(failures, indent=2, ensure_ascii=False), "```"]
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {args.report}; replace_main={within}")


if __name__ == "__main__":
    main()
