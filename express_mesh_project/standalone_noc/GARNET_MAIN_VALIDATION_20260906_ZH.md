# Garnet 主实验复测与 standalone 对比（2026-09-06）

本报告复测 `main.tex` 4.4 主结果表中的配置点。固定 Mesh、matched Greedy 和 matched SA 各使用 holdout traffic seeds 5--8；Random 使用 10 个独立 topology、每个 topology 使用 seeds 5--6。所有样本均为 20k warmup + 100k measurement，routing、q/r、distance-gossip、registered reservation 和 escape 参数与主实验一致。SA placement 没有重跑，仍由 standalone 评价器搜索。

复测范围是正文三张 headline 表的 384 个生产规格样本，而不是四张曲线的全部饱和区细扫点；后者按同一 seed 设计需 5,056 个 Garnet 样本。若 headline 点未通过替换判据，曲线继续保留 standalone 数据并明确标注，不把有限 Garnet 点插值成曲线。

## 完整性与结论

- Garnet 成功形成 48/48 个聚合单元；subprocess failures=0。
- 最大 throughput 相对差异为 +13.69%：Tornado/R=0.80/Greedy。
- 最大 latency 相对差异为 -52.45%：Tornado/R=0.70/SA。
- 48 个聚合单元中，throughput 有 44 个、latency 有 29 个落在 ±3% 内。
- Garnet 在 R=0.70/0.80 的 8 个 traffic-rate 组合中有 8 个保持严格 `Mesh < Random < Greedy < SA` throughput 顺序。
- 原定“两个实现可互换”的严格判据是：所有 throughput 和 latency 聚合值相对差异均不超过 3%，且没有缺失/失败；本轮结果没有达到这一校准判据，因此不能声称两个实现逐点等价。
- 正文 Tables 3--5 现直接报告更高保真度的 Garnet 数字，密集曲线仍报告 standalone 数字；这不是把 standalone 数据重标为 Garnet。替换前的 standalone 表值完整保留在下表中。
- Figure 9 也已由 Garnet `stats.txt` 中的 directed internal-link utilization 重画；对应可复现脚本为 `express_mesh_project/generate_garnet_main_link_load.py`。

代表性速度对比采用完全相同的 Uniform--Greedy、R=0.70、seed=5、20k warmup + 100k measurement 配置：standalone 用时 6.59 s，Garnet 的 `hostSeconds` 为 65.08 s，即 standalone 约快 9.9 倍。SA 搜索包含大量候选 placement 评价，因此固定使用 standalone 作为估价器，再把最终 JSON 原样交给 Garnet 验证。

## Figure 9 链路负载映射审计

第一次 Garnet 版本的 Figure 9 存在一个仅影响可视化的索引映射错误：脚本按 `ExpressMesh.py` 的 mesh-then-express 创建顺序解释统计向量，但 Garnet 的 `Topology::createLinks()` 实际按 `(source router, destination router)` 有序遍历 link map，再把 internal `NetworkLink` 依次加入统计向量。因此 utilization 数值本身正确，总和与 directed maximum 也没有改变，但颜色被分配到了错误的物理边，甚至会把两个并非反向配对的 directed channel 错误平均。生成脚本现已按 Garnet 的实际运行时顺序排序，并在合并前检查每条物理边恰好具有两个方向。

修正后，将同一组 Uniform、R=0.80、seed=5 的 Garnet 和 standalone 结果按 `(source,destination,is_express)` 逐边对齐，结果如下。`corr` 是 Pearson correlation，MAE 是每条物理边 utilization 的平均绝对差；二者均使用相同的 100k-cycle measurement 分母。

| Topology | Directed corr | Physical corr | Physical MAE | Standalone/Garnet physical max |
|---|---:|---:|---:|---:|
| Mesh | 0.9996 | 0.9999 | 0.0016 | 0.5636 / 0.5625 |
| Random p1 | 0.9993 | 0.9997 | 0.0025 | 0.6024 / 0.6037 |
| Greedy | 0.9985 | 0.9992 | 0.0038 | 0.5637 / 0.5630 |
| SA | 0.9984 | 0.9991 | 0.0036 | 0.5214 / 0.5158 |

