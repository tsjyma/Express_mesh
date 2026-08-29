星期三 11:32
report1.pdf
PDF
report2.pdf
PDF
report3.pdf
PDF
report4.pdf
PDF
b03a7408-fdc1-4894-a6c9-8cde940441e0.pdf
PDF
Lab4-Project.md
文件
Lab0_Preliminary.md
文件

我发给你了我们一门课的大作业要求，以及历年一些比较优秀的project report。我们现在选择Topic 1: Topology & routing algorithm。我现在希望你帮我调查：

这方面目前的科研都在做什么，有哪些比较nontrivial的成果
如果我们要做的话，如何组织结构（而不是简单地present一个topology）能拿高分，怎样讲好一个故事
我们可以考虑哪些创新方向

我把你发的作业要求和几份历年 report 都看了一遍，也查了近两三年 MICRO / HPCA / SC / NSDI 里和 topology、routing、chiplet/wafer interconnect 相关的工作。

先给一个最核心的判断：

Topic 1 想拿高分，最好不要做成“我们实现了某个 topology，然后再实现一个 routing algorithm”。应该做成一个小型 architecture research project：先提出一个明确的网络瓶颈，再让 topology 和 adaptive routing 分别解决这个瓶颈的不同部分，最后通过 adversarial traffic、ablation 和内部网络行为证明“为什么它有效”。

课程本身要求的其实已经很接近这个框架：至少一个新 topology、对应的 deadlock-free adaptive/non-deterministic routing，以及 appropriate baselines。 而且你们用的是 gem5 Garnet standalone，这非常适合做 topology/routing 的机制级实验。

1. 现在 topology & routing 的科研主要在做什么？

我觉得近年的大趋势可以概括成一句话：

大家已经不太满足于单独问“什么图的 diameter 小”，而是在做 topology × routing × physical constraints × workload 的 co-design。

这和十几二十年前单纯比较 Mesh / Torus / FatTree / DragonFly 的味道已经明显不同。

方向	现在真正的问题	代表性成果	对你们 project 的启发
Chiplet / wafer-scale topology	封装技术允许大量长线、die-to-die link 后，传统 Mesh 和高 radix switch 是否还是合理？	Ring Road、Switch-Less Dragonfly	topology 要与 physical connectivity/cost 一起讲
Topology synthesis	不人工拍脑袋设计图，而是给定 degree/wire/cost constraint 自动生成 topology	CINT-AD	非常适合课程项目：Python 离线生成 + Garnet 仿真
Workload-aware topology	不存在 universal best topology；AllReduce、PIM、MoE 各有特殊 communication pattern	TopoOpt、TACOS、PIMnet	从 traffic structure 推 topology，比“我喜欢这个图”高级很多
Congestion-adaptive routing	shortest path 在 hotspot/adversarial traffic 下很差，怎样用 path diversity？	UGAL 类、Valiant/non-minimal、escape-VC routing	可以把创新重点放在 path selection metric
Deadlock modularity	chiplet 是黑盒时，还能否保证整个 system deadlock-free？	HPCA 2026 DFBM	deadlock proof 本身也可以成为 contribution
Realistic / heterogeneous traffic	synthetic uniform random 和真实 GPU traffic 差多远？	MICRO 2024 real GPU NoC characterization	不要只跑 uniform_random
Topology/routing/application co-design	mapping、collective algorithm、routing、topology 应不应该联合优化？	TopoOpt、TACOS	最“research-like”的叙事方式
1.1 一个很明显的前沿：wafer / chiplet 改变了 topology 的设计空间

你们老师组近年的 Ring Road 就是一个很典型的例子。它不是说“再发明一个奇怪的 graph”，而是先观察传统 router-based NoC 的几个问题：router overhead、片内/片间 traffic 混合，以及 multi-chip routing complexity；然后把高速 independent rings 和 compact routers 组合起来，用极坐标描述 topology，再配套设计 deadlock-free routing。

这类工作的 nontrivial 点在于：

物理结构 → graph structure → coordinate system → routing rule → deadlock correctness 是一整套。

另一个很值得你们看的就是 SC 2024 的 Switch-Less Dragonfly on Wafers。传统 DragonFly 的问题之一是 high-radix switches 昂贵；wafer-scale wiring 允许把这些连接“铺开”，于是作者直接去掉高 radix switch，同时又设计了 minimal/non-minimal 的 deadlock-free routing，而且只需要一个额外 VC。

这几乎就是你们这个课程 Topic 1 的“研究版满配答案”。

1.2 一个非常适合你们模仿的方法论：自动 topology synthesis

2025 的 CINT-AD 很值得仔细看，因为它甚至和你们一样用 gem5 Garnet 做 evaluation。

它的思路不是：

“这是我设计的 topology。”

而是：

“给定物理约束、symmetry、性能目标，能否自动产生 topology？”

然后配了一个基于 Up/Down escape VC** 的 deadlock-free routing，再评价 latency、throughput、power、area 和 sensitivity。

这其实提示了一个非常好的课程创新空间：

你们未必要“手绘”一个 topology。可以设计一个 topology generator。

哪怕你们的优化算法只是 greedy，而不是复杂 combinatorial optimizer，整个 project 的研究感都会强很多。

1.3 workload-specific / collective-specific network 是另一条主线

比如 NSDI'23 的 TopoOpt，核心不是寻找一个 universal topology，而是联合优化：

DNN parallelization strategy ↔ traffic demand ↔ topology ↔ routing。

他们甚至做了 12-node 100Gbps RDMA prototype。

MICRO'24 的 TACOS 更进一步：对于一个给定 topology 和 collective，自动合成 topology-aware collective algorithm；128 NPU AllReduce 可以很快合成，并且能扩展到非常大的系统。

HPCA'25 的 PIMnet 则是 domain-specific topology 的典型案例：因为 PIM bank 之间经过 host 通信太慢，设计一个匹配 DRAM packaging hierarchy 的多层 network，直接支持 collective communication；论文报告 collective 最高 85×、实际 application 最高 11.8× 的提升。

这些工作的共同点其实就是：

先从 communication pattern 找 structural bottleneck，再设计 network。

这正是你们 report1 做得很漂亮的地方。它不是先造 topology，而是先指出 matrix multiplication 中同一 row 的 PE 都需要相同 A block、同一 column 都需要相同 B block，于是自然导出 row/column distribution structure 和 multicast。

1.4 routing 的研究重点也从“最短路”变成“何时值得绕路”

adaptive routing 真正有意思的地方不是“随机挑一条路”，而是这个 tradeoff：

多走几 hop vs 绕开 congestion。

这其实在你们发来的 report 里已经体现得很好。那个 arbitrary-topology report 观察到 adaptive routing 会增加 average hops，但通过使用更多 links、均衡 load，反而降低 latency。

SlimFly 那组用的 UGAL-L 也是这个思想：采样 non-minimal paths，然后把 queue length × path length 作为 cost。

所以一个新的 routing contribution 不一定需要极复杂。真正重要的是：

