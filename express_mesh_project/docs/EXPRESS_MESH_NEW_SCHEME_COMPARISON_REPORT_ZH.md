# 新方案相对旧报告的改进与实验总结

## 1. 结论先行

新方案把旧报告中的“逐跳局部 adaptive heuristic”改成了“注入时一次性选择完整 express 候选路径”的机制，并加入了 express reservation。这样可以同时回答两个问题：全局候选规划是否有用，以及 `r[e]` 是否能缓解多个 packet 同时抢同一条 express link。

目前结果表明：q+r 主策略和测量链路已通过 smoke、q/r 账目和长窗口基本验证；在接近饱和的 Uniform Random 流量下，Hybrid 在 `rate=0.50` 仍有约 `0.144` accepted throughput、约 `2.15e4` cycles 平均延迟，但尚未证明稳定优于纯 Mesh。Reservation 能减少 express 使用和集中竞争，但当前数据没有显示明确吞吐收益。NoEscape 的 6 个 near-knee case 全部触发 deadlock watchdog；统一长窗口的 RandomCandidate 也有 1/3 seed 失败，说明 correctness 风险尚未完全关闭。

## 2. 相比 `BUDGETED_EXPRESS_MESH_REPORT_ZH.md` 的改进

| 方面 | 旧报告 | 新方案 |
|---|---|---|
| Mesh 段路由 | 旧的逐跳 `LegacyLocalAdaptive`，与 Express 段实现边界不统一 | 纯 Mesh 与 Express 路径中的普通 Mesh 段共用同一个 `LocalMeshAdaptive` primitive |
| Express 选择 | 途中局部重规划/启发式 | 注入时从最多 `K=8` 条完整候选中选择一次并 commit，途中不重规划 |
| 候选路径 | 主要依赖单一路径或局部候选 | 覆盖 0/1/2 条 directed express，始终包含纯 Mesh candidate，无 loop |
| 拥塞信息 | 主要看局部 output occupancy | `GlobalExpressReservation` 使用 `L(P)+Σ(q[e]+r[e])` |
| Reservation | 无明确 in-flight 预留语义 | 注入时 `r[e]++`，进入 express 时 `r[e]--`，转 Escape 时释放剩余预留 |
| 路由 baseline | deterministic/adaptive 对比不完全隔离 | 增加同一 candidate set 上的 `RandomCandidate` baseline |
| VC 对照 | Escape 语义未充分隔离 | Escape：3 adaptive + 1 XY escape；NoEscape：4 个 ordinary adaptive VC，硬件总数均为 4 |
| 测量 | 曾有 binary、latency 单位、seed/cache 问题 | 使用隔离 `gem5-lab4`、500 ticks/network-cycle、seed-aware cache 和独立 run tag |
| 负载范围 | 主要低到中负载 | 扩展到 `0.08...0.80`，明确观察到约 `0.50` 的 saturation knee |

这里的 `CutStress-aware Hybrid` 是 placement 名称：它只表示离线阶段用 CutStress demand matrix 选 express edges，不表示运行时路由器知道 traffic 类型。

## 3. 固定实验 setting

| 项目 | 设置 |
|---|---|
| 网络 | 8x8 Mesh，64 routers/nodes，Garnet standalone |
| 物理预算 | express wire budget `B=16`；`d_min=3`；每 router 额外 degree <= 1 |
| Express | latency=1 cycle；双向 edge 拆成 directed edge；每个 `(src,dst)` 最多 K=8 candidates，最多 2 条 express |
| 流量 | 主结果为 Uniform Random；补充 CutStress；traffic seeds=1,2,3。Random placement 另有独立 placement seeds=1,2,3 |
| 路由 | source-route；主策略 policy 2=`GlobalExpressReservation`；ablation policy 3=`RandomCandidate` |
| VC | Escape 模式总 4 VC/vnet，其中 3 adaptive + 1 XY escape；NoEscape 使用全部 4 个 adaptive VC |
| 窗口 | headline：warmup 20,000 + measurement 100,000 cycles；早期 coarse scan 使用 3,000 + 10,000 |
| 指标 | accepted throughput、平均 packet latency、max/P95 link utilization、express traversals；新版结果还记录 0/1/2-express packet bins、逐 edge selected count、reservation increments/decrements |

## 4. 实验与结果

### 4.1 正确性与机制验证

