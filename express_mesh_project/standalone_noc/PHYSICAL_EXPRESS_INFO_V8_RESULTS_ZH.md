# 物理闭合的 Express 阻塞信息机制 V8

本文修正 V7 中“q/r 的读取有传播延迟、但 reservation 的写入却瞬时到达远端 express 入口”的不一致。V8 不再把任意 router 直接修改远端全局计数器当作硬件能力，而是明确建模一套 route-setup/registration 协议。旧的 full-information 和 V7 模式均原样保留，V8 通过新的 `--express-reservation-mode registered` 增量启用。

## 1. 最终结论

最终采用的机制为：top-8 committed source routing、mesh segment 固定 XY、escape VC 保持不变，express 信息采用 `distance-gossip / period=1 / base-delay=1 / 4-bit`，reservation 使用 `registered` 协议，评分为

```text
candidate_cost = static_latency + 1.0 * advertised_q
                                  + 0.6 * observed_r
```

这里不再使用 V7 的 hash admission，`express_admission_fraction=1.0`。在 Uniform 与 Tornado、rate 0.7 与 0.8、20 个 Random placement × 3 traffic seeds 的四组 standalone 长测中，throughput 与 latency 都满足观测均值上的 `Mesh < Random expectation < traffic-aware Greedy < SA Pareto`，且所有 run 正常到达 simulation limit，没有 late registration、reservation underflow 或 no-progress。Tornado 的最终 SA 是针对这套 registered 协议重新搜索并经 Garnet 筛选的 `placement_v8/tornado_registered_sa_pareto.json`，它保留了原始 `sa_registered_finalists/finalist_11.json` 的完整副本；下表的 Tornado Greedy/SA 另用 6 个 seed 复测，避免把旧机制下得到的 SA 拓扑继续当作新机制的 winner。

| Traffic / rate | Mesh th / lat | Random expectation th / lat | Greedy th / lat | SA th / lat |
|---|---:|---:|---:|---:|
| Uniform 0.7 | 0.265727 / 3590 | 0.288583 / 2349 | 0.322427 / 1344 | **0.330199 / 945** |
| Uniform 0.8 | 0.266064 / 4976 | 0.288505 / 4237 | 0.322739 / 3483 | **0.329244 / 3021** |
| Tornado 0.7 | 0.209913 / 5593 | 0.222695 / 4864 | 0.330983 / 766 | **0.334039 / 572** |
| Tornado 0.8 | 0.210176 / 6846 | 0.223578 / 6185 | 0.342885 / 2035 | **0.347198 / 1791** |

rate 0.8 时，Greedy 相对 Mesh 的 throughput 提升为 Uniform **21.30%**、Tornado **63.13%**；相对 Random expectation 分别提升 **11.87%** 和 **53.35%**。这表明消除瞬时远端 reservation 写入之后，主要 topology 优势仍然存在。重新搜索的 Tornado SA 相对 Greedy 提高 **1.26%** throughput、降低 **11.98%** latency；收益不是巨大，但方向在 6 个 seed 和两个 rate 上一致。

Garnet 使用相同 routing、相同 topology 文件和相同信息协议做了 rate 0.8、20k warmup + 100k measurement 正式长测。Uniform 的 69 个原始 run 中，p13 seed 1、p16 seed 1、p19 seed 1/2 在默认 50k NI watchdog 下退出，使用 150k threshold 精确补跑后全部到达 simulation limit；聚合时以补跑值替换同一 sample，没有丢弃坏 Random 布局。Tornado 的 Mesh/Random/Greedy 使用 3 个 seed，新 SA 除相同的 seeds 1--3 外又用未参与搜索和首轮筛选的 holdout seeds 4--6 做了长测。

| Garnet rate 0.8 | Mesh th / lat | Random expectation th / lat | Greedy th / lat | SA th / lat |
|---|---:|---:|---:|---:|
| Uniform（3 seeds） | 0.266210 / 17886 | 0.283949 / 16296 | 0.323386 / 12235 | **0.330633 / 10570** |
| Tornado（seeds 1--3） | 0.211563 / 23719 | 0.225865 / 21283 | 0.368482 / 3769 | **0.369664 / 3349** |

