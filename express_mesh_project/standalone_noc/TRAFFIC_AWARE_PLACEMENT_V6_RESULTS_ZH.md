# Traffic-aware Express-Link 布线 V6：SA 与流近似实验

本文记录本轮只改变 **express-link 布线** 的实验。最重要的控制变量是：所有候选 topology 最终都使用完全相同的 routing，即 source-route policy 4、每对源/目的保留 top-8 committed routes、mesh segment 使用 XY、`reservation_weight=0.375`、`express_vc_weight=0.625`，并保留 escape VC。因而下面的差异不能归因于 routing 参数变化。

## 1. 结论

对于一个已经确定、且布线算法知道其 traffic matrix 的 traffic，simulation-guided SA 确实能稳定越过逐边 Greedy 的局部最优。在 8×8、express wire budget 64、每个 router 最多一条 express link、最短线长 3、express latency 1、injection rate 0.8 的正式 Garnet 长测中，最佳 SA 相比对应 traffic-aware Greedy-ASPL 的 throughput 提升如下：

| Traffic | Greedy throughput / latency | 最佳 SA | SA throughput / latency | Throughput 变化 | Latency 变化 |
|---|---:|---|---:|---:|---:|
| Uniform random | 0.340667 / 9275 | Reheating SA | 0.355321 / 6626 | **+4.30%** | **-28.57%** |
| CutStress | 0.195695 / 1244 | Geometric SA | 0.199604 / 79 | **+2.00%** | **-93.67%** |
| Bit-complement | 0.275805 / 17391 | Parallel tempering | 0.278933 / 17431 | **+1.13%** | +0.23% |
| Tornado | 0.379366 / 2459 | Parallel tempering | 0.389261 / 1528 | **+2.61%** | **-37.87%** |

每个数是 3 个 seed、20k warmup + 100k measurement cycles 的均值，所有 24 个 run 均正常到达 simulation limit。结论不是“SA 总能大幅胜出”，而是当前约束和固定 routing 下，SA 能取得约 1.1%–4.3% 的可复现实测 throughput 增益；Uniform 和 Tornado 的 latency 改善也很明显。

网络流近似没有普遍胜过 Greedy：它只在 CutStress 上有效，在 Uniform、Bit-complement 上明显较差，在 Tornado 上与 Greedy-ASPL 接近。它仍然很有诊断价值，因为它说明“离线最小化理想 fractional flow 的最大边负载”和“固定 policy-4 实际产生的吞吐”并不是同一个目标。若最终目标是这套 routing 的真实性能，目前最有效的方法是直接以仿真 throughput/latency 为目标做 SA。

Hotspot 是例外而不是 SA 失败：当前定义把 50% 流量集中到两个目的 router。在 rate 0.8 下，目的 NI/ejection port 已经成为 express topology 无法消除的容量上限。standalone 中各种方法的 throughput 都约为 0.060；Garnet 长测的 6 个 Greedy/SA run 中有 5 个触发 NI watchdog，唯一正常结束的 run 也是 0.0602 throughput、约 4.86 万周期 latency。这里的 NI watchdog 表示注入长期得不到 VC，并不能单独证明 channel deadlock；结合不可避免的目的端超载，应解释为这个负载点没有“靠布线得到稳定高吞吐”的可行解。

## 2. 固定条件与比较基准

所有布线都从完整候选集合中选边，没有 stride、axis-aligned 等额外人工限制。候选 edge 的 Manhattan wire length 至少为 3；总 wire length 不超过 64；每个 router 的 express degree 不超过 1；`ideal` latency model 将每条 express link latency 设为 1。搜索中的 mutation 会同时拆掉并重连多条边，但每个中间候选都会重新检查这些约束，并通常使用 62–64 的 budget。

