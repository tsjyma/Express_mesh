# V4 增量实验：理想路径规划、可反悔布局与 Traffic-aware 布局

> **V5 纠错（2026-08-29）：本报告 4.3--5 节的 Garnet 负结论无效。**
> 原实验遗漏 `--xor-low-bit=0`，directory XOR hashing 把 deterministic
> traffic 的目的改写成近似全目的扫描。修复后的 100k × 3-seed Garnet
> 长测中，Bit-complement-aware/Tornado-aware ASPL 吞吐分别为
> 0.275805/0.379366，均显著胜过 Mesh 和 Random，且与 standalone 在 1%
> 左右吻合。根因、公式和新结果见
> `ADAPTIVE_ROUTING_V5_GARNET_AUDIT_ZH.md`。以下保留旧内容仅用于记录当时的
> 实验过程，旧 Garnet 表不得再作为 performance 结论引用。

## 0. 结论先行

本轮三个问题的答案分别是：

1. **K=8 不是当前主要瓶颈。** 把每对 router 的候选数增加到
   32/64/128/256，throughput 和 latency 都没有稳定改善。只感知 express
   拥塞的 per-packet Dijkstra 也不能全面胜过 K=8。真正明显的理论上界来自
   “知道所有 mesh/express link 压力”的 global Dijkstra：Uniform rate 0.8
   吞吐从 0.3514 提高到 0.3996，CutStress rate 0.8 从 0.1414 提高到
   0.1755。不过它依赖全局瞬时状态，只是 oracle，不是最终硬件方案。

2. **没有找到全面支配 stride 的 unrestricted 布局。** 可反悔的模拟退火
   能针对某一种 traffic 改善，但会明显损害另一种 traffic。说明 stride
   不是数学最优约束，却在当前 budget/degree 下起到了很强的 regularization
   作用；不能用单一 traffic 的短窗口结果宣称 unrestricted 更优。

3. **[已由 V5 作废] Traffic-aware 静态布局在 standalone 中看起来非常强，但 Garnet 长测
   不支持这个结论。** Bit-complement 和 Tornado 都是端点均衡的一一映射，
   不存在 hotspot 接收端瓶颈；然而在 100k-cycle Garnet 中，无论纯 ASPL
   还是加入静态最大负载项的 hybrid，都没有在有压力时胜过 uniform-ASPL、
   stride 或 random。20k-cycle 的漂亮结果是 packet 尚在持续堆积的
   transient，不是稳态优势。

因此，本轮最可靠的设计启示是：**下一步值得优化的是对 mesh 压力的可实现
近似，而不是继续增大静态 K，也不是只根据 traffic demand 静态摆边。**

## 1. 增量实现与不覆盖原则

原有 policy 0--4 和 V3 topology 文件均保留。新增内容包括：

- standalone policy 5：每个 packet 运行 Dijkstra；mesh 边权固定为 1，
  只有 express 边叠加 reservation 和 occupied-VC 压力；
- standalone policy 6：每个 packet 在所有 mesh/express 有向物理边上运行
  Dijkstra，所有边都叠加 reservation 和 occupied-VC 压力；
- `--source-route-candidates` 上限扩展到 512，用于 K sweep；
- 独立的 `search_placement_sa.py`，通过删除 1--3 条旧边再重新填充，使早期
  决策能够反悔，候选空间不限制 axis、stride 或固定长度；
- 新增 Bit-complement 与 Tornado traffic，以及 `bitcomp_*`、`tornado_*`
  traffic-aware placement；
- 新增独立 direct-pair baseline 生成器，不修改原 `placement.py` 的已有算法；
- 新增 V4 聚合脚本、CSV/JSON 和 SVG。

审计时还发现 standalone 的同延迟 candidate tie-break 少了 gem5 中的
“express 条数较少者优先”。现已修正为
`(latency, express_count, express_ids)`。修正前后 Stride-ASPL 的 18 个 K=8
验证 run 数值完全相同，Bit-complement 的代表 run 也相同；因此它没有改变
下文数值，但保证以后与 `ExpressMesh.py` 的候选排序一致。

## 2. 尝试一：K 扩展和 Dijkstra 理论上界

### 2.1 增大候选数

配置为 Stride-ASPL、budget 64、max degree 1、policy 4、
reservation/VC weights = 0.375/0.625。下表为 seed 1、warmup 5k、measurement
30k 的 rate 0.8 结果：

| K | Uniform throughput | Uniform latency | CutStress throughput | CutStress latency |
|---:|---:|---:|---:|---:|
| 8 | 0.35208 | 2082 | 0.14143 | 3703 |
| 32 | 0.35052 | 2153 | 0.14143 | 3703 |
| 64 | 0.35161 | 2099 | 0.14143 | 3703 |
| 128 | 0.35161 | 2099 | 0.14143 | 3703 |
| 256 | 0.35161 | 2099 | 0.14143 | 3703 |

K=8 已包含真正有竞争力的低静态代价路线。继续加入的路线大多更长，local
express pressure 并没有足够信息判断这些 detour 能否缓解后续 mesh 拥塞，
所以“更多候选”本身没有价值。