你选择了什么 congestion signal？候选路径怎样生成？在什么 regime 下切 minimal/non-minimal？deadlock 如何保证？

1.5 “real traffic 根本不 uniform”也越来越重要

MICRO'24 有一篇很有意思的测量工作，直接测真实 NVIDIA V100/A100/H100 一类 GPU 的 NoC，发现 core 到 memory partition 的 on-chip latency 会因为物理位置产生最高约 70% 的非均匀性，而 bandwidth 又相对更均匀。

2026 年 HPCA 也已经明显在讨论 chiplet communication 的 burstiness、open chiplet interoperability 等问题。例如 DFBM 研究的是：不同 vendor 的 chiplet 内部 NoC 是黑盒时，怎样仍然保证 inter-chiplet communication deadlock-free。

所以现在再做 topology，如果实验全是：

uniform_random → latency curve

会显得很旧。

2. 从你发来的历年优秀作业看，怎样组织才能拿高分？

我觉得这些 report 暗示出的“评分偏好”非常明显。

最好的项目不是代码最多，而是有一条因果闭环：

Problem → Observation → Design → Prediction → Experiment → Mechanistic explanation

SlimFly 那一组尤其典型。他们开头不是“我们实现了 SlimFly”，而是先抛出 degree–diameter tradeoff：希望 diameter 小，但 router degree 又不能无限涨。然后再说 SlimFly 为什么是这个 graph-theoretic problem 的一个漂亮解。 接着他们不仅复现 MIN/VAL/UGAL，还自己提出了 hybrid routing，并自己做 diameter=2 的证明。

matrix-multiplication 那一组则做对了另一个非常关键的事情：ablation。他们不只画“ours vs mesh”，而是拆成 vanilla / varying-link-period / optimized-traffic / without-multicast，然后每一组 comparison 验证一个具体 mechanism。 更漂亮的是，因为 multicast 改变了发送 packet 的数量，他们甚至重新定义了公平的 “one injection”——比较的是“完成一次完整矩阵 distribution”，而不是盲目比较相同 packet injection rate。

DragonFly 那组也有一个很值得模仿的实验设计：uniform_random 下 adaptive 和 minimal 几乎一样，于是他们人为构造 single-destination adversarial traffic，这时 minimal path 出现严重拥塞，而 adaptive routing 通过 non-minimal path 做 load balancing，优势才显现出来。 随后他们又分析为什么：uniform 下各处 VC occupancy 差不多，adaptive 大部分时间实际上还是选 minimal；adversarial 下才真正使用 path diversity。

而 report2 最像真正 architecture paper：不仅报告 end-to-end latency，还画内部 forwarding path / link load / VC assignment，研究不同 adaptive/deterministic VC 数量，并且愿意报告“更多 adaptive VC 并不一定更好”。 甚至两个 VC-balancing heuristic 没有明确 winner，他们也如实报告，并进一步研究 convergence scalability。

因此我会建议你们按照下面这一个结构做整个 project，而不是按“Topology / Routing / Experiment”机械展开：

Motivation / research question：明确指出现有 topology 在某种 constraint/traffic 下为什么失败，最好先用一个小 experiment 或 graph metric 把问题可视化。
Design goals：例如 fixed router radix、fixed extra wire budget、low zero-load latency、high path diversity、deadlock freedom。这里最好存在明确 tradeoff。
Topology design：每一类新增 edge 都必须回答“为什么在这里”，而不是“这样看起来更密”。同时分析 degree、diameter、average shortest path、bisection/path diversity、wire-cost proxy。
Adaptive routing：先解释 minimal routing 的 failure case，再提出 candidate path 和 congestion score。一定单独讲 deadlock-free argument，而不是最后一句“we use 2 VCs”。
Implementation：只讲真正困难/有研究含量的 gem5 修改，例如 arbitrary outport、routing table、VC state、congestion statistics，不要拿大量 LOC 当 contribution。
Evaluation methodology：先声明 fairness。节点数、VC/buffer、link bandwidth、router radix、额外 wires 怎么归一化；然后定义 hypotheses，而不是直接甩曲线。
Results + ablation + mechanism：至少包括低负载 latency、saturation throughput、average hops，再加 link-utilization heatmap / max-link load / path stretch / VC occupancy。用 adversarial traffic 证明 routing 的必要性，用 ablation 分开 topology 和 routing 的贡献。
Limitations / insight：什么 traffic 下不赢？为什么？scale 上去会怎样？一个有解释的 negative result 往往比“所有图 ours 都最好”更可信。
3. 你们最应该避免的一种实验结构

我特别不建议最后只有：

Mesh + XY
vs
Our topology + our adaptive routing

然后发现 ours 快很多。

这实际上根本回答不了：

是 topology 赢了还是 routing 赢了？
是不是只是因为你用了更多 links？
是不是你 router radix 更大？
minimal routing 放在新 topology 上已经一样好？
adaptive routing 放在 Mesh 上是不是也会赢？
只在 uniform traffic 赢还是 hotspot 也赢？

比较漂亮的 experimental matrix 应该至少是：

	Minimal / deterministic	Adaptive
Baseline topology	A	B
Proposed topology	C	D

这样可以回答两个非常漂亮的问题：

A → C：topology 本身贡献多少？
C → D：adaptive routing 在新 topology 上额外贡献多少？

如果 adaptive algorithm 能泛化到 baseline topology，B 又能告诉你 topology 和 routing 是否有真正的 interaction effect。

再加上：

Proposed topology + random/heuristic edge placement vs optimized placement

你就可以证明“不是多几根线就行，而是我们放线的方法确实重要”。

4. Baseline 的“公平”非常重要

这里我甚至觉得是你们最容易比历年作业再提高一档的地方。

例如 SlimFly / DragonFly 天生比 Mesh 多很多 connectivity。如果简单按相同 link bandwidth 比，它们赢很正常，但不一定是公平 architecture comparison。

我建议至少报告两种 setting：

Setting	固定什么	回答什么问题
Iso-link	每条 link bandwidth/latency 相同	topology 的原始性能上限
Iso-cost	限制总 links / total wire length / router degree / aggregate bandwidth	相同硬件预算下谁更好

哪怕 Garnet 不能精确建模 area，你们完全可以定义简单 proxy：

Cost = Σ edge Manhattan length，再加 max degree。

如果增加一根长度 7 的 express link 和一根长度 1 的 mesh link都算“1 link”，实际上是不太公平的。

如果你们最后能说：

在相同 long-wire budget 和 maximum radix 下，我们的方法 saturation throughput 仍然提高 X%。

这个 statement 会比“比 Mesh 快 X%”强很多。

5. 我最推荐的创新方向

我按“研究感 × 实现可控性 × 跟课程要求契合度”来排：

