# Express-Mesh V3：预算、拓扑与路由联合优化结果

## 结论

在 8x8 mesh、单 flit 控制包、4 VCs、理想 express latency=1 的当前实验设定下，
目前找到的最佳均衡设计是：

- 总 express 线长预算从 16 增加到 64；
- 每个 router 的 express degree 仍限制为 1；
- 只允许同行或同列、Manhattan 长度恰好为 4 的候选边；
- 用 Uniform demand 的 ASPL 边际收益/线长做离线 Greedy，选出 16 条边；
- 每个源—目的保留 8 条包含 0/1/2 个 express link 的候选路径；
- 注入时选择
  `static_latency + 0.375 * reservations + 0.625 * occupied_express_VCs`；
- 选中后 mesh 段使用 XY；VC3 是只走 base mesh XY 的独立 escape VC。

这个方案以下称为 **Stride-ASPL Greedy**。它不需要运行时 Dijkstra、全 mesh
链路状态或多商品流求解。运行时只需传播 16 条 express link 的小整数
reservation/VC-pressure 摘要；可以用低频 congestion beacon 近似实现。

最终 Garnet 的 36 个运行（20k warmup + 100k measurement，3 seeds）全部正常
结束，0 deadlock/no-progress。

## 最终 Garnet 结果

吞吐单位是 accepted packets/router/network-cycle；延迟单位是 network cycles。

| Traffic / rate | Mesh XY | Stride Random | Stride-ASPL Greedy | Greedy vs Mesh | Greedy vs Random |
|---|---:|---:|---:|---:|---:|
| Uniform / 0.70 | 0.265936 | 0.334073 | **0.349648** | **+31.48%** | **+4.66%** |
| Uniform / 0.80 | 0.266222 | 0.320258 | **0.353579** | **+32.81%** | **+10.40%** |
| CutStress / 0.50 | 0.119062 | 0.124560 | **0.124824** | **+4.84%** | **+0.21%** |
| CutStress / 0.80 | 0.138471 | 0.166333 | **0.174835** | **+26.26%** | **+5.11%** |

CutStress 0.50 尚未充分拉开吞吐，但延迟已经明显区分：Greedy 53.8、Random
254.0、Mesh 3224.9 cycles。其余高压点的平均延迟如下：

| Traffic / rate | Mesh XY | Stride Random | Stride-ASPL Greedy |
|---|---:|---:|---:|
| Uniform / 0.70 | 12737.7 | 2733.0 | **16.4** |
| Uniform / 0.80 | 17928.7 | 12758.6 | **7278.6** |
| CutStress / 0.80 | 18792.9 | 10938.3 | **8669.2** |

Uniform 0.70 是最能体现设计价值的点：Mesh 和 Random 已进入持续积压区，
Stride-ASPL 仍接受几乎全部 offered traffic，延迟保持在 16 cycles 左右。高于
饱和点后，吞吐是有意义的平台指标；延迟会随测量窗口增长，不能解释成稳态延迟。

完整 Garnet 汇总在
`results/garnet_b64_final/aggregate.{json,csv}`。

## Standalone 完整曲线

Standalone 对 Mesh、代表性 Stride-Random、原 ASPL Greedy 和新 Stride-ASPL
Greedy 跑了 216 个有效长测：Uniform 10 个 rate、CutStress 8 个 rate、每点 3
seeds。所有这 216 个运行均未触发 no-progress。四联图为：

`standalone_noc/results/final_b64_summary/throughput_latency_curves.svg`

关键均值：

| Traffic / rate | Mesh | Stride Random | 原 ASPL | Stride-ASPL |
|---|---:|---:|---:|---:|
| Uniform / 0.70 | 0.265671 | 0.333666 | 0.342911 | **0.349670** |
| Uniform / 0.80 | 0.266229 | 0.326697 | 0.338796 | **0.339596** |
| CutStress / 0.50 | 0.093750 | 0.116491 | 0.115182 | **0.118610** |
| CutStress / 0.80 | 0.093750 | 0.139267 | 0.136771 | **0.141407** |

Standalone 和 Garnet 的绝对 CutStress 饱和值不同，但拓扑排序和关键设计判断
一致。Garnet smoke test 的 Uniform 0.70 数值为 0.34883/16.19，Standalone 长测
为 0.34967/16.3，说明最终配置的快速模型在该关键点上吻合良好。

## 为什么旧设计只提高几个百分点

最主要限制是 budget=16。单种子短测中，ASPL-Greedy 的 Uniform 饱和吞吐随
预算增加约为：

