# Express-Mesh 项目设计与实验结果汇总

本文汇总截至 2026-08-29 已完成的 Express-Mesh 拓扑、路由和实验工作，目标读者是了解项目总体目标、但没有参与具体实现和调参的队友。项目研究的问题是：在二维 mesh 上增加受物理预算限制的长距离 express link，并使用可实现的自适应路由，能否在不牺牲 deadlock freedom 的前提下提高高压流量下的吞吐、降低延迟。实验采用两级方法：`standalone_noc/express_noc.cpp` 用来快速搜索和解释机制，完整 gem5 Garnet 用来确认最终结果。两者在 V5 修复地址映射后，对完全相同的 Phase-3 配置已经达到吞吐误差小于 1%、延迟误差小于 2%；但 standalone 只复现 vnet 0 的单 flit 控制包，不代表完整 Ruby 协议或多 flit 数据包。

需要先强调一个数据口径：旧的 Garnet deterministic-traffic 实验曾遗漏 `--xor-low-bit=0`。Ruby 默认的 directory XOR hashing 会把 tester 指定的目的 router 改写，因此旧的 Bit-complement、Tornado、CutStress，以及任何依赖固定目的集合的 Hotspot Garnet 数字都不能作为相应 traffic 的性能证据。Uniform-random 的分布在这种异或下仍近似均匀，所以旧 Uniform 结果仍可参考。当前 runner 已关闭该 hashing，并会检查 Bit-complement、Tornado 和 CutStress 的实际 source-destination 矩阵；本汇总在性能表中将有效结果和历史作废结果明确分开。

## 1. 布线的 config（限制）

基础网络是一个 `8×8` 的二维 mesh，共 64 个 router。每对水平或垂直相邻 router 之间有双向 base-mesh channel，普通 mesh link 和 router 的延迟均为 1 network cycle。额外加入的每一条 express link 在拓扑文件中表示一条双向物理连接；当前主要实验采用 ideal latency，即无论其几何长度是多少，express traversal 都按 1 cycle 计算。这是为了先分离拓扑和路由本身的收益，不能理解为真实长线没有时延代价。代码也支持 length-aware 模型，例如将长线延迟近似为 `ceil(Manhattan length / 4)`，但还没有用它完成与 ideal 模型同等规模的最终 Garnet 曲线。

所有方案都受三个主要物理约束。第一，总 wire budget 按所有无向 express link 的 Manhattan 长度之和计算，最初使用 16，之后系统地测试 24、32、48 和 64，当前主配置为 64。第二，每个 router 可连接的 express link 数量受 `max_degree` 限制，测试过 1 和 2；在固定 budget 64 下，degree 2 没有稳定收益，还会使端口和流量更集中，因此主配置保持 degree 1。第三，候选长线通常要求 Manhattan 长度至少为 3，避免把预算花在与 base mesh 功能相近的短边上。每个无向 express link 会形成两个有向通道，但 wire budget 只计一次物理线长。

候选边空间先后采用过三种口径。`all` 允许任意两点间满足最短长度限制的边，包括斜向边；`axis` 只允许同行或同列；`stride4` 进一步只允许同行或同列且 Manhattan 长度恰好为 4。V3 的通用 winner 使用 `budget=64、max_degree=1、stride4、ideal latency`，因此最终放置 16 条长度为 4 的边。这个 stride 约束不是 deadlock 或物理实现所必需，也没有被证明为数学最优；它是当前搜索中的 regularization：限制斜向和长短混杂的边后，静态图指标更贴合实际的 XY mesh segment，且在 Uniform 与 CutStress 之间比已测试的 unrestricted 搜索更稳健。公平比较 Random 与 Greedy 时，两者必须使用完全相同的 budget、degree、最短长度和候选空间；例如 V3 的 Random baseline 也是 Stride-Random，而不是从更宽或更窄的空间中抽样。

traffic-aware 的 V5 布局没有强制 stride，而是在 `all、budget=64、max_degree=1、minimum length=3、ideal latency` 下按目标 demand 选边。Bit-complement-aware ASPL 最终用了 10 条边并耗尽 64 的 wire budget，Tornado-aware ASPL 用了 18 条边并耗尽同样预算。它们说明固定预算并不等于固定边数：长边多时边数少，较短但仍属 express 的边多时边数可以更多。以后比较面积、端口数或功耗时，不能只报告 wire budget，还应同时报告边数、各 router 端口数和真实 repeater/router 成本。

