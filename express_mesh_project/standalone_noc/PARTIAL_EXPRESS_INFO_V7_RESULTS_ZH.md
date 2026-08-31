# Express 阻塞信息不完整的 Adaptive Routing V7 实验

> **后续审计说明（V8）**：V7 的 q/r advertisement 本身有传播延迟，但 source 在选路时会瞬时修改 express 入口的全局 reservation counter；因此本报告的“物理解释”只适合看作信息延迟敏感性实验，不是一套闭合的硬件协议。V8 已用显式 route-setup、入口登记、返回 ACK、source-local pending shadow 和延迟 cancellation 取代这次瞬时远端写入；V7 的数据保留作历史对照，最终物理机制与新实验见 `PHYSICAL_EXPRESS_INFO_V8_RESULTS_ZH.md`。

本文只研究 routing 在选择既有 top-8 committed route 时能够看到多少 express-link 阻塞信息，不重新搜索 topology，也不改变 V6 的 SA。比较中的 Mesh、20 个 Random placement、traffic-aware Greedy-ASPL 和对应的 SA Pareto topology 都使用同一套 routing：source-route policy 4、每对 source/destination 的 top-8 候选、mesh segment 固定 XY、`reservation_weight=0.375`、`express_vc_weight=0.625`、4 VCs/vnet 和原有 escape VC。因而下面 Greedy 与 SA 的差别来自 topology，完整信息与不完整信息的差别来自信息机制。

## 1. 结论

当前最稳妥的非理想信息配置是 `distance-gossip / period=1 / base-delay=1 / 4-bit / admission=0.75`，下文简称 **P1-D1-B4-A75**。每条有向 express link 的入口只产生自己的 `q` 和 `r`；更新按 base mesh 每跳一 cycle 传播，所以 source router 使用的信息至少旧 1 cycle，最远时旧 `1+14=15` cycles，而不是瞬时读取全网状态。`q` 和 `r` 各自量化到 4 bit；`q` 的实际范围为 0--3，因此没有量化损失，`r` 在 15 截断。每个 router 保存最近收到的值。

延迟信息会使许多 packet 同时根据同一份旧低压状态涌向 express link。A75 是一个不需要通信的局部 hedge：policy 4 仍然先从 top-8 中选最低代价 route；若它含 express，packet id、source 和 destination 的固定 hash 让 75% packet 接受该 route，另外 25% 直接采用纯 XY route。它可由很小的 LFSR/hash 实现，不读取 mesh congestion，也不改变候选评分。它的作用不是假装知道更多信息，而是降低旧信息导致的同步 herd。route 仍在 injection 时一次性承诺，escape 的 VC、timeout、单向转换和 XY channel dependency graph 均未改变，因此没有引入新的 deadlock dependency。

P1-D1-B4-A75 在 standalone 的 Uniform/Tornado、rate 0.7/0.8 四个配置中都得到观测均值上的 `Mesh < Random expectation < Greedy < SA`，throughput 和 latency 两个方向同时成立。完整 Garnet 在 rate 0.8 上也复现了这一级序：

| Traffic / Garnet 20k+100k | Mesh | Random expectation（20 layouts × 3 seeds） | Greedy | SA Pareto |
|---|---:|---:|---:|---:|
| Uniform throughput | 0.266210 | 0.277365 | 0.332020 | **0.334949** |
| Uniform latency | 17886 | 17305 | 10892 | **10003** |
| Tornado throughput | 0.211563 | 0.225681 | 0.349058 | **0.351491** |
| Tornado latency | 23719 | 21281 | 5999 | **5352** |

Uniform 中 Greedy 相对 Mesh/Random 的 throughput 分别提高 **24.72%/19.71%**，Tornado 中分别提高 **64.99%/54.67%**。SA 相对 Greedy 的 throughput 增益较小，Uniform/Tornado 分别为 **0.88%/0.70%**，但 latency 同时降低 **8.16%/10.78%**，所以在这两个 traffic 上仍然是 Pareto 改善。全部 Greedy、SA、Mesh run 都在默认 50k watchdog 下正常完成。

这个结果满足的是有限样本中的 **Random expectation**，不是“每一个 Random 都优于 Mesh”。Uniform Random 的 layout 方差极大：20 个 placement 的 Garnet throughput 从 p19 的 0.110613 到 p10 的 0.318409；Random 样本标准差为 0.06291，均值与 Mesh 的差距没有达到保守的 95% 置信强度。Tornado Random 方差小得多，均值顺序更稳。论文中可以写“20-layout empirical Random mean”，不能写成数学保证或所有随机布局保证。

## 2. 原始 full-information 模式是否保留

原来的模式完整保留，并仍是默认值：`--express-info-mode instant --express-admission-fraction 1.0`。此时不调度 advertisement event，policy 4 仍直接读取当前 express output VC occupancy 和 reservation，准入器永远接受，代码路径与 V6 相同。

用重新编译后的 Garnet 对 Uniform Greedy seed 1 做了 20k warmup + 100k measurement 回归。V7 instant 与原 V6 存档的 throughput 都为 `0.34056828125`，latency 都为 `9326.849207`；packets injected/received、express traversals、escape fraction、average hops 和 max-link utilization 也逐字段完全相同。standalone 的 instant 回归同样保持原结果。新增模式没有覆盖或暗中改变旧实验。