方向	研究感	工程难度	风险	我的评价
A. Cost-constrained topology synthesis + adaptive routing	★★★★★	★★★☆	低中	最推荐
B. Traffic-aware topology synthesis	★★★★★	★★★★	中	最像真正 paper
C. Heterogeneous/express-link topology + latency-aware routing	★★★★☆	★★★	低中	很现代，也很好讲
D. 新 topology + improved UGAL/lookahead routing	★★★★	★★★	低	稳妥
E. Collective/MoE-aware topology	★★★★★	★★★★☆	中高	story 非常强，但 workload 要做好
F. Failure-aware adaptive topology/routing	★★★☆	★★★★	中	correctness 很漂亮
G. RL/ML routing	★★★	★★★★★	高	我反而不优先推荐
6. 我最推荐的具体方案：Budgeted Express-Mesh

如果让我替你们现在直接定一个题，我会倾向于：

Cost-Constrained Express-Mesh with Deadlock-Free Congestion-Adaptive Routing

这个项目的核心研究问题是：

在固定 wiring / router-degree budget 下，应该把少量 long-range links 放在哪里？加完这些 link 后，怎样让 routing 真正利用新增的 path diversity，而不会造成 deadlock？

这比“实现 SlimFly”强的地方在于，你们自己的 contribution 非常清晰。

Topology 部分

以 \(8\times8\) Mesh 为 backbone，不推翻 Mesh，只允许加入 \(B\) 条 express links，同时设 degree cap，比如每个 router 最多 5 或 6 个 network ports。

然后定义 cost，例如：

\(C(E)=\sum_{(u,v)\in E_{\rm express}} d_{\rm Manhattan}(u,v)\)。

在固定 \(C(E)\le B\) 的情况下，寻找 express link placement。

最简单也足够 nontrivial 的 optimizer 是 greedy：

候选 edge 加入后，计算它对下面目标的改善：

average shortest-path distance；
diameter；
maximum edge betweenness；
或某个 target traffic matrix 下的最大 link load。

每轮加入 benefit/cost 最大的一条。

这就已经从“topology design”升级成：

topology synthesis problem。

而且这和 CINT-AD 的研究方向高度呼应，但规模和实现难度非常适合课程。CINT-AD 本身也是“physical constraint + automated topology generation + escape-VC routing + Garnet evaluation”。

7. Routing 怎么做才既有创新又容易证明 deadlock-free？

你们可以让 adaptive routing 在两个层次选择。

正常情况下，它比较几条候选路径：

augmented graph 上的 shortest path；
一条经 express link 的 alternate path；
1–2 条经中间节点的 non-minimal path。

score 不必很复杂，例如可以用：

\(S(P)=\text{hop}(P)\,[1+\alpha q_{\rm first}(P)]\)

或者

\(S(P)=\text{remaining latency}+\alpha\cdot\text{congestion}\)。

这和 UGAL 的精神是一致的，但你们可以进一步研究：

first-hop congestion 到底够不够？

例如比较：

Routing	congestion information
UGAL-L-like	first output queue
Lookahead-2	first two links
Bottleneck-aware	max predicted occupancy
EWMA	recent link-utilization history

然后你们很可能得到一个挺有意思的结论：

更“聪明”的 congestion information 并不一定更好，因为 stale information / extra detour 会抵消收益。

这种 mixed result 其实非常像真正论文。

8. Deadlock freedom 可以成为你们项目里一个很漂亮的理论点

最方便的设计不是费劲证明所有 adaptive paths 的 CDG 都无环，而是保留一个 escape network。

例如：

VC0 = adaptive VC
VC1 = escape VC

在 adaptive VC 中允许使用 Mesh + express links。

一旦满足某个条件，例如：

adaptive candidate 都 blocked；
或 stall 超过 \(T\) cycles；

packet 可以不可逆地：

Adaptive → Escape

而绝不能：

Escape → Adaptive。

escape VC 只沿原始 Mesh 的 XY routing。

那么 escape routing 的 channel dependency 本身是 acyclic 的，而 packet 总有机会逃入这个 deadlock-free subnetwork。你们可以用 CDG 或 Duato-style argument formalize 这个性质。

这样非常适合 presentation，因为一句话就能形成很漂亮的设计：

The express network is aggressive; the underlying mesh is the safety net.

而且 topology 和 routing 形成了真正的整体，而不是两个拼起来的作业任务。

9. 这个题最漂亮的 story 会是什么？

你们整个 report 可以围绕三个 hypothesis：

Hypothesis	实验
H1：少量合理的 long links 可以显著降低 structural bottleneck	Mesh vs Express-Mesh + minimal
H2：仅仅增加 links 不够，minimal routing 无法在 congestion 下利用 path diversity	Express-Mesh minimal vs adaptive
H3：placement 与 routing 必须 co-design	random links vs distance-only links vs congestion-aware links + adaptive

然后构造 traffic。

uniform_random 用于 general sanity check。

transpose / tornado / shuffle 用于传统 synthetic comparison。

再专门设计一个 cut-stressing / hotspot traffic，让一组 source-destination pairs 的 minimal paths 穿过同一片区域。

最后画一张 link-utilization heatmap：

Mesh 上中间一条 cut 红得发紫；

Express-Mesh + minimal 好一些；

Express-Mesh + adaptive 负载被摊开。

这张图可能比十条 latency curve 都更有说服力。

你们发来的 report2 已经证明这种“内部 dynamics visualization”非常有效——他们不是只说 adaptive 快，而是直接看 packet 使用哪些 links，再得出“用了更多 network links → load 更均匀 → latency 更低”的机制解释。

10. 第二推荐：Traffic-aware topology synthesis

如果你们愿意更激进一点，可以把问题进一步改成：

同样的 hardware budget 下，不同 traffic distribution 是否应该产生不同 topology？

例如构造三种 demand matrix：

Uniform
所有 pair 大致均匀。

Hotspot
20% nodes 接受 60% traffic。

Clustered / chiplet-like
cluster 内 traffic 很大，cluster 间较小。

然后 topology generator 根据 \(D_{sd}\) 自动加 express edges。

这时会出现一个非常有意思的研究问题：

为一种 workload optimized topology 在另一种 workload 上会不会 overfit？

于是你可以画：

training traffic	Uniform test	Hotspot test	Clustered test
Uniform-optimized topology
Hotspot-optimized topology
Cluster-optimized topology
Traffic-agnostic topology

这一下就很像 ML 里的 distribution shift，但对象是 architecture。

最后再设计一个 robust topology，优化 average-case 或 worst-case。

这是我认为你们完全有机会做出“比作业要求高一层”的地方。

11. 第三推荐：Heterogeneous links + latency-aware adaptive routing

另一个现代感很强、同时不会过度复杂的方向是：

Mesh 中不是所有 links 都一样。

例如普通 local link：

latency = 1，bandwidth = 1。

express link：

latency = 2/3，但跨越多个 routers；

或者 bandwidth 更高，但只有少量。

这时 shortest-hop routing 不再等价于 shortest-latency routing。

routing 的 cost 可以同时考虑：

physical latency + queueing delay + remaining distance。

这和真实 GPU / chiplet 系统非常契合：MICRO'24 的真实 GPU 测量已经说明片上 communication latency 明显具有 spatial nonuniformity。