## 2. 布线算法（包括 baseline、Greedy、SA 和其他尝试）

最基本的 baseline 是纯 Mesh，即不增加任何 express link；它给出新增长线前的性能下界。Random placement 在合法候选中随机排列并依次加入仍满足剩余预算和 degree 限制的边，生成时会尽量把预算填满。Random 的意义不是提供一个确定的弱对手，而是反映“只增加相同资源、但不优化连接位置”的分布，因此结论应写成 Greedy 平均优于 Random，而不能写成 Greedy 胜过每一个随机样本。V2 对 200 个合法 Random 拓扑的短测中，Uniform 0.8 吞吐均值为 0.27473、95 分位为 0.28013、最大值为 0.28399，同窗口 ASPL-Greedy 约为 0.28222；Greedy 胜过 98.5% 的样本，但确有少数 Random 更高。V3 对 20 个 Stride-Random 拓扑的短测也观察到明显方差：Uniform 0.8 的均值和标准差为 `0.31962±0.03692`，CutStress 0.8 为 `0.13685±0.01501`。

Handcrafted baseline 使用规则化的中心长线，通常交替放置横向和纵向、长度为 4 的连接。它的用途是判断复杂算法的收益是否只是来自“均匀铺一些长线”。原始 ASPL-Greedy 从所有合法边开始，每一步临时加入一条边，重新计算 demand-weighted shortest-path 指标，并选择“目标函数下降量除以 wire length”最大的合法边，直到没有正收益或预算无法继续使用。ASPL 目标是需求加权平均最短路径长度；Bottleneck 目标使用静态最短路负载中的最大边负载 `Lmax`；Hybrid 则对相对于纯 mesh 归一化后的 ASPL 与 `Lmax` 做加权和。Uniform-ASPL 用均匀全对需求，traffic-aware ASPL 则直接使用目标 traffic 的 source-destination demand。需要注意，这里的 shortest path 和 `Lmax` 都是离线静态图指标，不模拟 VC、router arbitration、endpoint 带宽或运行时拥塞，因此降低 ASPL 不等价于最大化饱和吞吐。

V3 最终采用的 Stride-ASPL Greedy 与原 ASPL-Greedy 的计算方式相同，只把候选空间限制为轴向 length-4。它选出的 16 条边为：`(0,0)-(0,4)`、`(4,0)-(4,4)`、`(1,1)-(5,1)`、`(2,1)-(2,5)`、`(3,1)-(7,1)`、`(6,1)-(6,5)`、`(1,2)-(1,6)`、`(2,2)-(6,2)`、`(3,2)-(3,6)`、`(7,2)-(7,6)`、`(0,3)-(4,3)`、`(5,3)-(5,7)`、`(1,4)-(5,4)`、`(3,5)-(7,5)`、`(0,6)-(4,6)`、`(2,6)-(6,6)`。这个方案的优点是单一拓扑在 Uniform 和 standalone CutStress 上都较稳定，而不是针对某一个源宿矩阵做到最优。

为了允许早期 Greedy 决策“反悔”，V4 增量实现了 simulation-guided simulated annealing（SA）。SA 从已有布局开始，每次随机删除 1 至 3 条边，再从所有 Manhattan length 至少为 3 的合法边中重新填充到接近 budget 64，然后用 Metropolis 概率接受更差候选，以便跳出局部最优。评价函数直接运行 standalone 的 policy 4，将每种 traffic 的 accepted throughput 除以其 offered capacity，再减去一个较小的对数 latency penalty；可选择一个主 traffic 和一个 secondary traffic。因此 SA 在离线搜索阶段可以是 traffic-aware 的，但运行时仍然不知道 traffic 类型，也不改变 routing。Uniform-only SA 在 Uniform 0.8 上只比 Stride-ASPL 高约 0.4%，却在 CutStress 上低约 8.1%；Uniform+Cut balanced SA 在 CutStress 上高约 19.2%，但 Uniform 上低约 20.5%。这表明 SA 找到了不同 Pareto 点，却没有找到同时支配 Stride-ASPL 的布局。其原因不只是早期 Garnet 地址 bug：SA 的主要实验在 standalone 的 Uniform/CutStress 上完成，不经过 Ruby 地址映射。更深层原因是 budget、degree、Uniform 容量和 central-cut 容量本身存在真实冲突，同时搜索窗口短、只用一个 traffic seed，容易把 transient backlog 和随机仲裁噪声当成稳态收益；当前“删少量边再随机填充”的局部 mutation 也没有全局最优保证。