## 3. 尝试的信息模型和失败机制

实现并筛选过三类信息视图。`instant` 是原始 oracle-like baseline；`delayed-global` 让所有 router 在相同固定 delay 后看到广告，用来隔离“仅时间变旧”的影响；`distance-gossip` 使用 `base delay + source 到 express 入口的 Manhattan distance`，表示更新沿 mesh 逐跳传播。三者都可配置 advertisement period、base delay 和 0--4 bit 量化，其中 0 bit 表示精确整数。最终只把这三种通用视图移植到 Garnet，旧 full-information 仍为默认。

结果显示信息不能任意削弱。短窗口 Uniform 0.8 中，`global P4-D1-B2` 的 Mesh/Random/Greedy/SA throughput 为 `0.26575/0.20195/0.26008/0.34768`；`gossip P8-D1-B2` 为 `0.26575/0.19638/0.25799/0.34181`；`gossip P4-D1-B1` 更降到 `0.26575/0.15510/0.18334/0.27857`。旧状态本身会产生 herd，1--2 bit 又不能区分中等和严重 reservation，Random 和 Greedy 的 express endpoint 更容易被成批误选。Tornado 的需求方向较固定，匹配 topology 即使在旧信息下仍有大幅结构收益，所以对这些削弱更不敏感。

`P1-D1-B4` 不加 A75 时，短测曾保持顺序，但扩大到更多 Random layout 后，Uniform 0.8 的 Random mean 为 0.25350，低于 Mesh 0.26561；甚至原始 instant 在同一批样本中的 Random mean 也只有 0.25982。这说明用户希望的四层顺序并不是原始 full-information 在任意有限 Random 集合上天然成立。A75 的价值主要是让坏 Random topology 少量使用有害 express route，同时 Greedy/SA 的高价值 express route仍保留大部分流量。

还试过只给一定 Manhattan radius 内的 express link 当前信息、只用 express 入口的本地 reservation 修正、固定 confidence/guard margin 等方式。局部 radius 会令未知远端 edge 看起来过于便宜；本地修正无法反映远端即将到达的 committed packet；固定 margin 虽能修复某个 Uniform 点，却会令 Tornado 的 SA 低于 Greedy，margin 足够大时又退化为少用 express。这些机制已经从最终 C++ 中删除，只在被 gitignore 排除的试验结果中保留记录。

为了进一步减半控制频率，又完整验证了 `P2-D1-B4-A75`。它在 8-Random 短测的四个点全部成功，Tornado 0.8 甚至提高到 Greedy/SA `0.3463/0.3486`；但 20-Random、3-seed、5k+30k 长测的 Uniform 0.8 得到 `Mesh 0.2661 > Random 0.2566 < Greedy 0.3305 < SA 0.3387`，不满足目标，因此 P2-A75 没有作为 final preset 或 Garnet winner。这个案例说明 topology sample 数不足会给出相反结论。

## 4. Standalone 完整结果

standalone 正式矩阵包含 276 个 run：两个 traffic、两个 rate、Mesh/Greedy/SA 各 3 traffic seeds，以及 20 个 Random placement 各 3 seeds；均为 5k warmup + 30k measurement，全部正常到达 simulation limit。表中的 Random 是 60 个 run 的 pooled mean。

| Traffic / rate | Mesh th / lat | Random expectation th / lat | Greedy th / lat | SA th / lat |
|---|---:|---:|---:|---:|
| Uniform 0.7 | 0.265727 / 3590 | 0.277167 / 2719 | 0.330648 / 958 | **0.338847 / 569** |
| Uniform 0.8 | 0.266064 / 4976 | 0.273091 / 4858 | 0.330552 / 3129 | **0.338016 / 2674** |
| Tornado 0.7 | 0.209913 / 5593 | 0.222705 / 4837 | 0.327230 / 893 | **0.329538 / 781** |
| Tornado 0.8 | 0.210176 / 6846 | 0.223746 / 6172 | 0.337505 / 2139 | **0.341408 / 1886** |

rate 0.8 时，Uniform Greedy/SA 查询值相对即时真值的 q MAE 为 0.772/0.838、r MAE 为 1.132/1.198；Tornado 为 q 0.757/0.811、r 0.764/0.812。也就是说 winner 并不是靠延迟恰好很小而等效于 instant：路由确实在使用有明显误差的状态，仍能保留 topology 层级。

相对同一 standalone 的 instant，P1-D1-B4-A75 在 rate 0.8 将 Uniform Greedy/SA throughput 降低约 2.45%/4.41%，Tornado 降低约 11.33%/12.50%。Tornado 的退化更大，但 Greedy 相对 Mesh 的收益仍有 60.6%。因此准确表述应是“保留大部分 topology advantage”，不能说不完整信息没有性能成本。

## 5. Garnet 长测、watchdog 与机制指标