于是你们可以问：

Hop-count-based UGAL 在 heterogeneous network 上是不是错误 metric？

改成 weighted-latency-aware UGAL。

这个问题很干净，也很容易 experimentally validate。

12. 一个很诱人但我不太推荐的方向：RL routing

2025/26 的确能找到越来越多 ML/RL-assisted routing 的工作，但是对于你们这个 project，我觉得性价比不高。

原因不是它“不前沿”，而是它容易导致整个 story 失焦：

network topology 是一个 contribution；

RL training 是另一个 contribution；

state/action/reward 是另一个坑；

generalization 是另一个坑；

最后还得证明 deadlock-free。

很可能做了非常多工作，但 presentation 最后只能说“DDQN latency 比 heuristic 低 5%”，反而不如一个非常清楚的 graph + routing insight。

如果真想沾 ML，我宁可做：

offline topology search / optimization

而不是 online RL routing。

13. 我也不太建议你们直接做这几个题

3D Torus + 一个标准 adaptive routing：能满足要求，但故事比较 textbook。

SlimFly + UGAL-L：你们给我的历年优秀作业已经完整做过，而且还加了 hybrid routing 和 diameter proof。 除非你们有明显不同的 research question，否则容易变成复刻。

DragonFly + Valiant/UGAL：同样，历年报告已经有 DragonFly + minimal/Valiant/adaptive，甚至进一步组合了 DRAIN。

只设计一个花哨的新 graph：这是最需要避免的。没有 failure case、没有 hardware budget、没有 routing interaction，就很难回答“为什么是这个 topology”。

14. 8 分钟 presentation 我会怎么分配

不要按照 report 的篇幅比例讲。

前 1 分钟必须让老师明白现有网络哪里坏了，最好直接上一张 link-load heatmap 或一个 bottleneck diagram。

接下来约 1 分钟讲 topology，而且只讲 topology 中和问题有关的结构。

然后约 1 分钟讲 adaptive routing + 一个非常简洁的 deadlock-free argument。

Implementation 控制在 40–50 秒，只讲最难的 gem5 modifications。

真正应该占最多时间的是大概 3 分钟 evaluation：baseline fairness → main result → adversarial case → ablation → heatmap。

最后用大约 40 秒总结三个 finding，再很快交代 division of labor。课程 presentation 明确要求包含 Architecture / Implementation / Evaluation / Division of Labor。

也就是说，你们的 PPT 不应该让“Topology diagram”成为高潮。

高潮应该是：

我们预测这里会堵。
它确实堵了。
我们的 topology 把结构性 bottleneck 拆掉了一部分。
但 minimal routing 仍然会聚集。
adaptive routing 又把它摊平。
这是 link-utilization map。
因此性能提升来自我们声称的 mechanism，而不是偶然参数调出来的。

这就是一条很完整的 architecture story。

15. 如果现在让我替你们定题

我会把优先级定成：

第一选择：Traffic-aware / cost-constrained Express-Mesh topology synthesis + escape-VC adaptive routing。

它同时满足几个非常有利的条件：有自己的 topology、自己的 adaptive algorithm、deadlock proof 很好做、可以有自动 optimization、baseline 公平性好处理、ablation 很自然，而且和当前 chiplet/topology synthesis 的研究趋势一致。更重要的是，它完全可以在你们现有的 Garnet 环境里做，不需要把 project 变成一个巨型系统工程。

一个可以暂时使用的题目名是：

BudgetRoute: Cost-Constrained Topology Augmentation with Deadlock-Free Congestion-Adaptive Routing

如果你愿意，我下一步可以直接基于这个方向，继续帮你们把整个 project technical design 定下来：包括 topology generator 的具体 objective、greedy algorithm、routing pseudocode、deadlock-free proof、gem5 要改哪些文件、需要跑的完整实验矩阵，以及最终 report 应该出现哪几张关键图。

星期三 22:38
c88db812-d1d9-4b5f-9bd0-c295b4af445f.pdf
PDF
我队友目前做出来的实验，效果不理想，分析一下可能的原因
昨天 23:31
abde9860-3a3d-43c3-b9c9-18c50e8acbf2.md
文件

我发给你了，按照你的prompt修改之后的报告。请你：

给我讲清楚其中的实验设定，尤其是routing的算法的公式
告诉我这轮的实验结果意味着什么

Mesh + LegacyLocalAdaptive 里的 LegacyLocalAdaptive 就是你们上一版 report 里那套旧的 adaptive routing，之所以加 Legacy，只是为了和这轮新实现的 GlobalExpress / GlobalExpressReservation 区分。新 report 明确说它是“逐跳局部决策：依据第一跳拥塞、阈值和加权参数选择下一跳，packet 在途中可能重新规划”。

具体沿用上一版的规则是：

当前 router 只看候选 output 的 VC occupancy；
如果 deterministic minimal output 的 congestion \(Q<\theta=0.25\)，直接走 deterministic minimal；
否则对候选 next hop 按 \(C=L(1+\lambda Q)\) 打分，主 setting 是 \(\lambda=1\)；
candidate 必须让 remaining distance 严格下降，并满足原来的 bounded-detour 条件；
每到一个 router 都可以重新做一次这个决定。

而当 topology 是纯 Mesh时，没有任何 express link，所以它实际上可以理解成：

一个 local minimal-adaptive Mesh routing baseline。

例如从左下到右上，如果 East 和 North 都能让 Manhattan distance 减 1，那么它可以根据当前 East/North output congestion 选择更空的方向；如果当前 deterministic 方向还不堵，就继续 deterministic route。

所以它和我们刚才讨论的“pure Mesh 上每一段采用 local minimal adaptive”其实非常接近。

一个例子：

          N (queue=1)
          ↑
          |
current --+--> E (queue=5)

如果两边都属于合法 productive direction，而且 adaptive 已被触发，那么它会倾向 North。

不过它有两个特点：

只看当前 router 的 local output congestion，不知道后面的网络状况；
逐 hop 决策，不像现在的 GlobalExpressReservation 那样 injection 时先选完整 express sequence 再 commit。

因此 Mesh + LegacyLocalAdaptive 在这轮 report 里的角色就是：

“如果完全不加 express link，只使用我们以前那套局部 adaptive Mesh routing，性能是多少？”

它是用来回答新 Express-Mesh 至少能不能超过一个不是弱智 XY baseline，而是已经会局部 adaptive load-balance 的纯 Mesh。

另外，目前这个 baseline 只补跑了 rate=0.10、2 seeds，而 Express 的主 sweep 是其他 rates，因此 report 自己也强调目前还不能用这一行直接得出 peak-throughput 胜负。

很好。请你写个codex prompt，我的主要意图如下：

仍然保留mesh上adaptation的实验，尽量让mesh的adaptation策略和有express时的各段mesh上策略相同，方便比较公平
对于local mesh adaptative的情况，做两类，一类是有escape channel，一种是没有escape channel，可能在rate高时会有区别。但是你得控制变量让比较公平
使用更加广的injection rate range模拟更加阻塞情况，然后看不同的express连接方法和不同的adaptive routing algorithm他们各自的表现
保留q+r的这种routing cost预估，然后你再想一种可以作为baseline的cost预估。比如说每次就随机选一条路径，让它走

