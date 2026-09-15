# Garnet 全量论文实验与 standalone 对比（2026-09-15）

## 1. 数据完整性与统计口径

原下载包包含 **9240 个唯一 Garnet 执行 / 9640 个逻辑样本**；补跑包对其中 161 个配置重新测量，补跑最终为 161/161 成功。两个包内残留的 failure log 都是成功重试前的旧日志，最终 `failures.json` 均为空。

下表中的“有效完成”首先要求 `termination_reason=simulate_limit`，并排除已经确认参数错误的样本。补跑的 200k no-progress 阈值长于完整 120k-cycle 运行，因此它给出固定窗口数据，而不是稳态或 deadlock-free 证明。未补跑的 `deadlock_panic` 仍按删失样本处理。

| section | 逻辑样本 | 有效完成 | watchdog/无效配置 |
|---|---:|---:|---:|
| cross | 512 | 512 | 0 |
| escape | 1600 | 1600 | 0 |
| escape_deadlock_contrast | 40 | 20 | 20 |
| escape_off | 1280 | 1280 | 0 |
| information_ablation | 48 | 48 | 0 |
| main | 5056 | 5056 | 0 |
| random_distribution | 800 | 783 | 17 |
| routing_ablation | 112 | 112 | 0 |
| scaling | 192 | 172 | 20 |

## 2. 数值一致性

Garnet 与 standalone 按 section、traffic、topology、rate、seed 和测量窗口逐项对齐；比较表先在各报告组内取完成样本均值，再计算相对差。Cross 的 R=0.80 是本次 Garnet 新增数据，没有 standalone 对照。

| 比较集合 | 组数 | T误差中位/P90 | T≤3% | L误差中位/P90 | L≤5% |
|---|---:|---:|---:|---:|---:|
| 主实验（R=0.40/0.70/0.80） | 48 | 0.2%/2.3% | 44/48 | 1.2%/20.1% | 36/48 |
| 主实验全部曲线点 | 632 | 0.1%/1.6% | 602/632 | 0.6%/14.0% | 481/632 |
| Cross matrix（R=0.65） | 84 | 0.4%/1.5% | 81/84 | 1.2%/6.5% | 73/84 |
| Routing ablation | 28 | 2.3%/8.2% | 17/28 | 2.6%/8.4% | 22/28 |
| Information ablation | 12 | 0.1%/1.3% | 12/12 | 3.1%/9.8% | 10/12 |
| Escape timeout | 80 | 0.0%/8.0% | 71/80 | 0.2%/15.4% | 63/80 |
| Scaling | 54 | 0.5%/27.5% | 35/54 | 6.7%/47.2% | 21/54 |

主实验三个表的结论稳定：R=0.70 和 0.80 的八个 traffic--rate 组合全部保持严格的 Mesh < Random expectation < Greedy < SA throughput 排序。48 个表格级 throughput 对比中 44 个在 3% 内；四个较大差异都来自 Tornado 的 Greedy/SA 饱和点（最大 13.7%）。latency 对微时序更敏感，36/48 在 5% 内，最大差异 52.5%，但没有改变上述排序。

Cross matrix 反而比 standalone 更符合目标：在 R=0.65 和 0.80，Uniform、Tornado、BitComp、CutStress 四列均由 matched SA 严格取胜，Mixture 几何均值也由 Mixture SA 严格取胜。

## 3. 不符合预期或必须加注的结果

1. **Length-aware Random 生成错误（已修复并补跑）。** 原下载包中 `16x16-B256-L4` Random 仍使用 unit-latency JSON。原因是 standalone 可在运行时覆盖 latency，而 Garnet 只读取 topology JSON。生成器现已把 `ceil(wire_length/wire_per_cycle)` 烘焙进输入；论文固定的 p1--p5 共 10 个样本全部按正确配置补跑并纳入，未采用的 p6--p10 旧样本继续标作无效。

2. **Random-placement 百分位的旧对照口径有误（已修复）。** Garnet 中有 392/400 个 layout 可形成 topology mean；Greedy 超过 388/392 个可测 layout （约 99.0th percentile），而 SA 超过全部 392 个。另有 8 个 layout 两个 seed 均被 watchdog 删失、1 个仅完成一个 seed。Random layout 使用 seeds 7/8，因此固定 Greedy/SA 也必须取相同 seeds；旧图误用了主表 seeds 5--8 的 Greedy 均值，并把可重复但 topology-specific 的 seed-6 拥塞分支混入阈值。配对后 Greedy 为 0.29344、约处于 99.0th percentile，与 standalone 的 98.8th percentile 一致；两套 Random 分布的中位数也分别为 0.28340/0.28349。这不是 placement/routing 实现差异，而是后处理时没有配对 seed。