Uniform Greedy 相对 Mesh/Random 的吞吐提高 21.48%/13.89%，延迟降低 31.60%/24.92%；SA 相对 Greedy 又提高 2.24% 吞吐、降低 13.61% 延迟。Tornado Greedy 相对 Mesh/Random 的吞吐提高 74.17%/63.14%，延迟降低 84.11%/82.29%；新 SA 相对 Greedy 再提高 0.32% 吞吐、降低 11.16% 延迟。Tornado holdout seeds 4--6 的长测为 Greedy `0.368373 / 3797`、新 SA `0.369757 / 3358`；合并 6 seeds 后分别为 `0.368427 / 3783` 和 `0.369711 / 3353`，即 SA 提高 0.35% 吞吐、降低 11.37% 延迟。所有 6 个新 SA 长测均到达 simulation limit，late registration 总数为 0。

如果更重视控制面带宽，可以只把 advertisement period 从 1 改为 2，其余机制、routing 和 topology 全部不变。P2 的 Garnet 正式 selected-placement 长测中，Uniform Greedy/SA 为 `0.321364 / 12659`、`0.328763 / 10854`，Tornado 6-seed Greedy/SA 为 `0.369595 / 3706`、`0.370324 / 3337`，仍保持 SA Pareto；Uniform 的 20-Random 单-seed Garnet screen 也保持 `Mesh 0.266099 < Random mean 0.271043 < Greedy 0.320534 < SA 0.328102`。standalone 中，P2 将 rate 0.8 Greedy 的广告接收事件从约 16.7/19.6 降到 12.2/14.7 次“源更新等价量”每 cycle（Uniform/Tornado），约下降 25%--27%，但并没有消除全网 multicast 成本。由于 P2 尚未完成 20 Random × 3 seeds 的 Garnet 正式矩阵，本文把 P1 作为证据最完整的性能配置，把 P2 作为当前最可信的低控制流量折中，而不把两者混成一组结果。

## 2. 完整的硬件故事

每条有向 express link 的入口 router 是该方向状态的唯一 authority。`q` 是入口 express output 上已占用的 adaptive VC 数，`r` 是已经在入口登记、但 packet 尚未穿过该 express link 的未来需求数。其他 router 既不能瞬时读取入口，也不能直接修改入口的 `r`。

source 在 injection 时从 top-8 候选中选出 committed route。若 route 含 express link，source 立即只在自己的本地表中增加 `local_pending[source][express_id]`，随后发出一个窄 route-setup control record。setup 沿已经承诺的数据路径向前传播：每经过一个 mesh hop 增加一 cycle，经过前一条 express link 时计入该 link 的传播 latency；到达目标 express 入口后，入口才真正执行 `r++`。因此 setup 必须先物理到达，代码中不存在 source 对远端 counter 的瞬时写入。

入口登记后返回 ACK。source 在 ACK 到达以前，选路时使用 `advertised_r + own_local_pending`，从而能立刻看到自己刚刚制造的需求，又不会假装知道其他 source 的新 reservation。ACK 与第一份包含该登记的周期性 advertisement 对齐；当它能传播回 source 时，本地 pending 才清除，所以 `local_pending` 与延迟广告之间没有低估的空窗。若 packet 正常穿过 express link，入口本地执行 `r--`；若 packet 因 timeout 单向转入 escape，cancel record 从当前 router 沿 mesh 传播回尚未使用的 express 入口，只有 cancel 到达后才执行远端撤销。cancel 可能先于 setup 到达，token 状态机会把这种重排解释为“登记到达时直接作废”，不会产生负计数。

q/r advertisement 由 express 入口产生，4-bit q 和 4-bit r 只在量化值变化时发送；接收 router 保存最后一个值。传播延迟为 `1 + Manhattan(entry, observer)` cycles，最远为 15 cycles。V6 的 16--18 条无向 express link 对应 32--36 个方向，因此每个 router 的 q/r payload 状态约为 256--288 bit，另加 valid bit。硬件上 setup、ACK、cancel 和 q/r change 可以编码为带小型 source-local sequence tag 的 16-bit control word，通过 mesh/express link 旁的窄 sideband 逐跳传递；它不是一条要求瞬时全局 fanout 的组合路径。

当前仿真明确建模了传播时间、注册/ACK/cancel 的先后顺序、source-local pending 和入口 token 状态，但 control word 不与 data VC 竞争，也没有进一步建模 sideband 自身的排队。因此这个实现是“可构造的独立控制 overlay”的性能模型，而不是已经证明控制网络面积、功耗和最坏拥塞都可忽略。尤其不能仅凭 8-bit q/r payload 就称整个广播网络很窄：还要计入 link ID、multicast 复制和每跳负载。正式报告应把这点列为限制，不能把它说成零成本机制。

