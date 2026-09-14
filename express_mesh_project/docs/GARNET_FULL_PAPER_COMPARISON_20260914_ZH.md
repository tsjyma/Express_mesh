# Garnet 全量论文实验与 standalone 对比（2026-09-14）

## 1. 数据完整性与统计口径

下载包包含 **9240 个唯一 Garnet 执行 / 9640 个逻辑样本**；最终 `failures.json` 为空。最初的 15 份 failure log 是重试前遗留文件，不能解释成最终仍有 15 个缺失命令。

下表中的“有效完成”首先要求 `termination_reason=simulate_limit`，并排除已经确认参数错误的样本。`deadlock_panic` 是 Garnet watchdog 对长期无进展的中止，统计时作为删失样本，不填成 throughput=0，也不混入 latency 均值。

| section | 逻辑样本 | 有效完成 | watchdog/无效配置 |
|---|---:|---:|---:|
| cross | 512 | 512 | 0 |
| escape | 1600 | 1475 | 125 |
| escape_deadlock_contrast | 40 | 20 | 20 |
| escape_off | 1280 | 1277 | 3 |
| information_ablation | 48 | 48 | 0 |
| main | 5056 | 5056 | 0 |
| random_distribution | 800 | 783 | 17 |
| routing_ablation | 112 | 110 | 2 |
| scaling | 192 | 144 | 48 |

## 2. 数值一致性

Garnet 与 standalone 按 section、traffic、topology、rate、seed 和测量窗口逐项对齐；比较表先在各报告组内取完成样本均值，再计算相对差。Cross 的 R=0.80 是本次 Garnet 新增数据，没有 standalone 对照。

| 比较集合 | 组数 | T误差中位/P90 | T≤3% | L误差中位/P90 | L≤5% |
|---|---:|---:|---:|---:|---:|
| 主实验（R=0.40/0.70/0.80） | 48 | 0.2%/2.3% | 44/48 | 1.2%/20.1% | 36/48 |
| 主实验全部曲线点 | 632 | 0.1%/1.6% | 602/632 | 0.6%/14.0% | 481/632 |
| Cross matrix（R=0.65） | 84 | 0.4%/1.5% | 81/84 | 1.2%/6.5% | 73/84 |
| Routing ablation | 28 | 2.4%/8.2% | 17/28 | 2.6%/8.4% | 22/28 |
| Information ablation | 12 | 0.1%/1.3% | 12/12 | 3.1%/9.8% | 10/12 |
| Escape timeout | 80 | 0.0%/11.3% | 70/80 | 0.2%/13.4% | 62/80 |
| Scaling | 42 | 0.3%/33.2% | 31/42 | 5.9%/47.5% | 19/42 |

主实验三个表的结论稳定：R=0.70 和 0.80 的八个 traffic--rate 组合全部保持严格的 Mesh < Random expectation < Greedy < SA throughput 排序。48 个表格级 throughput 对比中 44 个在 3% 内；四个较大差异都来自 Tornado 的 Greedy/SA 饱和点（最大 13.7%）。latency 对微时序更敏感，36/48 在 5% 内，最大差异 52.5%，但没有改变上述排序。

Cross matrix 反而比 standalone 更符合目标：在 R=0.65 和 0.80，Uniform、Tornado、BitComp、CutStress 四列均由 matched SA 严格取胜，Mixture 几何均值也由 Mixture SA 严格取胜。

## 3. 不符合预期或必须加注的结果

1. **Length-aware Random 生成错误（生成器已修复）。** 原下载包中 `16x16-B256-L4` 的 20 个 Random 样本与理想 1-cycle 行完全重复，`8x8-B64-L2` 的 Random 也仍是 latency=1。原因是 standalone 支持运行时覆盖 latency，而 Garnet 只读取 topology JSON；Greedy/SA JSON 已经正确，只有 Random 错。生成器现已把 `ceil(wire_length/wire_per_cycle)` 烘焙进Garnet 输入；8x8 的四个样本已定向补跑，16x16 的错误数据则从 arXiv 图中剔除并标作 N/A，避免把错误配置或选择性完成样本当作 Random expectation。

2. **Random-placement 百分位的旧对照口径有误（已修复）。** Garnet 中有 392/400 个 layout 可形成 topology mean；Greedy 超过 388/392 个可测 layout （约 99.0th percentile），而 SA 超过全部 392 个。另有 8 个 layout 两个 seed 均被 watchdog 删失、1 个仅完成一个 seed。Random layout 使用 seeds 7/8，因此固定 Greedy/SA 也必须取相同 seeds；旧图误用了主表 seeds 5--8 的 Greedy 均值，并把可重复但 topology-specific 的 seed-6 拥塞分支混入阈值。配对后 Greedy 为 0.29344、约处于 99.0th percentile，与 standalone 的 98.8th percentile 一致；两套 Random 分布的中位数也分别为 0.28340/0.28349。这不是 placement/routing 实现差异，而是后处理时没有配对 seed。