其他已尝试的放置方法包括 unrestricted 斜向 ASPL、axis-only ASPL、Bottleneck、不同 ASPL/Lmax 权重的 Hybrid、Uniform/CutStress 混合 demand 的 robust Greedy、degree 2、不同 wire budget、traffic-aware direct-pair、traffic-aware ASPL/Hybrid，以及 unrestricted SA 的不同初始拓扑和目标组合。任意斜向 Greedy 常选出静态距离很好、却会把实际 XY ingress/egress traffic 汇聚到少数 corridor 的边；Axis-Hybrid 可显著增强 CutStress，却损害 Uniform，并出现严重 NI injection starvation；degree 2 没有稳定收益；Direct-pair 能服务一部分固定需求，但容易过早耗尽 degree 和 wire budget；traffic-aware ASPL 在与目标 traffic 匹配时非常强，但专用性明显，用错 traffic 时会显著退化。因此目前有两个不同用途的候选：Stride-ASPL 是偏通用、较稳健的布局，matched traffic-aware ASPL 是已知或高度可预测 traffic 下的专用布局，不能把后者的 matched 结果描述成对任意 traffic 的普遍胜利。

## 3. Routing 算法、候选路径和 escape 机制

纯 Mesh 的主要 baseline 是 deterministic XY：先沿 X 维走到目标列，再沿 Y 维走到目标行。它的 channel dependency graph 天然无环，逻辑简单，在当前高压测试中也是最强或最稳健的 common Mesh baseline。还测试过 minimal per-hop adaptive、West-first、Odd-Even、segment-level adaptive XY/YX、monotonic XY、DOR-adaptive 和 phase-VC 等方案。Minimal adaptive 每一跳都选择仍能让 Manhattan 距离减一的较空方向；“每步距离减一”只保证不会绕路，并不保证不同 packet 形成的 channel dependency 无环，也不保证负载均衡。实验中这类方案会频繁改变方向、形成循环等待压力，并把大量 packet 推入 escape；West-first、Odd-Even 和 adaptive XY/YX 在 Uniform 高压下普遍早于 XY 饱和。严格 phase-VC partition 可以消除一部分依赖，但固定分区损失了普通 VC 容量，也没有成为 winner。

Express-Mesh 的主路由是 injection-time adaptive、之后 committed 的 source routing。对每个 source-destination，离线枚举所有 loop-free 完整路径，其中可含 0、1 或 2 条有向 express link，express 之间和两端的 base-mesh segment 均使用 XY。候选按 `(static_latency, express_count, express_ids)` 排序，即先选静态延迟最短，再偏好 express 条数更少，最后用固定 edge id 保证确定性，然后保留前 K 条。当前 K=8 的八条不是随机路线，而是这个排序下的 top 8。packet 在 NI 注入时根据当前压力选择其中一条并承诺执行，中途不反复改路；因此它仍然是 adaptive routing，只是自适应发生在 packet 注入时，而不是每一跳。

主策略 policy 4 的代价为 `static_latency(P) + 0.375 × Σ reservations[e] + 0.625 × Σ occupied_express_VCs[e]`。`reservations[e]` 统计已经选择某条 express edge、但尚未真正穿过它的 packet 数，相当于预告即将到来的排队；`occupied_express_VCs[e]` 是该 express 输出端口上三个普通 VC 中已被占用的数量。packet 进入相应 express link 时释放该 edge 的 reservation；如果提前转入 escape，则释放尚未使用的所有 reservation。V2 的旧权重是 0.5/1.0，V3 调到 0.375/0.625 后减少了对 express 的过度回避。早期 policy 0 只选静态最短路径，policy 1 使用 staging queue `q`，policy 2 使用 `q+r`，policy 3 随机选候选，policy 4 才是 reservation 加真实 express output VC occupancy。这里的 `q` 只代表某个本地 staging/等待量，并不等于当前 express link 及其下游 corridor 的完整阻塞程度；这也是早期 `q+r` 机制容易误判的原因。额外统计 switch-arbiter waiter 没有得到可重复提升，未进入 Garnet 主实现。