### 2.2 三种规划方式的 3-seed 长测

均为 warmup 5k、measurement 30k。Dijkstra-express 使用
weights 0.125/0.25；Dijkstra-global 使用 1.5/0.5。

| Traffic/rate | K=8 policy 4 | Dijkstra-express | Dijkstra-global oracle |
|---|---:|---:|---:|
| Uniform 0.5 throughput / latency | 0.24966 / 13.2 | 0.24966 / 13.0 | 0.24966 / 13.1 |
| Uniform 0.7 throughput / latency | 0.34948 / 16.3 | 0.34949 / 15.4 | 0.34949 / 13.8 |
| Uniform 0.8 throughput / latency | 0.35141 / 2136 | 0.33403 / 2962 | **0.39961 / 14.8** |
| CutStress 0.5 throughput / latency | 0.11855 / 712 | 0.12438 / 62 | **0.12479 / 12.7** |
| CutStress 0.7 throughput / latency | 0.13855 / 2475 | 0.14992 / 1731 | **0.16693 / 574** |
| CutStress 0.8 throughput / latency | 0.14137 / 3693 | 0.15678 / 2757 | **0.17547 / 1479** |

rate 0.8 时，global oracle 相对 K=8：

- Uniform throughput **+13.7%**，latency **-99.3%**；
- CutStress throughput **+24.1%**，latency **-60.0%**。

Dijkstra-express 在 CutStress 有帮助，却使 Uniform 饱和吞吐下降 4.9%。它只
知道 express 是否堵塞，不知道到达/离开 express 的 mesh segment 是否已经
堵塞，因此仍会把 packet 导向错误的 mesh 区域。Global oracle 的提升说明
真正缺失的信息是 **mesh pressure**。

policy 6 不能直接落地：它假设每个 NI 瞬时知道全网每条 link 的 VC occupancy
和预订数，还要逐 packet 跑 Dijkstra。更合理的下一步是从它的决策中提炼
低成本近似，例如分区拥塞摘要、少数路径 probe，或对进入/离开 express 的
mesh corridor 维护低比特压力估计。

## 3. 尝试二：无 stride 的可反悔布局

SA mutation 每次移除 1--3 条边，然后从所有 Manhattan length >= 3 的合法边
中重新填满 budget；budget=64、max degree=1。搜索使用 standalone policy 4，
最后用 3 seeds、5k+30k 验证。

| 布局 | Uniform 0.8 th / lat | CutStress 0.8 th / lat |
|---|---:|---:|
| Stride-ASPL | 0.35141 / 2136 | 0.14137 / 3693 |
| Uniform-only SA | **0.35281 / 2001** | 0.12998 / 4723 |
| Uniform+Cut balanced SA | 0.27934 / 3439 | **0.16850 / 1845** |

Uniform-only SA 只带来约 0.4% throughput 提升，却让 CutStress throughput 下降
约 8.1%。Balanced SA 把 CutStress throughput 提高约 19.2%，但 Uniform
下降约 20.5%。它们证明 unrestricted 搜索可以找到不同 Pareto 点，却没有
找到同时支配 stride 的点。

还观察到明显的短窗口 overfitting：balanced SA 搜索窗口中曾显示 Uniform
约 0.330、CutStress 约 0.169，看起来是不错的折中；30k 验证后 Uniform 只剩
0.279。原因是短窗口结束时 packet 仍在持续排队，accepted throughput 尚未
下降到长期服务能力。

所以对 stride 的结论应是：

- 它不是必要的物理/数学约束；
- 本轮没有证明它是全局最优；
- 但在 Uniform/CutStress 两类 traffic 间，它比当前 unrestricted SA 更稳健，
  并能减少搜索对短窗口噪声和单一 traffic 的过拟合。

## 4. 尝试三：Traffic-aware placement

### 4.1 Traffic 定义

为了排除 hotspot 接收端瓶颈，本轮选择两个标准、端点完全均衡的 permutation
traffic。每个 router 恰好有一个源和一个目的：

- **Bit-complement**：`(x,y) -> (7-x,7-y)`；
- **Tornado**：`(x,y) -> ((x+3) mod 8,y)`。

为每种 traffic 分别生成：纯 demand-weighted ASPL greedy、ASPL/Lmax hybrid，
以及只连接高收益源宿对的 direct baseline。所有布局仍满足 budget 64、
max degree 1、minimum length 3。

### 4.2 Standalone 探索结果：非常乐观

5k+30k、3 seeds、policy 4、rate 0.8：

| Traffic | Mesh | Random | Uniform-ASPL | Stride-ASPL | Matched traffic-aware ASPL |
|---|---:|---:|---:|---:|---:|
| Bit-complement throughput | 0.12800 | 0.17459 | 0.18517 | 0.20178 | **0.27372** |
| Bit-complement latency | 11373 | 9664 | 9362 | 7449 | **4985** |
| Tornado throughput | 0.21018 | 0.22238 | 0.27750 | 0.27164 | **0.38061** |
| Tornado latency | 6846 | 6063 | 3559 | 4135 | **698** |