3. **Watchdog 不是命令失败，也不自动等于已证明的协议死锁。** 210 个 panic 中，125 个来自 Tesc=8/16 的高负载 escape sweep，43 个来自 scaling Random/SoC overload，17 个来自 400-layout Random 分布，2 个来自 LocalMeshAdaptive，3 个来自正常 escape-off 高负载；这些更适合解释为长期 starvation/拥塞删失。只有受控两-VC escape-off 对照显示 20/20 panic、escape-on 20/20 完成，并从Garnet VC dump 中恢复出 13-channel 闭环，构成直接的死锁证据。

4. **Cross 中有一个明显的亚稳态离群组。** Uniform、R=0.65 的 Mixture Greedy 在 Garnet 中只有 0.2440，而 standalone 为 0.2859；Garnet 的 seed 7 尤其低至 0.1970。相同 topology 在 R=0.80 又回到 0.2807，说明 delayed feedback 进入了不同的拥塞吸引域，而不是 topology 容量随 offered load 单调变化。它不影响 matched-SA/mixture-SA winner，但这一个点不应被用作模型精确一致性的证据。

5. **Scaling 的 Random 柱并非统一精度的 expectation。** 原下载包的 16x16 行使用 10 个 layout×2 seed；论文固定取编号 1--5 的五个 layout×2 seed，而多数 8x8 行只使用一个 Random layout×4 seed。原 16x16-B256/L4 的 20 个错误配置样本全部剔除，当前五-layout SoC Random 只有 2/10 完成；图中必须给出有效完成比例，并把其余有删失的柱高解释成 completed-run conditional mean。

6. **2-flit scaling 是最大的正常模型差异。** Garnet 的四类 topology throughput 均比 standalone 低约 35%，latency 高 46--58%，但仍保持 Mesh < Random < Greedy < SA。Garnet 的 flits/packets 恰为 2，证明命令行宽度确实生成了两个 flit；差异来自 standalone 把整包作为一个对象移动，只以两周期 link service 和 tail-credit delay 近似多-flit，无法表示 head/body/tail 同时占据不同 router/VC 的 wormhole 状态。Garnet 中该行 escape fraction 为 0，standalone 则为 0.8--3.5%，也是这种 packet-atomic 近似改变等待判定的直接证据。因此不能用 standalone 做 packet-size 的定量替代。

7. **Information ablation 的物理传播代价比旧值大。** Garnet 中 physical 相对 instant 的 throughput 最大下降 2.9%，latency 最大增加 12.0%；效果仍属较小，但旧文的“0.9%/5.1%以内”必须更新。

## 4. Garnet scaling 摘要

以下 throughput/latency 仅对完成样本取均值；C/N 明示完成比例。因此有删失的 Random/SoC 数字偏乐观，不能视作无条件期望。

| 配置 | Mesh | Random | Greedy | SA |
|---|---:|---:|---:|---:|
| 8x8-B32 | 0.2663/17866 (4/4) | 0.2899/14926 (4/4) | 0.2865/17872 (4/4) | 0.3029/14124 (4/4) |
| 8x8-B64 | 0.2663/17866 (4/4) | 0.3062/14192 (4/4) | 0.3232/12282 (4/4) | 0.3386/9247 (4/4) |
| 8x8-B128 | 0.2661/22923 (4/4) | 0.3803/9005 (4/4) | 0.4030/6943 (4/4) | 0.4070/6185 (4/4) |
| 8x8-B32-VC8 | 0.3924/1199 (4/4) | 0.3917/1189 (4/4) | 0.3996/19 (4/4) | 0.3996/19 (4/4) |
| 8x8-B64-L2 | 0.2663/17866 (4/4) | 0.2918/15906 (4/4) | 0.3107/13996 (4/4) | 0.3148/12410 (4/4) |
| 8x8-B64-2flit | 0.1188/46629 (4/4) | 0.1326/45778 (4/4) | 0.1424/44649 (4/4) | 0.1525/42351 (4/4) |
| 16x16-B256 | 0.2102/3257 (4/4) | 0.2183/867 (1/10) | 0.2202/710 (4/4) | 0.2247/29 (4/4) |
| 16x16-B256-L4 | 0.2102/3257 (4/4) | N/A (0/10) | 0.2201/461 (4/4) | 0.2247/31 (4/4) |
| 16x16-B256-SoC | 0.1845/9915 (3/4) | 0.1665/11622 (2/10) | 0.2200/5951 (4/4) | 0.2488/2672 (4/4) |

## 5. 总结

可以用 Garnet 数据支持核心结论，但表述必须收窄为：主实验高负载下Greedy 高于 Random expectation，SA 再稳定提高 Greedy；SA 的 Random 分布优势和 cross-matrix matched 优势很强。配对 seed 后，Greedy 约位于 Garnet 可测 Random layout 的 99.0th percentile；仍不能隐去高压 ablation/scaling 的 watchdog 删失。修正/剔除 length-aware Random 后，其余异常均能由饱和非线性、watchdog 删失或standalone 的多-flit 简化解释，没有发现主 routing/escape 数据路径的新 bug。