增大 K 的实验表明候选数量不是当前瓶颈。K=8 已经包含低静态代价且真正有竞争力的路径，K=32 至 256 新增的主要是更长 detour；由于 policy 4 只知道 express edge 的 reservation 和 VC occupancy，不知道这些 detour 会进入的 mesh corridor 是否拥堵，所以无法可靠判断“多走几跳是否值得”。为估计理论空间，standalone 又实现了两种逐 packet Dijkstra。Policy 5 把所有 mesh edge 权重固定为 1，仅在 express edge 上叠加动态 reservation/VC pressure；它在 CutStress 上有帮助，但在 Uniform 0.8 反而降低吞吐，说明只更全面地搜索 express 状态仍不够。Policy 6 给每条 mesh 和 express 有向边都叠加瞬时压力，是全局状态 oracle；它在两个 traffic 上都显著优于 K=8，但要求每个 NI 瞬时知道全网所有 link 状态并逐 packet 跑最短路，不是合理的最终 NoC 实现。它的作用是定位缺失信息：当前主要缺的是 mesh corridor pressure，而不是更多静态候选。

Deadlock freedom 由独立 escape subnetwork 提供。每个 vnet 有 4 个 VC，VC0--VC2 是普通 adaptive/source-routed VC，VC3 专门用于 escape。一个普通 head flit 在当前输入位置连续阻塞 32 cycles 后，可以不可逆地从普通 VC 转到 VC3；转入后只沿 base mesh 的 deterministic XY 路由，永不使用 express link，也永不返回普通 VC。由于 XY escape channel dependency graph 无环、escape 不再依赖普通 channel、普通 packet 最终可以请求 escape successor，再加上 router 的 round-robin 仲裁公平且目的端持续消费，普通 channel 中的依赖环不能永久封闭，包含 escape 的依赖环也不可能形成。这一证明允许普通候选路径的并集本身有环，因此保留了 adaptive 性。48 组诊断中的 36 个 escape workload 都保持全局进展并可在停止注入后排空；V2 的有限注入 drain test 也做到网络为空且 reservation 全部归零。

这个证明有明确边界：它证明当前路由/协议配置不会发生永久 channel deadlock，不保证无限过载下每个 NI 都有有界等待时间。Garnet 的 `Possible network deadlock` watchdog 实际检查的是单个 NI 长时间拿不到注入 VC；在全网仍有 flit 移动和 packet delivery 时，它反映的是 injection starvation 或极端不公平，不能单独作为 channel deadlock 证据。当前正式 matched V5 的 18 个 Mesh-XY、Random 和 traffic-aware ASPL runs 均正常完成。另有一个已经修复的 NoEscape off-by-one：关闭 escape 时本应把第 4 个 VC 还给普通 traffic，旧 Garnet 范围计算却仍排除了 VC3；standalone 为复现实验默认保留兼容开关，做 NoEscape 新实验时必须使用修正模式。

## 4. 实验配置、可变参数和 traffic 定义

正式 Garnet 配置使用 `build/Garnet_standalone/gem5.opt`、`ExpressMesh` topology、64 CPUs、64 directories、8 mesh rows、4 VCs/vnet，并固定 `inj-vnet=0`，所以每个 packet 是一个单 flit control message。router latency、base mesh link latency 和主实验的 express latency均为 1 network cycle，control VC buffer depth 为 1，link 每 cycle 可传一个 flit。Ruby/network clock 为 2 GHz，而 synthetic tester 为 1 GHz，因此 tester 每两个 network cycles 才尝试注入一次；配置 injection rate 为 `R` 时，所有 source 都激活的 traffic 最大 offered throughput 是每 router 每 network cycle 的 `R/2`。例如 rate 0.8 的上限是 0.4，不是 0.8。CutStress 只启用一半 source，所以按全部 64 router 归一化的 offered throughput 是 `R/4`。吞吐主指标是 measurement 窗口内 accepted/received packets 除以 `64×measurement_cycles`，延迟以 network cycle 为单位；在 offered load 超过服务能力时，源队列会持续增长，此时吞吐平台仍有意义，但平均延迟会随窗口长度继续增大，不能称为稳态有界延迟。