- 离线 candidate、selector、committed-route 测试共 `10/10` 通过。
- Mesh/Hybrid、Escape/NoEscape、0/1/2-express、multi-flit data-vnet smoke 均可运行。
- q/r 账目短测中 reservation increments/decrements 正常，underflow 和非法 express ID 有断言保护。
- 新增 packet-level 分桶已在短程 Hybrid q+r run 中验证：planned `0/1/2 express = 10740/1945/28`，delivered `0/1/2 express = 10972/1947/28`；逐 express edge selected count 也已输出到 JSON。
- Escape 统计也已做非零短测验证：Hybrid q+r、rate `0.50` 的 `10k` measurement 中，`19,971` 次 transition，平均 transition 前等待 `48.88` cycles，交付 packet 中 `15.31%` 曾进入 Escape；低负载时这些字段正确为零。
- 该短窗口的 reservation increment/decrement 差为 `-2`，对应 reset 时仍有在途 packet，说明当前统计窗口不能把最终 reservation current 或 increments/decrements 直接宣称为完整闭合账目；正式实验需增加 drain 或 outstanding-packet 校验。
- 历史固定失败区域扩大回归：Random/Hybrid、deterministic/adaptive、rate `0.016--0.020`、3 seeds，共 `60/60` 完成；这说明旧失败在当前 binary 上不是必然复现，但不等于已形式化证明 deadlock-free。

### 4.2 Uniform Random：进入饱和区的主结果

以下为 `20,000 + 100,000` cycles、3 seeds 的均值；policy 为 `GlobalExpressReservation`，source-route，Escape 开启。

| Topology | rate | accepted throughput | avg latency (cycles) | max util | P95 util |
|---|---:|---:|---:|---:|---:|
| Mesh | 0.40 | 0.1998 | 17.7 | 0.473 | 0.450 |
| Mesh | 0.50 | 0.1419 | 24,121.8 | 0.386 | 0.339 |
| Random | 0.50 | 0.1482 | 21,078.5 | 0.426 | 0.347 |
| Hybrid | 0.50 | 0.1436 | 21,491.0 | 0.388 | 0.351 |
| Mesh | 0.60 | 0.1418 | 31,532.3 | 0.390 | 0.347 |
| Random | 0.60 | 0.1502 | 29,312.6 | 0.419 | 0.358 |
| Hybrid | 0.60 | 0.1438 | 30,334.2 | 0.390 | 0.344 |

解释：`rate=0.40` 尚未进入明显排队区；约 `0.50` 开始 accepted throughput 饱和而 latency 爆升。因此不能用低负载的 latency 差异外推 peak throughput。当前 single representative Random placement 下，Random 的 near-knee throughput 略高于 Hybrid，但其 max utilization 也更高。

### 4.2.1 新增路径与 q/r 统计

schema v4 的 27 个 headline run 全部完成。交付 packet 的 express 数量（跨 topology/seed 合并）如下：

| Topology/rate | 0 express | 1 express | 2 express | q 平均峰值* | q 观测最大 | r 平均峰值* | r 观测最大 |
|---|---:|---:|---:|---:|---:|---:|---:|
| Random/0.40 | 84.56% | 15.43% | 0.00% | 0.552 | 2 | 2.712 | 6 |
| Random/0.50 | 93.20% | 6.80% | 0.00% | 0.165 | 2 | 3.979 | 6 |
| Hybrid/0.40 | 87.49% | 12.48% | 0.03% | 0.281 | 2 | 1.556 | 4 |
| Hybrid/0.50 | 93.25% | 6.74% | 0.01% | 0.096 | 2 | 2.258 | 4 |

`q` 是 express output queue 中等待 flit 数，`r` 是尚未到达 express tail 的 committed packet 数；“平均峰值”指每个 run 中各 edge sample average 的最大值，不是所有 edge 的总体平均。结果显示 near-knee 时 q 较低但 r 较高，说明主要压力来自 in-flight reservation，而不是 express queue 本身。

### 4.3 Reservation 与 RandomCandidate ablation

在相同 candidate set 上，`RandomCandidate` 只随机选候选，不读取 q/r。早期 `5,000 + 20,000` 窗口的 3 seeds 都完成，accepted throughput 约 `0.0761`，平均 latency 约 `8.1--8.4k` cycles，express traversals per delivered packet 约 `0.18`。

统一到 headline 的 `20,000 + 100,000` 窗口后，rate `0.50` 的 seed 1/3 跑完，accepted throughput 仍约 `0.0762`；seed 2 在 tick `47,643,000`，即约 `95,286` network cycles 时触发 `Possible network deadlock`。补充 rate `0.40` 后，seed 1/2 跑完但 seed 3 在约 `98,851` cycles 触发同类 panic。因此 RandomCandidate 目前不仅性能明显差于 q+r，而且有长窗口 correctness 失败，不能把成功 seeds 的均值当成正式三 seed 结果。

早期 q-only 与 q+r 对照显示，reservation 会减少 express traversals：Hybrid/Uniform、rate `0.16` 约减少 `6.1%`，Random/Uniform、rate `0.12` 约减少 `12.1%`；但 accepted throughput 没有稳定提升，latency 大致持平或略高。因此目前能证明的是缓解 herding 的机制效果，而不是性能胜出。

