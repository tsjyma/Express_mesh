# Budgeted Express-Mesh：Topology–Routing Co-design 方案

## 1. Motivation

标准 2D Mesh 的问题不是总带宽一定不足，而是 shortest-path traffic 往往集中到少数 central/cut links，形成 structural bottleneck；大量其他 links 同时处于低利用率。第一步先用 Mesh + deterministic routing 在 CutStress / Hotspot 等 traffic 下画 link-utilization heatmap，证明 saturation 由少量 bottleneck links 主导，再引出：能否只花有限 wiring budget，用少量 long-range express links 绕开这些结构性瓶颈？

但 express links 只提供了 path diversity，并不保证 shortest-path routing 会正确利用它。高负载时，某条 nominally shortest express route 自身可能已经拥塞，而 slightly longer detour 的实际 latency 更低。因此项目的核心是 **static topology placement + dynamic adaptive routing**：前者决定 path diversity 出现在哪里，后者决定什么时候使用这些路径。

进一步考虑 expected traffic matrix：如果 workload 长期具有 Hotspot / Clustered / Cut-heavy 结构，express links 应该优先服务高需求 source–destination pairs。Traffic-unaware 方法可视为 traffic-aware 方法在 uniform demand 下的特例。

## 2. Topology 与 Physical Constraints

Base topology 为 \(n\times n\) Mesh，节点集合 \(V=\{(x,y):0\le x,y<n\}\)，Mesh edge 集合为 \(E_M\)。加入 bidirectional express edges \(E_X\) 后得到 \(G=(V,E_M\cup E_X)\)。对 \(e=(u,v)\)，定义 Manhattan wire length \(w(e)=|x_u-x_v|+|y_u-y_v|\)。

总 wire budget 满足 \(\sum_{e\in E_X}w(e)\le B\)；每个 router 的额外 express degree 满足 \(\deg_X(v)\le r\)。为避免 optimizer 用大量短边做局部 densification，可要求 \(w(e)\ge d_{\min}\)，主实验先取 \(d_{\min}=3\) 或 4 后用 offline metric 决定。

Express-link latency 跑两个 setting：Ideal 中所有 link latency 均为 1；Length-aware 中 Mesh link latency 为 1，express link latency 为 \(\tau(e)=\lceil w(e)/4\rceil\)，带宽仍保持 1 flit/cycle，用于模拟 pipelined long wire。

主 setting 为 \(8\times8,\ r=1,\ B=16\)。扩展测试 \(12\times12,16\times16\)，\(r\in\{1,2\}\)，\(B\in\{8,16,32,48,64\}\)。跨规模比较时同时报告 normalized budget \(\beta=B/[2n(n-1)]\)，避免 8×8 和 16×16 使用相同绝对 \(B\) 导致不公平。

## 3. Link Placement Algorithm

定义 expected traffic matrix \(D\)，其中 \(D_{sd}\ge0,\ D_{ss}=0,\ \sum_{s\ne d}D_{sd}=1\)。Traffic-unaware 情况取所有 ordered pairs 均匀，即 \(D_{sd}=1/[N(N-1)]\)。

给定 link latency \(\tau\)，定义 traffic-weighted average shortest-path length \(A(G;D)=\sum_{s\ne d}D_{sd}\,d_G(s,d)\)。对 edge \(e\)，令 \(\sigma_{sd}\) 为 \(s\to d\) 的 equal-cost shortest paths 数量，\(\sigma_{sd}(e)\) 为其中经过 \(e\) 的数量，则 predicted edge load 为 \(L_e(G;D)=\sum_{s\ne d}D_{sd}\sigma_{sd}(e)/\sigma_{sd}\)，并定义 \(L_{\max}(G;D)=\max_eL_e(G;D)\)。

所有 greedy 方法统一采用“单位 wire cost 收益”选择下一条合法 edge。ASPL-Greedy 最大化 \(A\) 的下降；Bottleneck-Greedy 最大化 \(L_{\max}\) 的下降；Hybrid-Greedy 最小化归一化目标
\[
J_\alpha(G;D)=\alpha\frac{A(G;D)}{A(G_M;D)}+(1-\alpha)\frac{L_{\max}(G;D)}{L_{\max}(G_M;D)}.
\]
主实验可取 \(\alpha=0.5\)，再做少量 sensitivity。

Baseline 包括 Random Placement 和 Conventional Handcrafted Express Links。后者固定一个对称、traffic-agnostic 的 stride pattern（例如 stride 4 的横纵 express links），并严格满足相同 \(B,r\)。

Traffic-Aware Greedy 直接把 \(D\) 换成目标 workload 的 demand matrix；不需要把“traffic-aware × 所有 objective”全部排列组合。建议先在 uniform \(D\) 下比较 Random / Handcrafted / ASPL / Bottleneck / Hybrid，再只对表现最好的 1–2 个 objective 做 traffic-aware 版本。

## 4. Routing Design

Baseline 为 deterministic weighted shortest-path routing：对每个 \((s,d)\) 固定选择 \(P_{\min}=\arg\min_P\sum_{e\in P}\tau(e)\)，equal-cost 时使用固定 tie-break。实际 Garnet 中所有 Express-Mesh routing 共用同一 escape-VC safety layer，保证比较中的“deterministic / adaptive”只表示 path-selection policy 不同。

Adaptive routing 分两类 candidate generation。UGAL-style 随机采样 waypoint \(w\)，构造 \(SP(s,w)\oplus SP(w,d)\)，作为 generic adaptive baseline。Express-Aware Bounded Detour 则显式构造会使用 express link 的候选路径，并只保留满足 \(L(P)\le\rho L(P_{\min})\) 的路径；主版本限制每条 candidate 最多使用 1 条 express link，扩展可允许 2 条。每次保留 top-\(K\) 个静态代价最小的 candidates。