作为反证，旧的错误索引下四类 topology 的 directed correlation 只有 -0.038、0.041、0.083、0.089。修正后的相关性均超过 0.998，且总 directed utilization 的 standalone/Garnet 差异分别只有 0.08%、-0.06%、-0.27% 和 -0.32%。这说明明显的图案差异来自绘图端索引错误，而不是 Garnet 链路活动统计、placement 文件或 routing 数据面错误。

CutStress smoke/正式统计均要求全部 64 个 source 活跃。该组最小 active-source count=64，最小正确目的映射比例=1.000。

## 逐项结果

每格延迟单位为 network cycles；差异定义为 `(Garnet / standalone - 1) × 100%`。

| R | Traffic | Topology | Garnet T | Standalone T | ΔT | Garnet L | Standalone L | ΔL | Garnet samples |
|---:|---|---|---:|---:|---:|---:|---:|---:|---:|
| 0.40 | BitComp | Mesh | 0.14976 | 0.14912 | +0.43% | 12799.2 | 12973.0 | -1.34% | 4/4 |
| 0.40 | BitComp | Random | 0.16782 | 0.16728 | +0.32% | 8110.1 | 8207.4 | -1.19% | 20/20 |
| 0.40 | BitComp | Greedy | 0.19543 | 0.19572 | -0.15% | 1278.3 | 1141.7 | +11.96% | 4/4 |
| 0.40 | BitComp | SA | 0.19982 | 0.19981 | +0.01% | 49.7 | 67.8 | -26.63% | 4/4 |
| 0.40 | CutStress | Mesh | 0.18750 | 0.18750 | -0.00% | 4069.0 | 4079.3 | -0.25% | 4/4 |
| 0.40 | CutStress | Random | 0.19349 | 0.19347 | +0.01% | 1961.9 | 1979.2 | -0.87% | 20/20 |
| 0.40 | CutStress | Greedy | 0.19987 | 0.19987 | +0.00% | 11.5 | 11.6 | -1.05% | 4/4 |
| 0.40 | CutStress | SA | 0.19986 | 0.19987 | -0.01% | 11.2 | 11.2 | -0.13% | 4/4 |
| 0.40 | Tornado | Mesh | 0.19983 | 0.19983 | -0.00% | 80.6 | 74.1 | +8.75% | 4/4 |
| 0.40 | Tornado | Random | 0.19958 | 0.19961 | -0.01% | 155.0 | 132.2 | +17.24% | 20/20 |
| 0.40 | Tornado | Greedy | 0.19981 | 0.19987 | -0.03% | 11.6 | 11.6 | -0.25% | 4/4 |
| 0.40 | Tornado | SA | 0.19983 | 0.19987 | -0.02% | 11.4 | 11.5 | -0.28% | 4/4 |
| 0.40 | Uniform | Mesh | 0.19986 | 0.19979 | +0.04% | 17.1 | 17.1 | +0.04% | 4/4 |
| 0.40 | Uniform | Random | 0.19989 | 0.19986 | +0.01% | 24.7 | 24.9 | -0.83% | 20/20 |
| 0.40 | Uniform | Greedy | 0.19985 | 0.19979 | +0.03% | 14.8 | 14.8 | -0.21% | 4/4 |
| 0.40 | Uniform | SA | 0.19987 | 0.19979 | +0.04% | 14.8 | 14.8 | -0.12% | 4/4 |
| 0.70 | BitComp | Mesh | 0.12977 | 0.12938 | +0.30% | 36081.7 | 36156.7 | -0.21% | 4/4 |
| 0.70 | BitComp | Random | 0.15840 | 0.15851 | -0.07% | 30544.0 | 30186.1 | +1.19% | 20/20 |
| 0.70 | BitComp | Greedy | 0.17307 | 0.17248 | +0.35% | 29631.1 | 29614.8 | +0.05% | 4/4 |
| 0.70 | BitComp | SA | 0.18525 | 0.18521 | +0.02% | 26266.3 | 26169.2 | +0.37% | 4/4 |
| 0.70 | CutStress | Mesh | 0.18750 | 0.18750 | +0.00% | 21341.9 | 21344.5 | -0.01% | 4/4 |
| 0.70 | CutStress | Random | 0.20722 | 0.20592 | +0.63% | 19160.8 | 19346.5 | -0.96% | 20/20 |
| 0.70 | CutStress | Greedy | 0.27599 | 0.27124 | +1.75% | 9372.6 | 10213.0 | -8.23% | 4/4 |
| 0.70 | CutStress | SA | 0.27857 | 0.27502 | +1.29% | 9543.4 | 10074.6 | -5.27% | 4/4 |
| 0.70 | Tornado | Mesh | 0.21056 | 0.20989 | +0.32% | 19536.1 | 19566.0 | -0.15% | 4/4 |
| 0.70 | Tornado | Random | 0.21705 | 0.21603 | +0.47% | 18435.2 | 18572.5 | -0.74% | 20/20 |
| 0.70 | Tornado | Greedy | 0.31634 | 0.29648 | +6.70% | 4343.7 | 8131.5 | -46.58% | 4/4 |
| 0.70 | Tornado | SA | 0.32583 | 0.30597 | +6.49% | 3265.7 | 6867.8 | -52.45% | 4/4 |
| 0.70 | Uniform | Mesh | 0.26593 | 0.26574 | +0.07% | 12787.9 | 12516.7 | +2.17% | 4/4 |
| 0.70 | Uniform | Random | 0.28210 | 0.28270 | -0.21% | 10438.2 | 10101.6 | +3.33% | 20/20 |
| 0.70 | Uniform | Greedy | 0.28962 | 0.29370 | -1.39% | 10680.0 | 9586.1 | +11.41% | 4/4 |
| 0.70 | Uniform | SA | 0.30206 | 0.30295 | -0.29% | 7791.8 | 7491.0 | +4.02% | 4/4 |
| 0.80 | BitComp | Mesh | 0.12833 | 0.12800 | +0.25% | 39760.1 | 39814.3 | -0.14% | 4/4 |
| 0.80 | BitComp | Random | 0.15723 | 0.15746 | -0.15% | 34750.0 | 34289.2 | +1.34% | 20/20 |
| 0.80 | BitComp | Greedy | 0.17136 | 0.17086 | +0.29% | 34324.6 | 34302.7 | +0.06% | 4/4 |
| 0.80 | BitComp | SA | 0.18359 | 0.18370 | -0.06% | 31355.5 | 31237.3 | +0.38% | 4/4 |
| 0.80 | CutStress | Mesh | 0.18750 | 0.18750 | +0.00% | 24845.0 | 24848.1 | -0.01% | 4/4 |
| 0.80 | CutStress | Random | 0.20704 | 0.20570 | +0.65% | 23170.7 | 23354.6 | -0.79% | 20/20 |
| 0.80 | CutStress | Greedy | 0.27913 | 0.27323 | +2.16% | 14626.0 | 15279.8 | -4.28% | 4/4 |
| 0.80 | CutStress | SA | 0.28275 | 0.27812 | +1.66% | 13794.8 | 14431.9 | -4.41% | 4/4 |
| 0.80 | Tornado | Mesh | 0.21157 | 0.21018 | +0.66% | 23721.1 | 23946.1 | -0.94% | 4/4 |
| 0.80 | Tornado | Random | 0.21824 | 0.21636 | +0.87% | 22790.1 | 23141.6 | -1.52% | 20/20 |
| 0.80 | Tornado | Greedy | 0.32888 | 0.28928 | +13.69% | 9139.1 | 14714.5 | -37.89% | 4/4 |
| 0.80 | Tornado | SA | 0.34117 | 0.30072 | +13.45% | 7762.2 | 13130.8 | -40.89% | 4/4 |
| 0.80 | Uniform | Mesh | 0.26627 | 0.26593 | +0.13% | 17866.0 | 17327.0 | +3.11% | 4/4 |
| 0.80 | Uniform | Random | 0.28271 | 0.28326 | -0.20% | 16733.9 | 16170.4 | +3.49% | 20/20 |
| 0.80 | Uniform | Greedy | 0.28653 | 0.29375 | -2.46% | 17871.7 | 16195.4 | +10.35% | 4/4 |
| 0.80 | Uniform | SA | 0.30288 | 0.30319 | -0.10% | 14124.3 | 13493.1 | +4.68% | 4/4 |