整体来说就是一轮更完整的validation

可以。这一轮我建议把目标收敛成一个很清楚的验证：

把“Express 选路”和“Mesh 内部自适应”分成两层；在完全相同的 local Mesh adaptive policy 下，比较 Mesh / Random Express / Greedy Express，以及 Q+R / Random-path 两种 express path selection；同时用 Escape/No-Escape 作为独立的 flow-control/correctness 维度。

最重要的是，不要让 Codex 又扩展成十几个 routing heuristic。下面这个 prompt 我已经把控制变量和主实验矩阵限定好了。

We need one focused, more complete validation round for the Budgeted Express-Mesh project.

Read the latest experiment report and existing implementation first. Reuse the current ExpressMesh topology, candidate generation, source-route state, q/r instrumentation, escape VC implementation, and measurement scripts where possible.

Do NOT redesign the project broadly. Do NOT add UGAL, EWMA, regional congestion, full-global Mesh congestion, RL, new placement objectives, or new topology sizes in this round.

The main research question is:

Can a sparse Express-Mesh outperform a pure Mesh when:
(1) express-sequence planning uses the existing global q+r information,
(2) every ordinary Mesh segment uses the SAME local minimal-adaptive routing policy as the pure-Mesh baseline,
and
(3) we evaluate far enough into the congested/saturation regime?

A secondary question is:
How does an escape channel affect performance/progress at high injection under otherwise identical adaptive routing?

==================================================
1. REFACTOR ONE SHARED LOCAL MESH ADAPTIVE ROUTER
==================================================

Implement/refactor ONE common local Mesh adaptive primitive and use it in BOTH:

A. pure Mesh adaptive routing;
B. every Mesh segment inside Express source-routed paths.

Do not maintain two subtly different implementations.

Suppose the current router is v and the current Mesh waypoint is w.

The legal productive outputs are exactly:

    O(v,w) = { (v,u) :
               u is a normal Mesh neighbor and
               ManhattanDistance(u,w)
               = ManhattanDistance(v,w) - 1 }

Among legal outputs with usable adaptive VC resources, choose the output with the lowest LOCAL congestion.

Use the same congestion measure everywhere, preferably the existing normalized output-VC occupancy / available-credit measure already used by LegacyLocalAdaptive.

Tie breaking must use the same seeded policy for Mesh and Express experiments.

Important:
- This is minimal adaptive routing inside a Mesh segment.
- It never increases Manhattan distance to the current waypoint.
- Therefore every Mesh segment still has exactly its Manhattan hop count.
- No global Mesh-link congestion information is used.

For pure Mesh:
    waypoint = final destination.

For an Express source route with planned express sequence [e1,e2,...]:
    waypoint = tail(next express edge)
until that express is traversed;
after all express edges are consumed:
    waypoint = final destination.

At an express tail, traverse the committed express edge and advance the stage.

Do NOT replan the express sequence in the middle of a packet.

==================================================
2. EXPRESS PATH PLANNING: KEEP THE CURRENT COMPLETE-CANDIDATE MODEL
==================================================

Reuse the current precomputed candidate paths:

- at most K=8 candidates per ordered (src,dst);
- each candidate uses 0, 1, or 2 directed express links;
- every inter-express Mesh portion is represented by its Manhattan-minimal segment;
- no repeated-router / looping candidates;
- pure Mesh candidate always included.

Because Mesh segments now use local minimal adaptation instead of fixed XY,
their static hop count is unchanged, so the existing static path latency L(P)
remains valid.

At packet injection choose ONE express sequence and commit to it.

==================================================
3. TWO EXPRESS-SEQUENCE SELECTION POLICIES ONLY
==================================================

Implement/retain exactly these two main policies.

--------------------------------------------------
Policy A: GlobalExpressReservation (main algorithm)
--------------------------------------------------

For each directed express edge e:

    q[e] = current real congestion / waiting-flit count
           at that express output

    r[e] = packets already committed to use e
           but not yet arrived at e's tail

For candidate P:

    C_qr(P) =
        L(P)
        + sum_{e in P ∩ E_express} (q[e] + r[e])

Choose:

    P* = argmin_P C_qr(P)

Then immediately reserve every express edge in P*:

    r[e] += 1

When the packet reaches the tail of e and starts entering that express resource:

    r[e] -= 1

If the packet abandons the committed express route because it enters the
escape path, release all reservations for unconsumed express edges.

Do NOT add theta/lambda/rho or any other tuning parameter.

--------------------------------------------------
Policy B: RandomCandidate (routing baseline)
--------------------------------------------------

Use the EXACT SAME candidate set P_sd.

At injection, uniformly randomly choose one candidate from P_sd using the
experiment seed.

Do not use q or r to choose the candidate.

If the chosen candidate contains express edges, the packet still commits to
the same source-route/stage mechanism.

For accounting consistency, reservations may still be tracked for
instrumentation, but MUST NOT influence RandomCandidate's route choice.

This baseline answers:

    Are gains from q+r actually due to congestion-aware express planning,
    rather than merely having multiple possible source routes?

OPTIONAL ONLY IF ALREADY TRIVIAL TO RUN:
StaticMinCandidate = choose candidate with minimum L(P).
Do not spend implementation time on this if it is not already easy.

==================================================
4. ESCAPE VS NO-ESCAPE: CONTROL VARIABLES CAREFULLY
==================================================

For every main adaptive configuration, support two VC modes:

A. Escape:
    total = 4 VCs/vnet
    3 adaptive VCs
    1 reserved XY escape VC

    retain the existing escape timeout, currently 32 cycles.

    If a packet switches to escape:
        - release reservations for all unconsumed express edges;
        - abandon the remaining express sequence;
        - route directly to FINAL destination using Mesh XY;
        - never return from Escape to Adaptive.

B. NoEscape:
    total hardware budget must ALSO be 4 VCs/vnet.
    Use all 4 as ordinary adaptive VCs.
    There is no escape transition.

This comparison deliberately gives NoEscape one extra adaptive VC, so Escape
is NOT being helped by extra VC hardware. State this explicitly in the report.

Everything else must be identical:
- topology
- candidate generation
- local Mesh adaptive policy
- local congestion metric
- path-selection policy
- packet size
- buffers
- arbitration
- seeds
- measurement window

For NoEscape runs:
- add robust no-progress/deadlock detection;
- do not let gem5 hang forever;
- record the cycle of detected no-progress/deadlock;
- record delivered packets before failure.

The purpose is to see whether Escape becomes useful at high load, not to make
NoEscape look artificially bad.

==================================================
5. TOPOLOGIES
==================================================

Main comparison:

1. pure Mesh
2. Random Express
3. Uniform-Hybrid / current greedy optimized placement

If the existing Handcrafted topology requires essentially zero extra work,
it may be included as a secondary row, but do not let it expand the report.