正式长测一般使用 20,000 cycles warmup、100,000 cycles measurement、traffic seeds 1/2/3、escape timeout 32 和 NI watchdog 50,000 cycles。standalone 的大规模筛选常用 1,000+5,000 或 5,000+30,000，以便快速淘汰；最终关键点再使用 20,000+100,000 与 Garnet 对照。当前常用 source-route 参数是 K=8、policy 4、reservation weight 0.375、express VC weight 0.625、XY mesh segment。placement 变量包括 wire budget 16/24/32/48/64、max degree 1/2、minimum express length 3、candidate mode all/axis/stride4、ideal/length-aware latency、Random seed、Greedy objective 和 Hybrid 权重。routing 变量包括 K=8 至 512、policy 0 至 6、pressure weights、escape timeout、是否启用 escape、ordinary mesh routing 和 traffic seed。诊断指标除 throughput/latency 外还包括平均 hops、express traversals per delivered packet、escape fraction、reservation balance、最大/P95/CV link utilization、每源公平性、全局 flit/delivery progress 和 drainability。

Uniform-random 让每个 source 独立、均匀地选择 64 个 router 中的目的，当前运行时允许约 `1/64` 的 local destination；部分离线 demand 构造曾排除 source 本身，这是一个很小但应继续统一的口径差异。CutStress 只让左半边 source 注入，并将 `(x,y)` 发送到 `(7-x,y)`，所有流都穿越中央垂直 cut，用于测试横向 bisection capacity；它不是全 source traffic，比较吞吐时必须记住 offered load 只有一般 traffic 的一半。Hotspot 以 50% 概率选择两个中心 router `(3,3)`、`(4,4)` 之一，另 50% 选择均匀非本地目的；如果 source 恰好等于选中的 hotspot，会改发另一个中心。Hotspot 的主要瓶颈是两个目的端 ejection port，内部 express link 不能创造额外接收带宽，所以合理目标是“不明显退化”，而不是保证胜过 Mesh。

Bit-complement 是平衡 permutation：`(x,y)→(7-x,7-y)`，每个 source 和 destination 都恰好出现一次，路径跨越二维镜像位置。Tornado 也是平衡 permutation：`(x,y)→((x+3) mod 8,y)`，只在同一行内移动 3 格。它们没有 Hotspot 的接收端集中问题，适合检验 topology-aware placement 是否真的利用已知 demand。当前 Garnet runner 固定传入 `--xor-low-bit=0`，并解析 `ctrl_traffic_distribution.n<src>.n<dest>`；对 Bit-complement、Tornado 和 CutStress，如果任何 packet 没到定义中的目的或出现不应注入的 source，整次 run 会直接失败。这项检查是所有后续 deterministic traffic 实验的必要条件。

## 5. 已完成实验、参数与 performance

目前最可信的 matched deterministic-traffic 结果来自 V5 修复后的 Garnet：`8×8、rate=0.8、budget=64、degree=1、minimum length=3、ideal express latency、K=8、policy 4、weights=0.375/0.625、escape on、20k+100k、3 seeds`。Random 与 traffic-aware Greedy 使用相同资源约束，Mesh-XY 不含 express。六个 matched 配置在相同 standalone 中的 throughput 与 Garnet 相差不到 1%，因此这些数据同时验证了快速模型，而不是只验证某个漂亮的拓扑排序。