3. **补跑把 watchdog 删失与固定窗口性能分开。** 125 个 Tesc=8/16、3 个普通 escape-off、2 个 LocalMeshAdaptive 和 31 个论文采用的 scaling 样本在 200k 阈值下全部到达固定窗口末尾；低 timeout 和 16x16 Random 的性能仍明显 collapse。普通 escape-off 中还有一个 R=0.8 样本在整个 100k measurement 内零交付。只有受控两-VC escape-off 对照显示 20/20 停止、escape-on 20/20 完成，并从Garnet VC dump 中恢复出 13-channel 闭环，构成直接的死锁证据。

4. **Cross 中有一个明显的亚稳态离群组。** Uniform、R=0.65 的 Mixture Greedy 在 Garnet 中只有 0.2440，而 standalone 为 0.2859；Garnet 的 seed 7 尤其低至 0.1970。相同 topology 在 R=0.80 又回到 0.2807，说明 delayed feedback 进入了不同的拥塞吸引域，而不是 topology 容量随 offered load 单调变化。它不影响 matched-SA/mixture-SA winner，但这一个点不应被用作模型精确一致性的证据。

5. **16x16 Random 的完整均值显著低于 Mesh。** 论文固定取 p1--p5 五个 layout×2 seed；补跑后三个 16x16 Random cohort 均为 10/10 固定窗口数据。其 throughput 分别为 0.1388、0.1846 和 0.0941，均低于对应 Mesh。逐 topology 对照显示部分 layout 在 Garnet 和 standalone 都 collapse，另有少数 seed 位于不同拥塞吸引域；这不是统一方向的参数漏传，而说明随机加边缺乏鲁棒性。

6. **2-flit scaling 是最大的正常模型差异。** Garnet 的四类 topology throughput 均比 standalone 低约 35%，latency 高 46--58%，但仍保持 Mesh < Random < Greedy < SA。Garnet 的 flits/packets 恰为 2，证明命令行宽度确实生成了两个 flit；差异来自 standalone 把整包作为一个对象移动，只以两周期 link service 和 tail-credit delay 近似多-flit，无法表示 head/body/tail 同时占据不同 router/VC 的 wormhole 状态。Garnet 中该行 escape fraction 为 0，standalone 则为 0.8--3.5%，也是这种 packet-atomic 近似改变等待判定的直接证据。因此不能用 standalone 做 packet-size 的定量替代。

7. **Information ablation 的物理传播代价比旧值大。** Garnet 中 physical 相对 instant 的 throughput 最大下降 2.9%，latency 最大增加 12.0%；效果仍属较小，但旧文的“0.9%/5.1%以内”必须更新。

## 4. Garnet scaling 摘要

以下 throughput/latency 对论文固定 cohort 取均值；C/N 明示到达固定窗口末尾的样本数。三个 16x16 Random 行均包含完整的五-layout×两-seed。

| 配置 | Mesh | Random | Greedy | SA |
|---|---:|---:|---:|---:|
| 8x8-B32 | 0.2663/17866 (4/4) | 0.2899/14926 (4/4) | 0.2865/17872 (4/4) | 0.3029/14124 (4/4) |
| 8x8-B64 | 0.2663/17866 (4/4) | 0.3062/14192 (4/4) | 0.3232/12282 (4/4) | 0.3386/9247 (4/4) |
| 8x8-B128 | 0.2661/22923 (4/4) | 0.3803/9005 (4/4) | 0.4030/6943 (4/4) | 0.4070/6185 (4/4) |
| 8x8-B32-VC8 | 0.3924/1199 (4/4) | 0.3917/1189 (4/4) | 0.3996/19 (4/4) | 0.3996/19 (4/4) |
| 8x8-B64-L2 | 0.2663/17866 (4/4) | 0.2918/15906 (4/4) | 0.3107/13996 (4/4) | 0.3148/12410 (4/4) |
| 8x8-B64-2flit | 0.1188/46629 (4/4) | 0.1326/45778 (4/4) | 0.1424/44649 (4/4) | 0.1525/42351 (4/4) |
| 16x16-B256 | 0.2102/3257 (4/4) | 0.1388/12852 (10/10) | 0.2202/710 (4/4) | 0.2247/29 (4/4) |
| 16x16-B256-L4 | 0.2102/3257 (4/4) | 0.1846/3437 (10/10) | 0.2201/461 (4/4) | 0.2247/31 (4/4) |
| 16x16-B256-SoC | 0.1808/10074 (4/4) | 0.0941/27838 (10/10) | 0.2200/5951 (4/4) | 0.2488/2672 (4/4) |

## 5. 总结

Garnet 数据继续支持核心结论：主实验高负载下 Greedy 高于 Random expectation，SA 再稳定提高 Greedy；cross matrix 和 400-Random study 也保持。Scaling 的更准确结论是每行都保持 Mesh < Greedy <= SA，而 Random 在三个 16x16 cohort 中明显退化。补跑消除了论文采用 scaling cohort 的选择性完成，但 2-flit 的系统差异和饱和微时序敏感性仍须保留说明。