For Random Express:
- do NOT cherry-pick one favorable placement;
- use at least 3 random placement seeds under the same B, r, d_min constraints;
- report mean ± std across placement seeds when comparing topology quality.

Keep:
    8x8
    B=16
    extra degree <=1
    current d_min
    ideal express latency = 1

Do NOT vary B/r/latency/network size in this round.

==================================================
6. FAIR ROUTING COMPARISON
==================================================

The pure-Mesh baseline must now use the SAME LocalMeshAdaptive primitive that
Express routes use inside each Mesh segment.

Therefore the fair headline comparison is:

    Mesh + LocalMeshAdaptive
vs
    Random Express
        + q+r express planning
        + LocalMeshAdaptive segments
vs
    Uniform-Hybrid
        + q+r express planning
        + LocalMeshAdaptive segments

Run the same comparison separately for:

    Escape
and
    NoEscape.

Do NOT use the old LegacyLocalAdaptive as the headline Mesh baseline if its
logic differs from the newly shared LocalMeshAdaptive primitive.

LegacyLocalAdaptive may remain as a compatibility/debug reference only.

==================================================
7. INJECTION-RATE SWEEP: ACTUALLY REACH SATURATION
==================================================

The previous round only tested configured rates up to 0.16, where accepted
throughput was still almost perfectly linear and max utilization was low.

This round MUST extend until a clear knee / saturation / instability is seen.

First run a quick 1-seed coarse sweep approximately over:

    0.08
    0.12
    0.16
    0.20
    0.24
    0.28
    0.32
    0.40

If accepted throughput is still linear and latency remains flat at 0.40,
continue upward (e.g. 0.50, 0.60, ...) until one of these is clearly observed:

- accepted throughput stops scaling linearly;
- latency rises sharply;
- link utilization approaches saturation;
- NoEscape loses progress.

Do not assume the command-line injectionrate equals actual packet throughput.
Continue recording actual injected/ejected packets/flits.

After locating the knee for each important family, run a denser sweep around
the common knee region.

Final main points:
- warmup ~= 20,000 network cycles
- measurement ~= 100,000 network cycles
- 3 traffic seeds minimum

Use shorter runs only for coarse scanning.

==================================================
8. EXPERIMENT MATRIX
==================================================

Do NOT take an unnecessary full Cartesian product.

Stage 1: find congestion regime
--------------------------------
Use:
    Uniform-Hybrid + q+r + LocalMeshAdaptive + Escape
    Mesh + LocalMeshAdaptive + Escape

Coarse-scan rates until a clear knee is found.

Stage 2: headline topology comparison
-------------------------------------
At the relevant full rate range compare:

    Mesh + LocalMeshAdaptive
    Random + q+r + LocalMeshAdaptive
    Uniform-Hybrid + q+r + LocalMeshAdaptive

First with Escape ON.

This is the MAIN result.

Stage 3: routing-policy ablation
--------------------------------
On the SAME Uniform-Hybrid topology compare:

    RandomCandidate + LocalMeshAdaptive
    q+r + LocalMeshAdaptive

with Escape ON.

This tests whether congestion-aware express-sequence planning matters.

Stage 4: escape ablation
------------------------
For:
    Mesh + LocalMeshAdaptive
    Uniform-Hybrid + q+r + LocalMeshAdaptive

compare:
    Escape
    NoEscape

especially near and above saturation.

If runtime permits, add Random topology here only as secondary data.

==================================================
9. KEY METRICS
==================================================

For every final run record:

- configured injection rate
- actual injected packet throughput
- accepted/ejected packet throughput
- average packet latency
- P95 packet latency if available
- average hops
- path stretch if available
- max directed-link utilization
- P95 directed-link utilization
- express traversals per delivered packet
- fraction of delivered packets using:
    0 express
    exactly 1 express
    exactly 2 express
- escape fraction
- average cycles before escape
- deadlock/no-progress flag
- deadlock/no-progress cycle for NoEscape
- per-express-edge selected packet count
- average/max q[e]
- average/max r[e]

==================================================
10. WHAT RESULTS MATTER
==================================================

Do NOT force an expected ordering.

The desired-but-not-assumed topology result is:

    throughput(
        UniformHybrid + q+r + LocalMeshAdaptive
    )
    >
    mean throughput(
        Random + q+r + LocalMeshAdaptive
    )
    >
    throughput(
        Mesh + LocalMeshAdaptive
    )

But Random is NOT required to beat Mesh.

The actual success criteria are:

A. Topology value:
    Uniform-Hybrid must beat pure Mesh under the SAME LocalMeshAdaptive policy,
    especially in peak/saturation throughput.

B. Placement value:
    Uniform-Hybrid should beat the mean Random placement under the SAME routing.

C. q+r routing value:
    On the SAME Uniform-Hybrid topology,
    q+r should outperform RandomCandidate near congestion if global express
    congestion awareness is useful.

D. local Mesh adaptation value:
    Express performance should no longer be artificially limited by fixed XY
    routing inside ordinary Mesh segments.

E. escape value:
    Escape may have little effect at low load but should preserve progress /
    avoid severe degradation at high load if adaptive channel dependencies
    become problematic.

If NoEscape performs better at all tested rates and never loses progress,
report that honestly.

==================================================
11. MAIN FIGURES ONLY
==================================================

Generate only the following headline figures:

Figure 1:
    accepted throughput vs actual offered throughput
    for:
        Mesh
        Random Express (mean across placements)
        Uniform-Hybrid
    all using q+r where applicable,
    LocalMeshAdaptive,
    Escape ON.

Figure 2:
    average latency vs actual offered throughput
    for the same three configurations.

Figure 3:
    Uniform-Hybrid only:
        RandomCandidate
        q+r
    same LocalMeshAdaptive + Escape.
    Show throughput and/or latency near saturation.

Figure 4:
    Escape vs NoEscape near/high load for:
        Mesh
        Uniform-Hybrid q+r

Optionally one near-saturation link-utilization heatmap if it clearly explains
a result.

Do NOT generate dozens of plots.

==================================================
12. REPORT FORMAT: VERY SHORT
==================================================

Produce one concise Markdown report, target <= 1500 Chinese characters
excluding tables.

Structure:

# More Complete Validation

## 1. Experimental change
Maximum 6-8 lines:
- shared LocalMeshAdaptive
- q+r vs RandomCandidate
- Escape vs NoEscape
- wider rate range

## 2. Main topology result
One table and at most one paragraph.

Required table:

| Routing/topology | Peak accepted throughput | Knee | Latency near knee | Max util |
|---|---:|---:|---:|---:|
| Mesh + LocalAdaptive | | | | |
| Random + q+r + LocalAdaptive | | | | |
| UniformHybrid + q+r + LocalAdaptive | | | | |

## 3. Routing and escape ablation
One compact table:

| Comparison | Main observation |
|---|---|
| q+r vs RandomCandidate | |
| Escape vs NoEscape (Mesh) | |
| Escape vs NoEscape (Hybrid) | |