三个 traffic-aware Greedy baseline 都使用已知 traffic matrix：ASPL 每一步选择单位 wire cost 带来最大 demand-weighted shortest-path length 降幅的 edge；Bottleneck 每一步选择使 equal-shortest-path flow 的最大 edge load `Lmax` 降幅最大的 edge；Hybrid 以 0.5/0.5 组合归一化 ASPL 和 `Lmax`。它们都有不可回退的共同问题：一旦早期 edge 占用了两个 endpoint 和 wire budget，后面不能用一次多边替换纠正它。Bottleneck Greedy 在 CutStress 和 Tornado 上甚至返回零条 edge，因为没有任何单独一条 edge 能立即降低全局最大负载；这正是只允许单步正收益的局限。

Traffic 包括 Uniform random、左右镜像的 CutStress、两个中心热点且热点概率为 0.5 的 Hotspot、`(x,y)->(7-x,7-y)` 的 Bit-complement，以及每行水平偏移 3 的 Tornado。rate 0.8 在 Uniform、Hotspot、Bit-complement、Tornado 中对应每 router 约 0.4 packets/cycle offered load；CutStress 只有左半边节点注入，对应 0.2 的全网归一化 offered load。

## 3. 尝试的 SA 变种

三种 SA 都从 Greedy-ASPL、Greedy-Bottleneck、Greedy-Hybrid 和流搜索结果中选择初态，搜索目标直接来自不变 routing 下的 standalone 仿真。单 traffic 目标以 `throughput / offered_capacity` 为主，并带很小的 latency penalty；多 traffic 目标为各类 normalized throughput 的均值，加 `0.25 × worst-case throughput`，再减很小的 latency penalty。搜索阶段每个 topology 用 seed 1、2，500 warmup + 2000 measurement cycles 快速筛选；最终用三个 seed、5000 + 30000 standalone 长验证，再用 Garnet 20k + 100k 验证赢家。

`sa-geometric` 是经典多起点 SA：每次随机拆掉 1–3 条 edge 后重新合法填满 budget，温度从 0.02 几何下降到 0.0005；每个 traffic 使用 3 个 restart、每个 25 次迭代。它证明即使没有复杂 proposal，允许接受临时变差解和多边回退也能越过 Greedy 的局部最优。

`sa-reheat` 使用 traffic-aware proposal：低直接收益的 edge 更容易被拆除，新 edge 从 demand-aware 排序池中按指数 rank 采样；一般拆 1–4 条，周期开始时会做拆 4–7 条的大邻域跳跃。温度每 12 步重新加热，使用 2 个 restart、每个 35 次迭代。它是 Uniform 上最好的变种，也是四类 traffic mixture 中最好的综合 topology。

`sa-tempering` 同时维护 4 个固定温度 replica，每个 replica 独立 mutation，每 5 步在相邻温度之间尝试交换状态；低温 replica 做 guided mutation，高温 replica 允许大邻域 mutation。每类 traffic 跑 25 次迭代。它在 Bit-complement 和 Tornado 上找到最佳 topology。这里三种搜索是逐步增强的 pipeline：后一个变种允许把前一个变种的赢家作为初态，因此表格比较的是“增加这种搜索能力后能找到的最好结果”，不是相同 CPU budget 下彼此完全独立的算法竞赛。

standalone 的完整单 traffic 结果如下，格式均为 throughput / packet latency：

| Traffic | Greedy ASPL | Greedy bottleneck | Greedy hybrid | Flow-LNS | SA geometric | SA reheat | SA tempering |
|---|---:|---:|---:|---:|---:|---:|---:|
| Uniform | 0.338867 / 2710 | 0.271068 / 4916 | 0.275082 / 4656 | 0.297622 / 5697 | 0.343860 / 2336 | **0.353609 / 1891** | **0.353609 / 1891** |
| CutStress | 0.195384 / 350 | 0.093750 / 7106 | 0.154320 / 3517 | 0.197679 / 189 | **0.199712 / 11** | 0.196633 / 304 | **0.199712 / 11** |
| Hotspot | 0.059501 / 14057 | 0.059749 / 11930 | 0.059960 / 12370 | 0.059953 / 14326 | 0.059777 / 12590 | 0.060031 / 13672 | 0.059805 / 14161 |
| Bit-complement | 0.273720 / 4985 | 0.169186 / 8482 | 0.164690 / 8824 | 0.248180 / 5984 | 0.273720 / 4985 | 0.273720 / 4985 | **0.277944 / 4780** |
| Tornado | 0.380612 / 698 | 0.210176 / 6846 | 0.365613 / 1022 | 0.378526 / 736 | 0.382334 / 633 | 0.385685 / 564 | **0.390198 / 413** |