276 个 standalone final run 中，registration/ACK/cancel 合计开销按 delivered packet 归一化后，Greedy 在 Uniform 0.7/0.8 为 0.787/0.768 个 control record，在 Tornado 0.7/0.8 为 0.981/0.879；SA 均不超过 1.0 个 record/packet。全部 276 个 run 的 `reservation_late_registrations` 总和为 0。Garnet 正式长测中，Uniform Greedy/SA 的 control record 分别为 0.798/0.749 个每 packet；Tornado 6-seed Greedy/新 SA 为 0.982/1.014，late registration 同样全部为 0。压力广告方面，rate 0.8 的 Greedy 每 network cycle 平均产生约 16.7 次 Uniform、19.6 次 Tornado 的量化状态变化，之后仍需 multicast 到订阅 router；这说明控制 overlay 的带宽不是可以忽略的小数。V8 证明的是协议时序可以物理闭合且数据面性能可保留，不是已经完成控制面的面积/能耗最优设计。

Tornado 的旧 `sa_tempering` placement 是在瞬时全局 reservation 语义下优化的；换成 registered 协议后，Garnet 长测为 `0.367128 / 4279`，确实低于 Greedy 的 `0.368482 / 3769`。这不是信息协议自身破坏 Greedy 优势，而是 placement objective 与运行机制不匹配。为避免改变 routing 来“救”旧拓扑，本轮固定了 top-8、权重、escape 和全部信息参数，仅用 registered standalone 重新做 parallel-tempering SA：4 replicas、2 restarts、remove/refill mutation、traffic seeds 1/2，并保存而非只保留标量最优的前 12 个不同候选。Garnet 对 12 个候选做同一短窗口筛选后，`finalist_11` 是唯一同时越过 Greedy throughput 和 latency 的布局；随后通过 seeds 1--3、holdout seeds 4--6 以及两组正式长测。这个过程说明 placement 必须对实际可实现的 routing/information mechanism 优化，也说明只按 standalone 标量第一名选拓扑仍不稳健。SA 的吞吐优势只有约 0.35%，所以应将它表述为可重复的小幅 Pareto 改善，而不是大幅领先。

## 3. 为什么它仍然 adaptive 且不改变 deadlock 证明

adaptive 的部分仍发生在 injection：source 根据自己实际收到的延迟 q/r 和本地 pending，在 top-8 committed routes 中动态选最低代价路径。路径一旦选定，普通 VC 上的 mesh segment 按 XY 前进，不会逐 hop 改写 waypoint；timeout 后只能单向进入专用 escape VC，escape VC 只走纯 mesh XY，且不能返回 adaptive VC。registration token 与 q/r advertisement 只影响“选择哪条既有 route”，不占用数据 VC，也不向数据 channel dependency graph 增加边，因此沿用已审计的 escape deadlock-free 条件。

需要区分两种保证：控制协议的 token 状态机可以保证 reservation 不因 setup/cancel 重排而 underflow，实验中的 late-registration 计数也全部为零；这不等于单靠 q/r 预测能保证任意过载网络不排队。NoC 的无 deadlock 来自 escape VC 的单向转换和 XY CDG，而不是来自 congestion estimate 永远准确。

## 4. 尝试过但未采用的方案

第一类是完全删除 `r`，只传播 express 入口的 q。这套机制最简单，也没有 reservation 写入问题，但 q 只在 packet 已经到达入口并占住 VC 后才反映拥塞，无法表示正在远处沿 committed route 汇聚的需求。提高 q 权重可以修复 Uniform 的部分 Random collapse，却会让 Tornado 的 Greedy/SA 大量放弃高价值 express route；在同时要求四层 hierarchy 和接近 V7 性能时没有找到合格点，因此没有另设 q-only final mode。

第二类是在 registered 协议上继续使用 V7 的 hash admission。50% admission 能在 rate 0.8 恢复完整顺序，但 Tornado Greedy throughput 只有约 0.278，明显低于无 admission 的 0.343；75% admission 仍会被若干坏 Random layout 拉低 Uniform mean。它既损失性能又需要解释人为随机门控，所以最终配置将 admission 恢复为 1.0。