## 4. Verdict
Answer only these four questions:
1. Does Uniform-Hybrid beat Mesh under fair local adaptation?
2. Does Uniform-Hybrid beat Random placement?
3. Does q+r beat random candidate selection?
4. Does escape matter at high injection?

Do NOT include:
- long implementation diary
- code walkthrough
- TODO list
- literature survey
- detailed correctness discussion unless something failed
- dozens of secondary metrics
- unsupported speculation

Save raw CSVs, detailed logs, and secondary plots separately.

==================================================
13. CORRECTNESS CHECKS BEFORE FULL RUN
==================================================

Before expensive sweeps verify:

1. Pure Mesh LocalMeshAdaptive and Express-segment LocalMeshAdaptive call the
   SAME implementation.

2. A Mesh segment always decreases Manhattan distance by exactly one per hop.

3. 0/1/2-express committed source routes still execute correctly.

4. q/r reservation accounting still returns to zero after drained runs.

5. Escape releases all unused express reservations.

6. Escape never returns to Adaptive.

7. NoEscape contains no hidden XY/escape fallback.

8. Escape and NoEscape both use exactly 4 VCs/vnet total.

9. Actual injected/ejected throughput accounting is consistent.

Then run the wider Uniform validation and report the actual result, including
negative results.

我这里特意做了两个取舍。

第一，纯 Mesh 和 Express 中的 Mesh segment 必须调用同一个 LocalMeshAdaptive 实现。这样最后如果 UniformHybrid > Mesh，不能被质疑为“你给 Express 用了更好的 Mesh routing”。而且因为这种 adaptation 始终只在 productive directions 中选择，所以每个 Mesh segment 的 hop count 仍等于 Manhattan distance，原来的 \(L(P)+\sum(q+r)\) source-level cost 不需要改。

第二，Escape/NoEscape 我建议按相同硬件预算比较：两者都是 4 VC；Escape 是 3 adaptive + 1 escape，NoEscape 是 4 adaptive。这其实还稍微偏袒 NoEscape，因为它多一个 adaptive VC。如果高 injection 下 Escape 仍然保持 progress、而 NoEscape 堵死，那么这个结果会比较有说服力。低 injection 下 NoEscape 反而略快也完全正常，不应该把它包装成失败。

这轮最关键的图其实只有两张：同一 local adaptation 下 Mesh / Random / Hybrid 的 throughput–load 曲线，以及 Hybrid 上 q+r vs RandomCandidate 的曲线。 只要把真正的 saturation knee 跑出来，基本就能判断整个 Budgeted Express-Mesh 的主 premise 到底成不成立。

今天 11:11
362ab60b-937c-4c9f-8b5c-537a7d9bea92.md
文件
帮我分析一下这次的报告

这次报告比前两轮有价值很多，因为现在终于进入了真正的高负载区，而且 Mesh / Express 内部 Mesh 段已经用了统一的 local adaptation，控制变量更干净。但结果也暴露出一个比较根本的问题：目前限制性能的主要因素似乎已经不是“express link 本身堵不堵”，而是 adaptive/escape 体系在高负载下发生了非常严重的 congestion collapse。 所以现在还不能简单把结果解读为“Hybrid placement 不行”。

1. 先看最核心的 headline：Hybrid 目前并没有明显 beating Mesh

在统一 GlobalExpressReservation + LocalMeshAdaptive + Escape 下：

rate	Mesh	Random p1	Hybrid
0.40	0.1998	—	—
0.50	0.1419	0.1482	0.1436
0.60	0.1418	0.1502	0.1438

所以单看 0.50/0.60：

Hybrid 相比 Mesh 只有约 +1.2% / +1.4%；
representative Random 反而是 +4.4% / +5.9% 左右。

因此目前完全不能讲“greedy placement 显著优于 Mesh”，更不能讲“greedy > random”。

Random placement 做了三个 placement seeds 后，rate=0.60 的平均 throughput 是 0.1461、std 0.0065；Hybrid 是 0.1438。两者差距甚至小于 Random placement 自身的波动量级。

所以目前对 placement 最准确的描述是：

B=16 下，Hybrid 没有显示出稳定优于 Random 的 runtime throughput 优势。

2. 但是 0.50 之后发生的不是普通“饱和”，而是非常严重的 collapse

这一点我觉得比 placement 排名本身更重要。

Mesh：

rate 0.40：throughput 0.1998，latency 17.7 cycles；
rate 0.50：throughput 掉到 0.1419，latency 爆到 24122 cycles。

Hybrid 也是两万多 cycle latency。

一个行为良好的 NoC 接近 saturation 时通常应该类似：

throughput
   ________
  /
 /

即 throughput 到达 plateau，latency 快速增加。

你们现在更像：

throughput
   /\
  /  \____
 /

即输入增加以后，实际 delivery rate 反而显著下降。

这说明 0.50 以后不是单纯“link bandwidth 用满”，而是在发生：

长时间 blocking；
channel dependency；
escape fallback；
severe HOL congestion；
tester/network backpressure；

这类系统级 collapse。

而且 max link utilization 在 rate 0.50 甚至只有大约 0.39–0.51，并没有接近 1。

这一点特别重要：

如果真的是物理 link capacity 饱和，应该看到关键 links utilization 非常高；现在 latency 两万 cycle、throughput collapse，但 link utilization 只有四五成，说明大量时间 packet 是“卡住了”，而不是 links 在满负荷有效工作。

因此当前测到的“saturation throughput”，很大程度是在测 routing/VC/escape 的 congestion stability，而不只是 topology capacity。

3. Escape 数据基本证实了这一点

rate=0.50/0.60 时：

Mesh：约 42% delivered packets 进入 Escape；
Hybrid：约 40%；
Random p1：约 39%。

40% 是非常高的比例。

这意味着到了 headline high-load regime：

每 10 个最终送达的 packet 中，大约 4 个已经放弃原来的 adaptive/source route，切换到了底层 XY escape network。

所以此时你以为自己在比较：

Mesh vs Random Express vs Hybrid Express

实际上有相当一部分运行时间是在比较：

谁能更有效地从 adaptive blocking 中逃回 XY

这会严重稀释 express topology 的贡献。

更强的证据是 NoEscape：

Mesh 3/3 deadlock watchdog；
Hybrid 3/3 deadlock watchdog；

都在约 50k network cycles 左右发生。

这说明 当前 LocalMeshAdaptive 本身在高负载下非常容易形成 cyclic dependency；Escape 不是偶尔备用，而是在大量救火。

所以我现在不会把：

“Hybrid 只比 Mesh +1%”

简单解释成 topology 无效。

更准确是：

高负载 regime 已经被 adaptive-routing deadlock pressure / escape behavior 主导，topology effect 被压得很小。

4. 还有一个非常关键的新现象：负载越高，你们反而越不用 express

这个结果我觉得值得重点看。

Hybrid：

rate 0.40：12.48% packet 使用 1 条 express；
rate 0.50：只剩 6.74%。