| Traffic/topology | Garnet throughput | Garnet latency | Avg hops | Express/packet | Delivered via escape |
|---|---:|---:|---:|---:|---:|
| Bit-complement Mesh-XY | 0.128327 | 39755 | 6.548 | 0 | 34.5% |
| Bit-complement Mesh per-hop adaptive | 0.06617（仅 2 个默认 watchdog run 完成） | 50524 | 5.782 | 0 | 52.4% |
| Bit-complement Random | 0.176410 | 33243 | 5.495 | 0.540 | 17.1% |
| Bit-complement Uniform-ASPL | 0.183101 | 33467 | 6.104 | 0.715 | 19.8% |
| **Bit-complement-aware ASPL** | **0.275805** | **17391** | **3.480** | **0.696** | **0.9%** |
| Tornado Mesh-XY | 0.211563 | 23719 | 3.886 | 0 | 5.1% |
| Tornado Mesh per-hop adaptive | 0.211570 | 23725 | 3.886 | 0 | 5.1% |
| Tornado Random | 0.223649 | 21139 | 3.848 | 0.090 | 4.8% |
| **Tornado-aware ASPL** | **0.379366** | **2459** | **2.502** | **0.598** | **0.0%** |

Matched traffic-aware ASPL 相对相同预算 Random，在 Bit-complement 上吞吐提高 56.3%、延迟降低 47.7%，在 Tornado 上吞吐提高 69.6%、延迟降低 88.4%；相对 Mesh-XY 的吞吐提高分别为 114.9% 和 79.3%。Tornado-aware 的 0.3794 已接近 rate 0.8 在 2:1 clock ratio 下 0.4 的 offered 上限。机制也与数字一致：matched 拓扑显著减少 hops、提高有效 express 使用并几乎消除 escape，而不是简单把更多 packet 塞进长线。Uniform-ASPL 对真正 Bit-complement 也略胜 Mesh/Random，但不如 matched placement；这说明通用拓扑有一定泛化能力，已知 demand 仍能带来大幅额外收益。Bit-complement 的 Mesh per-hop adaptive 有一个 seed 在 50k NI watchdog 下退出，将阈值提高到 150k 后可以完成，但吞吐仍约 0.066；这是严重 injection starvation，而不是已证明的全网 deadlock。

通用 Uniform 设计中，最有效的 Garnet 数据来自 V3 的 Stride-ASPL。旧 runner 的 Uniform 分布虽然经过 XOR，但均匀分布在异或置换下保持均匀，故以下 Uniform 长测仍可用于性能判断。配置同样为 budget 64、degree 1、16 条 stride-4 link、K=8、policy 4、20k+100k、3 seeds。

| Uniform rate | Mesh-XY th / lat | Stride-Random th / lat | Stride-ASPL th / lat | Greedy vs Mesh throughput | Greedy vs Random throughput |
|---:|---:|---:|---:|---:|---:|
| 0.70 | 0.265936 / 12737.7 | 0.334073 / 2733.0 | **0.349648 / 16.4** | **+31.48%** | **+4.66%** |
| 0.80 | 0.266222 / 17928.7 | 0.320258 / 12758.6 | **0.353579 / 7278.6** | **+32.81%** | **+10.40%** |

Uniform 0.70 是当前最清晰的通用设计工作点：offered 上限为 0.35，Stride-ASPL 基本全部接受且延迟只有约 16 cycles，而 Mesh 和代表性 Random 已经进入积压区。Uniform 0.80 时三个方案都已过载，但 Stride-ASPL 的饱和服务率仍明显更高。旧 budget 16 的 V2 Uniform Garnet 结果只有约 5% 增益：rate 0.6 时 Mesh/ASPL 为 0.26546/0.27998，rate 0.8 为 0.26622/0.28155。standalone budget sweep 显示 ASPL-Greedy 在 Uniform 高压下随 budget 24/32/48/64 的吞吐约为 0.291/0.298/0.312/0.329；这说明早期提升小的首要原因是 express 资源本身太少，而不是“express link 原理上无效”。budget 16 在 Uniform 0.8 仅约 0.097 express traversals/packet；budget 64 的 Stride-ASPL 在 rate 0.1/0.7/0.8 分别约为 0.821/0.531/0.426，高压下降是 pressure-aware 节流的结果。

K 扩展和理想 Dijkstra 均在 standalone 上使用 Stride-ASPL、budget 64、degree 1。K sweep 是 seed 1、5k+30k、rate 0.8；三类路由对比是 3 seeds、5k+30k。K 从 8 增到 256 没有稳定改善，证明静态 top-8 已覆盖主要有用路径。