Garnet 正式配置为 rate 0.8、20k warmup + 100k measurement、traffic seeds 1/2/3。Tornado 的 69 个 run 全部在 50k watchdog 下完成。Uniform 的 Mesh、Greedy、SA 9 个 run 和大部分 Random 也正常，但 Random p6/p10/p13/p19 中 7 个 run 触发 50k NI watchdog。将这四个 placement 的全部 12 个 seed/config 用 150k threshold 重跑后 12/12 都到达 simulation limit；最差 p19 的 throughput/latency 是 0.110613/42973。最终 Random expectation 使用这些完整 retry 替换对应行，没有把失败记为零，也没有把失败样本删除。

这组现象更符合严重 injection starvation，而不是永久 channel deadlock：提高 watchdog 后相同 deterministic run 能完成，escape 机制、route 和网络负载均未改变。但它仍是重要的 robustness 警告：某些 Random placement 在 Uniform 高压下会让个别 NI 超过 50k cycles 拿不到普通 VC。Greedy 和 SA 没出现这个问题，反而进一步支持 topology-aware placement 的优势。

机制指标也与性能排序一致。Uniform 中 Mesh/Random/Greedy/SA 的 express traversals per delivered packet 为 `0/0.229/0.386/0.339`，escape fraction 为 `2.69%/8.01%/3.40%/3.22%`；SA 虽少用一些 express，但 max-link utilization 从 Greedy 的 0.582 降到 0.546，说明它的收益来自更均衡的布局而非简单增加 express 使用。Tornado 中 Greedy/SA 都约为 0.485 express traversals/packet，escape fraction 只有 0.392%/0.345%，远低于 Mesh 的 5.14%。

相对 Garnet instant 的 V6 结果，P1-D1-B4-A75 将 Uniform Greedy/SA throughput 降低 2.54%/5.73%，Tornado 降低 7.99%/9.70%。在过载工作点 latency 对很小的服务率差异非常敏感，因此相对 instant 的 latency 增幅更大；但是相对 Mesh/Random，partial-info Greedy/SA 的 latency 仍显著更低。最终图表应同时画 throughput 和 latency，不能只报告 throughput 百分比。

## 6. 控制面的物理解释和边界

P1 每周期采样不要求硬件反复发送相同值。standalone 最终实现只在量化后的 q 或 r 改变时产生一次 event-driven advertisement；接收端保持最后值。用同一代码和 seed 做受控 A/B，event-driven 与逐周期重复发送在除更新计数外的所有输出字段完全相同，而 30k-cycle Uniform Greedy run 的 router-update 次数从 65,280,000 降到 34,136,576，减少 47.7%。

V6 topology 有 16--18 条无向 express link，即 32--36 个有向状态源；当前编码每个方向 q/r 共 8 bit，原始状态产生率上限约为 256--288 bit/network-cycle，event-driven 在上述 Uniform 样本中约减半。每个 router 需要保存约 256--288 bit 状态加 valid bit；由于 q 只有 0--3，实际硬件可把 q 压到 2 bit，使每个方向共 6 bit。hash admission 不需要额外全网状态。

不过该设计仍不是“纯局部信息”：每个 router 最终会收到所有 express link 的旧状态。广播树、multicast 合并、控制 packet 的 wire/energy、以及控制信息与 data traffic 是否共享带宽都没有在当前 Garnet 中建模。因此 V7 支持的结论是：**即时全局信息并非保持 topology 排序所必需；带 1--15 cycle 距离延迟、4-bit 量化和 event-driven 更新的信息仍可工作。** 它还不能证明控制网络的面积与功耗可以忽略。更低频 P2、1--2 bit 和严格局部信息目前没有同时保持目标排序，不能作为已有 winner 宣称。

## 7. 实现与复现

`express_noc.cpp` 增量加入 `instant/delayed-global/distance-gossip`、period、delay、quantization 和 admission 参数，默认值完全保持旧行为；`run_partial_info_experiments.py` 固定 routing/topology protocol，自动聚合 20-layout Random expectation 和四层 hierarchy。Garnet 的对应参数位于 `GarnetNetwork.py/.hh/.cc` 与 `configs/network/Network.py`，`run_phase3_measurement_v2.py` 和 `validate_garnet_placements.py` 已能传递、缓存和聚合这些配置。后者还支持将更高 watchdog 的完整 retry 补入汇总，避免遗漏坏样本。

standalone winner 的复现命令为：

```bash
python3 express_mesh_project/standalone_noc/run_partial_info_experiments.py \
  --traffics uniform_random tornado --rates 0.7 0.8 \
  --traffic-seeds 1 2 3 --random-seeds {1..20} \
  --info-configs gossip-p1-d1-b4-a75 \
  --warmup-cycles 5000 --measurement-cycles 30000 --workers 4 \
  --output-dir express_mesh_project/standalone_noc/results/partial_info_v7/recheck
```

原始结果位于被 `.gitignore` 排除的 `standalone_noc/results/partial_info_v7/`、`results/garnet_v7_partial_info/` 和 `results/garnet_v7_instant_regression/`。正式代码已通过 standalone self-test、15 个 Python 单元测试、Python syntax check、`git diff --check` 和完整 `build/Garnet_standalone/gem5.opt` 编译。