## 4. 流/网络容量近似

本轮实现的是 dependency-free、path-based 的 splittable multicommodity-flow 近似，不是假称的 exact LP。对每个 commodity，它构造与实际 routing 同型的 top-8 route：mesh segment 固定 XY，搜索阶段允许 0 或 1 次 express traversal，finalist 完整复评分允许最多 2 次。每份 demand 被切成若干小份，逐份选择对 directed-link load 的六次方和增量最小的 route，相当于用 convex `p`-norm 逼近最小化最大链路负载；source injection 和 destination ejection load 也计入 capacity bound。

在这个代理目标上尝试了两种 topology 搜索。`flow-swap` 每轮产生多个 traffic-aware 小邻域 swap，选 flow utility 最好的候选；`flow-lns` 加入拆 3–8 条 edge 的 large-neighborhood search，并在停滞时从当前最好 topology 扰动重启。单 traffic 分别大约评价 60 和 90 个候选；多 traffic 分别评价 81 和 144 个候选。Bit-complement 的 LNS 确实继续提高了 flow proxy，说明搜索本身不是简单卡死，但真实仿真 throughput 仍从 Greedy 的 0.2737 降到 0.2482。

这种不一致的具体机制是：fractional flow oracle 可以精确地按它计算出的比例把 commodity 分散到多条 path，并用全图所有 mesh/express link load 决策；实际 policy 4 是 packet-level committed route，只通过 express q/r pressure 区分 top-8 candidate，mesh edge 在选路时没有对应的全局负载信息，也不会执行 oracle 的分流比例。于是一个离线看来负载均匀的 topology，实际可能把多个 route 的 XY mesh segment 汇聚到同一位置。Flow-LNS 只在路径结构非常明确的 CutStress 中把 throughput 从 0.19538 提至 0.19768、latency 从 350 降至 189；在其他 traffic 上不能作为最终布线目标。

这也给出了后续“更像网络流”的正确方向：不是简单增加 LP 精度，而是建立 **routing-realizable flow model**。例如把 policy-4 的 route score 和随机 q/r 状态离散成可实现的 route-splitting constraints，或先由 flow 得到目标分流比例，再离线求一组可由局部 pressure threshold 实现的权重。否则 exact MCF 只会给出更精确的错误代理。

## 5. 已知 traffic 可能是多类时的一组 topology

我们进一步假定 workload 可能等概率属于 Uniform、CutStress、Bit-complement、Tornado，但芯片只能制造一组 express links。Hotspot 没放进优化目标，因为其 worst-case normalized throughput 被不可改变的 destination NI capacity 固定，加入 worst-case term 只会用常数项淹没 topology 差异；不过最后仍在 Hotspot 上做了交叉验证。

对这四类 traffic 的混合 demand 分别生成 Greedy-ASPL、Bottleneck、Hybrid，并运行 Flow-LNS 和三种 SA。最终 `mix_sa_reheat` 在 Garnet 中的四类 normalized throughput 平均值为 **0.7547**，最差值为 **0.6257**；`mix_flow_lns` 分别为 0.7442 和 0.5278；Greedy-Bottleneck 为 0.5511 和 0.4305；Greedy-Hybrid 为 0.5896 和 0.4355。因而 SA 是当前最好的单 topology 折中，flow 是第二名。