| K | Uniform th / lat | CutStress th / lat |
|---:|---:|---:|
| 8 | 0.35208 / 2082 | 0.14143 / 3703 |
| 32 | 0.35052 / 2153 | 0.14143 / 3703 |
| 64 | 0.35161 / 2099 | 0.14143 / 3703 |
| 128 | 0.35161 / 2099 | 0.14143 / 3703 |
| 256 | 0.35161 / 2099 | 0.14143 / 3703 |

| Traffic/rate | K=8 policy 4 th / lat | Express-only Dijkstra th / lat | Global-pressure Dijkstra oracle th / lat |
|---|---:|---:|---:|
| Uniform 0.5 | 0.24966 / 13.2 | 0.24966 / 13.0 | 0.24966 / 13.1 |
| Uniform 0.7 | 0.34948 / 16.3 | 0.34949 / 15.4 | 0.34949 / 13.8 |
| Uniform 0.8 | 0.35141 / 2136 | 0.33403 / 2962 | **0.39961 / 14.8** |
| CutStress 0.5 | 0.11855 / 712 | 0.12438 / 62 | **0.12479 / 12.7** |
| CutStress 0.7 | 0.13855 / 2475 | 0.14992 / 1731 | **0.16693 / 574** |
| CutStress 0.8 | 0.14137 / 3693 | 0.15678 / 2757 | **0.17547 / 1479** |

Global oracle 在 Uniform 0.8 相对 K=8 吞吐提高 13.7%、延迟降低 99.3%，在 CutStress 0.8 吞吐提高 24.1%、延迟降低 60.0%。这不是可直接提交的 routing winner，而是最重要的诊断结果：如果知道 mesh pressure，现有 topology 仍有很大未释放性能；仅增加 K 或仅观察 express pressure 无法获得这部分收益。

SA 的有效 standalone 验证为 budget 64、degree 1、policy 4、5k+30k、3 seeds、rate 0.8。Uniform-only SA 的 Uniform 结果为 `0.35281/2001`，略高于 Stride-ASPL 的 `0.35141/2136`，也高于代表性 Random 的 `0.32670`；但其 CutStress 仅 `0.12998/4723`，低于 Stride-ASPL 的 `0.14137/3693`，也低于 Random 的 `0.13927`。Balanced SA 的 Uniform 为 `0.27934/3439`，低于 Stride-ASPL 和 Random；CutStress 为 `0.16850/1845`，高于 Stride-ASPL 和 Random。另一个 unrestricted `sa_aspl` 在 Uniform/CutStress 0.8 只有约 0.33857/0.12288。故 SA 不是“完全没用”，它找到了偏 Uniform 或偏 CutStress 的 Pareto 解；目前失败的是找到一个跨 traffic 全面支配 Greedy 的单一布局，而且短窗口搜索曾严重高估 balanced 解的长期 Uniform 吞吐。

旧 V3 Garnet 报告中的 CutStress 0.5/0.8 表，以及 V2 的 CutStress 1.0 表，都是在修复 `--xor-low-bit=0` 前生成的。按照已确认的地址公式，实际 destination 会随 packet number 被 XOR 改写，所以这些数字只能作为历史调试记录，不能继续称为 CutStress Garnet 结果。V4 报告中旧 Bit-complement/Tornado 表同样已经作废：旧 run 每个 source 平均到达约 63 个不同目的，命中标称目的的比例只有约 1.3%，实际是带 256-packet burst 的近似全目的扫描。修复后 matched standalone 与 Garnet 的六组吞吐分别为 `0.127969/0.128327`、`0.176539/0.176410`、`0.273650/0.275805`、`0.210176/0.211563`、`0.222390/0.223649`、`0.380641/0.379366`，已经排除了“standalone 代码错误导致整个设计过拟合”的猜测。当前可以支持的总论是：通用 Stride-ASPL 在 Uniform 高压下明显优于 Mesh 和平均 Random；matched traffic-aware ASPL 在正确的 Bit-complement/Tornado 长测中显著优于 Mesh、Random 和通用 ASPL；但尚未证明任何单一拓扑对所有 traffic、所有 rate 或每一个 Random 样本都更优。