对候选路径 \(P\)，定义 congestion \(Q(P,t)\)。可比较：UGAL-L 的 first-hop queue；2-hop 加权 queue；full-path max queue 作为 oracle；以及 EWMA historical load。主 routing score 采用 \(C(P,t)=L(P)[1+\lambda Q(P,t)]\)，其中 \(\lambda\) 控制绕路 aggressiveness。

Threshold Hybrid 用 \(\theta\) 控制 adaptive routing 是否启动：若 minimal path 当前 congestion 小于 \(\theta\)，直接走 \(P_{\min}\)；否则在候选集中最小化 \(C(P,t)\)。这样低 load 保留 shortest-path latency，高 load 才使用 detour。

Escape VC 单独使用 underlying Mesh + deterministic XY routing，且只允许 Adaptive→Adaptive、Adaptive→Escape、Escape→Escape，禁止 Escape→Adaptive。可再设置 stall timeout \(T_{\rm esc}\)：adaptive packet 连续等待超过阈值后不可逆进入 escape VC。XY escape subnetwork 的 channel dependency acyclic，因此在标准 fair-arbitration 假设下提供 deadlock freedom。

## 5. Experiment Setup

Traffic 包括 Uniform Random、Transpose、Tornado、Bit Complement、Hotspot、Clustered、CutStress 和 topology-adversarial。CutStress 建议固定为左半边节点向右半边镜像节点发送，用于制造明确 central-cut bottleneck；topology-adversarial 则在给定 topology 上找 predicted load 最大的 edge，只从 shortest path 会经过该 edge 的 \((s,d)\) 中采样，专门检验 adaptive routing 是否真正利用 path diversity。

正式 Garnet comparison 固定 node count、packet size、total VC/buffer budget、per-link bandwidth、router pipeline 和 simulation window。每个 configuration 跑多个 seeds；headline 先集中在 8×8 + Uniform / CutStress / Hotspot，把 story 跑通后再扩展其他 traffic 与规模。

主图横轴为 offered injection rate，纵轴分别为 average packet latency 与 accepted throughput。accepted throughput 定义为 measurement window 内 delivered packets / (node count × cycles)。

## 6. Evaluation Metrics 与呈现

先不跑 gem5，对不同 topology 汇报 wire cost、max degree、diameter、traffic-weighted ASPL、\(L_{\max}\)、P95 predicted load、load imbalance 和 near-shortest path diversity。这样先筛掉明显不合理的 topology，再把代表性方案送进 Garnet。

Runtime 除 latency / throughput 外，重点记录 link-utilization heatmap、Maximum / P95 link utilization、utilization CV、average hops / path stretch、Express-link usage、Non-minimal routing rate、Escape VC fraction。最关键的 mechanism figure 是在同一 CutStress 下并排画 Mesh + deterministic、Express + deterministic、Express + adaptive 三张 heatmap，直接展示 “structural bottleneck → static bypass → dynamic balance”。

| Metric | Mesh | Random | ASPL | Bottleneck | Hybrid |
|---|---:|---:|---:|---:|---:|
| ASPL |  |  |  |  |  |
| \(L_{\max}\) |  |  |  |  |  |
| Peak throughput |  |  |  |  |  |
| Avg latency @ chosen load |  |  |  |  |  |

## 7. Ablation / Comparison

核心 2×2 ablation 使用尽量同构的 routing principle，分离 topology 与 adaptivity 的贡献：

|  | Deterministic | Adaptive |
|---|---:|---:|
| Mesh |  |  |
| Express |  |  |

Placement ablation 固定 \(B,r,\tau\) 比较 Random、Handcrafted、ASPL-Greedy、Bottleneck-Greedy、Hybrid-Greedy。Routing ablation 在同一 Express topology 上比较 deterministic shortest、UGAL-style、Express-Aware Bounded Detour，再分别比较 first-hop / 2-hop / oracle / EWMA congestion signal，以及 \(\lambda,\theta,T_{\rm esc}\) 的 sensitivity。

Traffic-aware 部分建议做一个 cross-workload matrix，用来区分 specialization 与 robustness：

| Optimized for | Uniform | Hotspot | Clustered |
|---|---:|---:|---:|
| Uniform |  |  |  |
| Hotspot |  |  |  |
| Clustered |  |  |  |
| Robust / Hybrid |  |  |  |

Cost sensitivity 重点看 wire budget、extra degree 和 long-link latency。比较时优先寻找“optimized B=16 是否能达到 random B=32”这一类 cost-efficiency 结论，而不是只报绝对性能提升。

## 8. 预期能体现论文品味的结论

最终不预设具体数值，而是希望回答几类机制性问题：saturation 更受 ASPL 还是 max bottleneck load 控制；少量 well-placed wires 是否有 disproportionate gain；placement 是否比单纯增加 budget 更重要；express topology 是否只有配合 adaptive routing 才真正发挥 path diversity；local congestion information 是否已经接近 oracle；traffic-aware topology 在 matched workload 上的收益是否以 robustness 为代价；escape VC 是否能以很低正常开销换来 long-run deadlock freedom。

如果实验出现 negative result 也保留，例如 2-hop 不优于 1-hop、EWMA 因 stale information 更差、extra degree=2 收益很小、length-aware latency 抵消超长 links 等。真正要讲的是 **什么设计在什么条件下有效，以及为什么**，而不是强行让所有 proposed components 都赢。