Random：

15.43% → 6.80%。

两条 express 的使用率更是基本为 0。

也就是说：

正是在 express links 最应该帮助提高 saturation throughput 的时候，routing algorithm 反而把 93% 左右的 packet 赶回纯 Mesh path。

这非常可能解释为什么 Express topology 最后只能比 Mesh 好一点点。

而且 \(q,r\) 数据告诉我们为什么。

Hybrid：

rate .40：\(q\) peak avg 0.281，\(r\) 1.556；
rate .50：\(q\) 反而降到 0.096，但 \(r\) 升到 2.258。

也就是：

express link 自己的 queue 根本没堵起来；真正变大的，是“已经决定要去 express、但迟迟还没到 express tail”的 packet 数。

这是一个非常有信息量的结果。

5. 这很可能说明 \(r_e\) 正在间接感知 Mesh gateway congestion

考虑一条：

src --Mesh--> A --express--> B

的路线。

如果 A→B express 本身很空，那么：

\(q_e\approx0\)。

但大量 packet 卡在：

src → ... → A

的 Mesh corridor 中，还没到 A。

于是它们一直算作：

\(r_e\)。

结果就是：

$$ q_e\text{ 小},\qquad r_e\text{ 大}. $$

这正是你们数据观察到的 pattern。

换句话说，之前你担心：

“source-level cost 不知道 Mesh 上堵不堵。”

现在看来 \(r_e\) 居然在一定程度上成为了一个 proxy for congestion on the way to the express gateway。

这是好事。

但也有副作用。

因为 cost 是：

$$ L(P)+\sum(q_e+r_e), $$

一旦很多 packet 在去 express 的路上走得很慢，\(r_e\) 上升，新 packet 就开始回避这条 express route。

于是形成：

Mesh access corridor congested
        ↓
reserved packets 到不了 express
        ↓
r_e 上升
        ↓
new packets avoid express
        ↓
express usage 从 12% 掉到 6%

因此你们现在并不是“所有 packet 高负载时都涌向 express”。

恰恰相反：

q+r 很可能过早地把流量从 express 上赶走了。

6. 这也解释了为什么 express q 本身一直很低

如果 routing selector 看到 \(r_e\) 一高就不再给这条 express 分新 packet，那么 express queue 自然不会长起来。

所以：

q 很低

不能解释为：

express capacity 很充足，所以 topology 没有 bottleneck。

也可能只是：

reservation feedback 太强，express 在真正拥堵前就被 route selector 主动限流了。

这个现象值得之后专门检查。

7. q+r beating RandomCandidate 是一个正结果，但现在这个 baseline 偏弱

RandomCandidate 在同一个 candidate set 中完全随机选 route：

长窗口 rate .50 accepted throughput 约 0.076；
而 q+r Hybrid 大约 0.144；
并且 RandomCandidate 多个 seed 直接触发 deadlock watchdog。

所以可以确定：

不能无脑随机使用 path diversity；congestion-aware route selection 明显有价值。

这是一个真实结果。

但我不会把它包装成：

“q+r 是一个非常优秀的 adaptive algorithm。”

因为 Uniform RandomCandidate 从 \(K\) 条候选里等概率抽，可能会选：

较长 route；
不必要的 2-express route；
更容易产生 channel dependency 的 route。

它本身是一个比较弱的 baseline。

更严谨的 routing baseline 应该是比如：

StaticMinCandidate

即：

$$ P=\arg\min L(P), $$

完全不知道 congestion，但至少总选择静态最短 candidate。

这样才能回答：

global \(q+r\) information 本身到底贡献多少。

目前的 RandomCandidate 更多说明：

random routing 很差。

8. Random placement 的结果反而暴露了 placement objective 的问题

旧的 Hybrid placement 是根据 static ASPL / predicted bottleneck 做出来的。

但现在实际 runtime 已经变成：

local Mesh adaptive；
express q+r source routing；
40% escape；
high-load source-route abandonment。

这和 offline placement 时假设的 traffic model 已经差得非常远。

因此：

offline 认为是“好 topology”的 Hybrid，不一定是这种 runtime routing 下的好 topology。

这很可能就是为什么 random placement 有时能赢。

尤其 rate .50，Random placement variance 极大：

\(0.2077\pm0.0477\)。

这说明：

express link 放在哪里极其重要，但你们现在的 Hybrid objective 并没有稳定找到 runtime 最好的位置。

这是一个比“express link 没用”更准确的 interpretation。

9. 我认为本轮最重要的四个结论
① Express premise 还没有被推翻

Hybrid 在高负载下仍略好于 Mesh，但收益只有约 1%，目前不能称为显著成功。

② 当前真正的主问题已经从“herding”转成“adaptive network stability”

NoEscape 全死，Escape 使用率约 40%，latency 达 2–3 万 cycles。

所以高负载 performance 被 escape/deadlock pressure 主导。

③ q+r 有效避免了 express 过载，但可能已经过于保守

高 load express usage 反而减半，而 \(r\) 上升、\(q\) 很低。

这非常值得进一步研究。

④ 当前 Greedy placement 没有显示出优于 Random

这是目前对原 project story 最大的负面结果。

而原因未必是 greedy idea 不行，更可能是：

offline placement objective 与实际 adaptive/escape runtime objective 严重 mismatch。

10. 还有一个 correctness 问题不能忽视

报告最后说：

“实现已经 deadlock-free”目前不能声称。

这判断是对的。

因为 RandomCandidate 即使开启 Escape，长窗口仍然出现 watchdog failure。

如果你们的 escape-channel argument 真正实现正确，理论上任意 adaptive dependency cycle 都应该最终通过 escape network 被打破。

所以这里至少有三种可能：

gem5 watchdog 把极长 congestion stall 误判为 deadlock；
Escape transition 在某些状态下没有真正可用；
source-routing / VC occupation 与 escape 的交互破坏了你们原本认为的 deadlock-free 条件。

这个问题在最终 report 前必须搞清楚。

如果让我现在评价整个项目状态

我会说你们已经得到一个比最初预期更有意思、但 story 发生变化的结果。

最初想讲：

Greedy express placement
→ 更多 path diversity
→ adaptive routing 利用它
→ throughput 明显提高。

目前实际看到的更像：

sparse express links 确实创造额外 routes；
q+r 能避免盲目拥塞 express；
但高负载时 reservation 和 gateway congestion 让 routing 大量回退到 Mesh；
同时 local minimal adaptation 本身产生严重 cyclic blocking，需要大量 Escape；
所以最终 topology gain 被 routing stability 问题压制，而 static greedy placement 也没有稳定胜过 random。

我认为现在最不应该做的是继续加新的 routing heuristic。 下一步最有价值的是先搞清两个问题：为什么约 40% packet 必须 Escape，以及为什么高负载时 express usage 从约 12–15% 掉到约 7%。只要这两个现象解释清楚，基本就能判断这个方向是还能救，还是应该把最终 project story改成“express routing 中 congestion-awareness / correctness tradeoff”的研究。