| Traffic | Mix bottleneck | Mix hybrid | Mix Flow-LNS | Mix SA-reheat | 对应 traffic 专用 SA |
|---|---:|---:|---:|---:|---:|
| Uniform | 0.310504 / 12088 | 0.290716 / 16435 | 0.323452 / 10481 | **0.335582 / 9571** | 0.355321 / 6626 |
| CutStress | 0.093750 / 24838 | 0.110133 / 22158 | **0.165968 / 5459** | 0.159054 / 6874 | 0.199604 / 79 |
| Bit-complement | 0.172194 / 32196 | 0.174196 / 30430 | 0.211130 / 27311 | **0.250298 / 20521** | 0.278933 / 17431 |
| Tornado | 0.211563 / 23719 | 0.258201 / 16272 | **0.324230 / 7363** | 0.303503 / 10793 | 0.389261 / 1528 |

表中均为 3-seed Garnet 长测。Mix-SA 在 Uniform 和 Bit-complement 上强于 Mix-Flow，Mix-Flow 在 CutStress 和 Tornado 上强于 Mix-SA；SA 的平均和 worst-case 更高，但它并不逐项支配 flow。与每种 traffic 单独制造专用 SA topology 相比，通用 topology 仍有明显代价，说明 degree 1 和 wire budget 64 下不存在一组边同时覆盖所有主要通信方向。

Mix-Greedy-ASPL 没放进上表，因为它不是一个可靠的有效样本：Uniform seed 1、2 触发 Garnet NI watchdog，只有 seed 3 正常结束且 throughput 仅 0.136734。旧汇总错误地把两个失败 run 当作 throughput 0 求均值；验证器现已修复为只统计 `termination_reason=simulate_limit` 的样本，并同时写出 `failed_sample_count`。该 topology 在 standalone 的 Uniform 结果也有极大的 seed 波动，因此应该判为不稳定、不能用其 1-seed Garnet 数字与其他 3-seed topology 排名。

在未参与 mixture 目标的 Hotspot 上，standalone 的 Mix-SA throughput 为 0.059925，专用 Greedy-Hybrid 为 0.059960，专用 SA 为 0.060031，均处在同一容量平台；说明不纳入 Hotspot 没有失去可通过 express 布线获得的实质收益。

## 6. 实现、复现与下一步

新增实现位于：

- `flow_placement.py`：top-8 path-based fractional multicommodity-flow 代理。
- `search_placement_advanced.py`：Geometric SA、Reheating SA、Parallel Tempering、Flow-Swap、Flow-LNS。
- `generate_traffic_aware_greedy_v6.py`：为每类 traffic 生成三种 Greedy baseline。
- `generate_traffic_mixture_greedy_v6.py`：生成多 traffic mixture Greedy baseline。
- `validate_traffic_aware_placements.py`：固定 routing 的 standalone 批量验证。
- `validate_garnet_placements.py`：固定 routing 的 Garnet 长测，并正确区分正常结束与 watchdog failure。

所有生成 topology 和搜索轨迹保存在 `express_mesh_project/results/placement_v6/`；standalone 汇总保存在 `standalone_noc/results/placement_v6/`；Garnet 原始长测保存在被 `.gitignore` 排除的 `results/garnet_v6_specialized/` 与 `results/garnet_v6_mixture/`。代码单元测试共 15 项，全部通过。

下一步最值得做的不是无限延长现有 SA，而是两项更针对性的工作。第一，增加独立 search seeds 和更长的 final-evaluation-in-the-loop：目前 60–104 个 simulation-evaluated topology 已经找到 1%–4% 增益，但还不能宣称全局最优；可将每类 traffic 的当前赢家作为 5–10 个独立 LNS/tempering chain 的初态，并用 successive halving 逐步延长测量。第二，做 routing-realizable flow/SA hybrid：Flow-LNS 负责产生结构多样的 topology，最后几轮完全改用 Garnet/standalone 的固定 policy-4 utility 选择，而不要继续相信 fractional flow proxy。多 workload 场景还可以调节 average 与 worst-case 权重，输出一条 Pareto frontier，而不是只给当前 `mean + 0.25×worst` 的单点折中。