## 6. 接下来可尝试的算法和需要补全的实验

路由方向最值得投入的不是继续机械增大 K，而是用可实现方式逼近 global-pressure Dijkstra 所揭示的 mesh-pressure 收益。可以让 router 或 NI 只维护少量区域级、行列级或 express endpoint ingress/egress corridor 的低比特拥塞摘要，通过低频 beacon 传播，而不是广播全网每条 link 的瞬时 VC 状态；候选评分在现有 `static + express reservation + express VC` 上增加这些 mesh segment 的估计代价。也可以从 global oracle 的决策日志中做离线 distillation，拟合只依赖 source、destination、候选 express endpoint 和少量局部/区域计数的规则。候选集合本身可从“静态 top K”改成兼顾 route/corridor diversity 的小集合，例如优先保留 mesh segment 较不重叠、使用不同 express endpoint 的路线，或按压力动态生成少量备选；这样增加的信息具有可辨识价值，而不是加入大量同质长 detour。所有方案都应继续保持 committed route 和单向 escape transition，避免重新引入 per-hop 随意转向造成的依赖与抖动。

placement 方向应从单纯 ASPL 转向“需求与容量共同建模”。可先用离线 multicommodity-flow、最大 concurrent flow 或带 router/endpoint capacity 的近似模型作为 topology oracle，再将其结果转化为可解释 Greedy：每轮加入最能降低预测最大 corridor/port load 的边，并在若干步后允许 swap 或 remove-and-reinsert。SA 可以延长测量窗口、使用多个 traffic seed 和多次 restart，将训练、验证 topology 分离，并维护 throughput/latency/robustness 的 Pareto front，而不是把多 traffic 压成一个容易过拟合的标量。还可以采用 tabu search、large-neighborhood search、beam search 或交替优化：固定 routing 估计 flow 后换边，固定 topology 后重新估计 routing，直到收敛。Traffic-aware placement 应分别研究“已知单一稳定 workload”和“已知 workload mixture”；后者应对 Uniform、CutStress、Bit-complement、Tornado 的训练组合优化，并在未参与搜索的 traffic 上测试泛化。Stride 不应被当作不可更改的规定，可以作为强 baseline，与 unrestricted、axis-only、有限长度集合和可布线分层长线方案公平比较。

实验上首先要补跑修复地址映射后的 CutStress 完整 Garnet 曲线，因为旧 V2/V3 CutStress Garnet 结论已经失效；Hotspot 也应在 `xor-low-bit=0` 下重跑，用来确认各设计至少不会显著退化。Bit-complement 和 Tornado 目前只有 rate 0.8 的正式 matched 长测，应补齐从低负载到饱和后的 throughput-latency 曲线，并加入“matched topology 跑错 traffic”和混合/随时间切换 traffic，区分专用收益和鲁棒性。还应加入 transpose、shuffle、neighbor、adversarial permutation 等端点均衡流量，报告多个 traffic seed、多个 Random topology seed、置信区间和 per-source fairness；对所有接近饱和的点检查 throughput 是否随 measurement 窗口增长继续下降，并做停止注入后的 drain test，避免把 transient 当作服务能力。

硬件现实性方面需要系统测试 length-aware express latency、不同 buffer depth、VC 数、escape timeout、router latency、link bandwidth，以及 express 端口/跨线的面积、功耗和时钟代价。网络规模至少应扩展到 `4×4` 和 `16×16`，并按 router 数、芯片边长或总 wire length 对 budget 归一化，判断当前 stride-4 与 degree-1 结论是否能缩放。最终图表应同时给出 throughput-latency curve、饱和点、平均 hops、express utilization、escape fraction、最大/P95/CV link load、endpoint/corridor heatmap 和 starvation/fairness；论文中的 deadlock 部分则应把 escape 的 channel-dependency 证明、普通到 escape 的不可逆关系、仲裁公平假设和有限注入 drain 实验证据分开陈述。完成这些补测后，项目才可以从“在当前 8×8 单 flit 场景中有明确 winner”推进到“对更一般 NoC 配置具有可信结论”。