而且“用错 traffic 的布局”明显变差：rate 0.8 时 Bit-complement 布局跑 Tornado
只有 0.2268，Tornado 布局跑 Bit-complement 只有 0.1890。这说明 standalone
中的提升确实来自 demand/topology 匹配，不是统一增加了链路资源。

### 4.3 [已作废] Garnet 长测：否定高压 winner

最终结论以 Garnet 为准。配置为 warmup 20k、measurement 100k、3 seeds，
原 policy 4 weights 0.375/0.625，escape 开启。

rate 0.8：

| Traffic/topology | Throughput mean | Latency mean |
|---|---:|---:|
| Bit-complement Mesh | 0.20434 | 35856 |
| Bit-complement Random | **0.22541** | **32004** |
| Bit-complement Uniform-ASPL | 0.19759 | 33289 |
| Bit-complement Stride-ASPL | 0.21349 | 32649 |
| Bit-complement-aware ASPL | 0.16709 | 44338 |
| Tornado Mesh | 0.19901 | 32624 |
| Tornado Random | 0.22665 | 28330 |
| Tornado Uniform-ASPL | **0.23342** | **26207** |
| Tornado Stride-ASPL | 0.23085 | 26800 |
| Tornado-aware ASPL | 0.19328 | 30004 |
| Tornado-aware hybrid | 0.20048 | 29591 |

Tornado rate 0.5 的结果也相同：Mesh/Random/Uniform-ASPL/Stride-ASPL/
aware-ASPL/aware-hybrid 分别为 0.1950/0.2257/0.2301/0.2371/0.2123/0.2145。

这里最关键的现象是测量长度：Tornado-hybrid 在 20k 窗口中曾达到 rate 0.8
throughput 0.2935，但 100k 后只有约 0.2005。即
`accepted throughput < offered rate/2 = 0.4`，源队列不断增长，短测只截取了
未完全堆积的 transient。

失败原因不是 traffic 端点集中，而是当前组合的两个结构问题：

1. 静态 ASPL/Lmax placement 不等价于动态多商品流容量优化；多条 demand 会
   共享相同 mesh corridor、express endpoint 或 router switch；
2. policy 4 只对 express 边计 pressure。express 开始拥塞后，大量 packet
   回退到 mesh，但算法不知道这些 mesh corridor 也已堵塞。这正好与 global
   Dijkstra oracle 的正面结果相呼应。

因此本轮**没有找到一个经 Garnet 长测验证、在有压力时明显胜过通用布局的
traffic-aware express placement**。这是负结果，不应选择 standalone 中最漂亮
的曲线作为论文结论。

### 4.4 权重与 watchdog 诊断

为排除“只是 Uniform-tuned weights 不合适”，又在 Garnet 上筛选了更低的
reservation/VC weights。短窗口偶尔改善，但长窗口没有产生 winner。

Bit-complement-aware、weights 0.25/0.5 的 100k run 中，seed 1/2 触发了 Garnet
NI 的 `Possible network deadlock` watchdog，seed 3 正常结束。这只能证明某个
NI 连续 50k cycles 没拿到注入 VC；NI watchdog 也会把严重注入饥饿报告成
possible deadlock，**不能单独证明全网 channel deadlock**。这组配置因此既不
安全也不用于 performance 均值。若要把它升级成严格 deadlock 反例，需要像
standalone diagnostics 一样继续运行、停止注入并 drain，同时检查全网 flit/
delivery 是否完全停止。

## 5. [已作废] 对 standalone 准确性的补充边界

standalone 在 V3 的 Uniform/CutStress 设计筛选上仍很有用，但新的 deterministic
permutation traffic 暴露出它对高压饱和点过于乐观：例如 Tornado-aware ASPL
在 standalone rate 0.8 为 0.3806，而 Garnet 只有 0.1933。候选表、traffic
mapping、VC 数和 buffer depth 已核对一致，差异来自被简化的 Garnet NI/router
时序、仲裁和拥塞反馈组合。

因此建议今后采用两级流程：

1. standalone 只用于大量排除和构造 oracle upper bound；
2. 任何“更优”结论必须经过 Garnet 长窗口，且确认 throughput 不随窗口增长
   持续下降、源队列不持续增长。

## 6. 产物位置

- 路由/traffic/placement 增量实现：`express_noc.cpp`、
  `search_placement_sa.py`、`generate_traffic_aware_placements.py`；
- traffic-aware topology：`results/design_space/traffic_aware_b64_d1/`；
- standalone 原始 V4 结果：`standalone_noc/results/ideal_path/`、
  `standalone_noc/results/placement_search*`、
  `standalone_noc/results/traffic_aware/`；
- Garnet 原始结果：`results/garnet_v4_final/`、
  `results/garnet_v4_final_r05/`；
- 汇总 CSV/JSON/SVG：`standalone_noc/results/v4_summary/`。

两张主要图为：

- `ideal_routing_curves.svg`：K=8、express-only Dijkstra、global oracle；
- `traffic_aware_curves.svg`：standalone traffic-aware 探索曲线。该图必须结合
  本报告的 Garnet 反证阅读，不能单独作为最终 performance claim。