| Wire budget | 24 | 32 | 48 | 64 |
|---|---:|---:|---:|---:|
| ASPL throughput | 0.291 | 0.298 | 0.312 | 0.329 |

旧 budget=16 在 Uniform 0.8 只有约 0.097 次 express traversal/packet。budget=64
的 Stride-ASPL 长测在 rate 0.1/0.7/0.8 分别为 0.821/0.531/0.426。高压下使用率
下降仍是正常的自适应节流，但已经不再是“几乎不用 express”。

另一个限制是旧 VC-pressure 权重 1.0 偏大。将其调到 0.625 后，允许更多尚未
拥塞的 express 路径被选中；reservation 仍提前抑制即将形成的热点，因此没有
退化成 static/Dijkstra 的盲目 express 使用。

## 尝试过但未采用的方案

1. **更多候选路径。** K=8/16/32/64 中，K=8 最好或持平。更长的候选在拥塞时
   会成为不必要的 detour；CutStress 对 K 完全不敏感。
2. **Static shortest path / Dijkstra。** 它们不看 express capacity。Uniform
   0.8 时 express 使用仍约 0.24 次/packet，但吞吐只有约 0.135，约为最终策略
   的一半；更多 express traversal 并不等于更高性能。
3. **任意斜向 Greedy。** 静态 graph metric 会选出对最短路好、对真实 XY 段
   和端口竞争不好的斜边。限制为易布线的轴向 stride-4 后，性能反而更高。
4. **Robust mixed-demand Greedy。** 50/50 版本 Uniform 很好，但 CutStress
   较弱；偏向 CutStress 又显著损失 Uniform，不如 Stride-ASPL 均衡。
5. **degree=2。** 固定 budget=64 时没有收益，因为边更容易集中到少量 router
   端口；degree=1 更快或持平，也更容易实现。
6. **Axis-Hybrid。** CutStress standalone 可达约 0.168，但 Uniform 高压吞吐
   下降，并出现 5 次 NI 长期 busy watchdog。触发时 flit 和 delivery 仍每周期
   前进，所以不是 routing deadlock，而是严重不公平/注入停滞；该偏科方案被排除。
7. **Mesh adaptive baselines。** XY/Monotonic-XY 是当前模型最强的 common Mesh
   baseline。Odd-Even、West-First、DOR-adaptive 在 Uniform 高压下更差；
   CutStress 与 XY 持平。因此最终表使用 Mesh XY，而非较弱基线。

## Greedy 与 Random 应如何表述

公平比较必须让 Random 和 Greedy 使用相同候选空间。因此 `Stride Random` 也只
能随机选择轴向长度 4 的边，并满足相同 budget=64、degree=1 约束。

20 个独立 Stride-Random 拓扑的短测中：

- Uniform 0.8：均值 0.31962，标准差 0.03692；
- CutStress 0.8：均值 0.13685，标准差 0.01501；
- Greedy 的对应两种子局部验证约为 0.35150/0.14140。

因此“Greedy 平均优于 Random”成立，最终代表性 Garnet 点也全部保持
Greedy > Random > Mesh。但不能宣称 Greedy 胜过每一个 Random 抽样：部分随机
拓扑会偶然偏向 CutStress，代价是 Uniform 较差。当前 Greedy 的主要优点是单一
拓扑在两种 traffic 上都稳定，而不是对所有目标的全局最优证明。

## 最终 16 条 express links

坐标为 `(x,y)`，每条 wire length 都是 4：

```
(0,0)-(0,4)  (4,0)-(4,4)  (1,1)-(5,1)  (2,1)-(2,5)
(3,1)-(7,1)  (6,1)-(6,5)  (1,2)-(1,6)  (2,2)-(6,2)
(3,2)-(3,6)  (7,2)-(7,6)  (0,3)-(4,3)  (5,3)-(5,7)
(1,4)-(5,4)  (3,5)-(7,5)  (0,6)-(4,6)  (2,6)-(6,6)
```

## Deadlock 结论

本轮没有改变 V2 的严格 escape 结构：普通 VC0--VC2 可单向转入 VC3，VC3 只走
base-mesh XY，且绝不回到普通 VC 或 express link。escape channel dependency
graph 无环，普通通道中的闭环依赖最终可退出到 escape 子网。增加 budget 和改变
placement/权重不改变该证明。

该结论是 routing/protocol deadlock-free，不是“无限过载时每个源都有有界延迟”。
NI 长期拿不到普通 VC 是 starvation/过载诊断，必须结合全局 flit/delivery progress
判断，不能单独当成 deadlock。