### 4.3.1 Random placement variance

Random topology 使用 placement seed `1,2,3`，每个 placement 再使用 traffic seed `1,2,3`；共 `9` 个样本/负载点，均为 q+r、source-route、Escape、`20k + 100k` cycles。

| rate | samples | accepted throughput mean | stdev | avg latency mean (cycles) | max util mean |
|---:|---:|---:|---:|---:|---:|
| 0.40 | 9 | 0.1999 | 0.0001 | 16.1 | 0.490 |
| 0.50 | 9 | 0.2077 | 0.0477 | 7,844.2 | 0.509 |
| 0.60 | 9 | 0.1461 | 0.0065 | 29,954.0 | 0.407 |

`rate=0.50` 的高标准差说明 placement variance 很大，单一 Random placement 的结果不能代表 Random 策略总体表现。汇总文件为 `express_mesh/results/phase3_measurement_v2/random_placement_results.json`。

### 4.4 Escape 对照

Hybrid 和 Mesh、policy q+r、rate `0.50`、3 seeds 的 NoEscape runs 全部触发 deadlock watchdog。NoEscape 并非少了硬件 VC：它同样有 4 个 VC/vnet，只是 4 个都作为 ordinary adaptive VC；Escape 模式则是 3 adaptive + 1 reserved XY escape VC。

| Topology | seed 1 no-progress cycle | seed 2 | seed 3 | termination |
|---|---:|---:|---:|---|
| Mesh | 50,245 | 50,229 | 50,235 | 3/3 `deadlock_panic` |
| Hybrid | 51,497 | 50,592 | 50,411 | 3/3 `deadlock_panic` |

这些 cycle 是从 gem5 panic 日志的实际 tick 按 500 ticks/cycle 换算，不再用预设模拟终点代替。该结果支持 Escape 对 near-knee progress 很重要，但 RandomCandidate 的失败也表明“有 Escape”本身并不足以保证所有 adaptive/source-route 组合正确。

Hybrid q+r 的 schema v5 正式 Escape 统计为：rate `0.40` 没有 transition；rate `0.50/0.60` 的 delivered escape fraction 分别约 `39.98%/40.09%`，平均 transition 前等待分别为 `49.34/49.36` cycles。后者大于配置的 32-cycle timeout，因为统计从 packet 注入开始，包含到达发生阻塞 router 前的传播时间。

同口径 schema v5 对照如下（Random 为 placement seed 1）：

| Topology | rate 0.50 escape fraction | rate 0.50 cycles before escape | rate 0.60 escape fraction | rate 0.60 cycles |
|---|---:|---:|---:|---:|
| Mesh | 41.77% | 50.07 | 41.85% | 50.03 |
| Random p1 | 39.05% | 48.79 | 38.93% | 48.89 |
| Hybrid | 39.98% | 49.34 | 40.09% | 49.36 |

三者在饱和区都有约 39--42% delivered packet 使用 Escape，说明当前 headline 性能很大程度依赖 escape 子网维持进展；Express topology 并未消除这一依赖。

Random 三 placement 全部升级到 schema v5 后，跨 `3 placement x 3 traffic seeds` 的 Escape fraction 在 rate `0.50/0.60` 分别为 `15.92%/39.60%`，平均 transition 前周期为 `44.47/48.85`。rate 0.50 的 placement variance 同样反映在 Escape 使用率上：部分 placement 尚未饱和，不能用 p1 的约 39% 代表所有 Random topology。

## 5. 与原计划的对应关系

已覆盖原计划中的：预算约束 topology、candidate generation、deterministic/source-route、global q+r、RandomCandidate baseline、Escape/NoEscape 对照和饱和区扫描。ASPL-Greedy、Bottleneck-Greedy 的离线 screening 结果仍保留在旧报告中。Random placement seeds=1,2,3 已生成、校验并完成 27 个 schema v5 高负载 case；后续 headline 应报告 placement 均值和标准差，而不能只引用 seed 1。

## 6. 当前可分享的结论与限制

可以分享的结论是：新方案在定义上比旧方案更完整、路径承诺和 reservation 账目可观测；高负载下确实存在 saturation knee；q+r 相比随机候选选择更稳定且性能更好；Escape 对保持进展很重要。

暂时不能声称：Hybrid 已稳定击败 Mesh、Reservation 已提高 peak throughput、或实现已经 deadlock-free。NoEscape、0/1/2-express 分桶、per-edge selected count、q/r average/max 和 3 个 Random placements 均已补齐；RandomCandidate 的长窗口仍有 watchdog failure。P95 packet latency 与 path stretch 属于 feedback1 的 optional-if-available 指标，当前 Garnet 没有 packet histogram，未在本轮扩展。