第三类是始终把直接 XY 加成 top-8 之外的第九个候选。这在概念上合理，但 20-Random 长测的 Uniform Random mean 仍只有约 0.232，低于 Mesh；同时 Tornado SA 略低于 Greedy。该尝试已经从正式代码撤掉，只保留结果作为反例。

第四类是降低 advertisement 频率。最初用旧 Tornado SA 测 `period=2` 时，Greedy throughput 为 0.3432、SA 为 0.3423，看似破坏了顺序；换成针对 registered 协议重新搜索的 SA 后，standalone 6-seed rate 0.7/0.8 和 Garnet 6-seed 长测都恢复 Pareto。P2 因而不是失败方案，而是一个有效的带宽/新鲜度折中；它没有被提升为唯一 final，仅因为 Random expectation 的 Garnet 正式证据仍少于 P1，而不是因为性能机制不成立。

还测试了把 q/r 从 4 bit 一起压到 2 bit。8-Random screen 的 Uniform 顺序变成 `Mesh 0.2661 > Random 0.2566 < Greedy 0.3246 < SA 0.3330`，Tornado 则为 `Greedy 0.3505 > SA 0.3453`。2-bit 编码把 2 和 3 合并、把所有大于 3 的 reservation 饱和到同一档，控制 payload 更小但失去了坏 Random topology 所需的节流分辨率，因此没有保留这个 final preset。

最后对 pressure 权重做了长窗口扫描。原 V7 权重 `q=0.625, r=0.375` 在 registered 模式下对坏 Random topology 太激进；整体放大到约 1.5 倍以上可以恢复 Random mean。`q=1.0, r=0.6` 是接近 1.6 倍、数值最简单且四个点全部通过的选择。更保守的 `q=1.25, r=0.75` 也满足排序，但将 Tornado Greedy throughput 降到约 0.298，因此只作为 robustness 对照，不作为最终点。

## 5. 实现、兼容性与复现

standalone 新增 `--express-reservation-mode instant|registered`；默认 `instant` 完整保留 V6/V7 行为。`registered` 只允许与 source-route policy 4、`distance-gossip` 和至少一 cycle base delay 配合，避免生成物理语义不闭合的组合。结果 JSON 新增 registration messages、ACK、cancel 和 late-registration 统计。Garnet 使用同名 SimObject/CLI 参数，并把 packet ID 写入 `RouteInfo` 作为最多两个 express stage 的 token 基础；`InputUnit` 在 express traversal 和 escape cancellation 时把 route、stage 与当前 router 交给网络状态机。

standalone 最终矩阵可用以下命令复现：

```bash
python3 express_mesh_project/standalone_noc/run_partial_info_experiments.py \
  --traffics uniform_random tornado --rates 0.7 0.8 \
  --traffic-seeds 1 2 3 --random-seeds {1..20} \
  --info-configs gossip-p1-d1-b4 \
  --reservation-weight 0.6 --vc-pressure-weight 1.0 \
  --express-reservation-mode registered \
  --warmup-cycles 5000 --measurement-cycles 30000 --workers 4 \
  --output-dir express_mesh_project/standalone_noc/results/physical_info_v8/final
```

合并后的 276-run standalone 原始矩阵位于 `standalone_noc/results/physical_info_v8/final/`；新 Tornado SA 的 6-seed、两个 rate 对照位于 `standalone_noc/results/physical_info_v8/registered_sa_pareto_tornado/`。Garnet Uniform、Tornado 基线矩阵和新 SA 的训练/holdout 正式结果分别位于：

```text
results/garnet_v8_physical_info/formal_uniform/
results/garnet_v8_physical_info/formal_tornado/
results/garnet_v8_physical_info/registered_sa_pareto_formal_tornado/
results/garnet_v8_physical_info/registered_sa_pareto_formal_holdout_tornado/
results/garnet_v8_physical_info/p2_formal_uniform_selected/
results/garnet_v8_physical_info/p2_formal_tornado_selected/
```

standalone 默认 `instant` 回归与 V6 存档的所有旧字段逐项相同；Garnet Uniform Greedy seed 1 的 20k+100k instant 回归也逐项相同，旧/新 throughput 都是 `0.34056828125`、latency 都是 `9326.849207`。因此 V8 是增量机制，没有悄悄改变旧实验默认值。正式实现已通过 standalone self-test、Python offline unit tests、Python bytecode compile、`git diff --check` 和完整 Garnet build；具体测试命令与最终增量 build 状态以本轮交付记录为准。
