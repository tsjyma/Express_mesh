# V5 Garnet 一致性审计：deterministic traffic 地址哈希错误

## 0. 最终结论

V4 报告中“Garnet 长测否定 traffic-aware placement”的结论无效。问题不在
ASPL placement，也不在 standalone routing，而在 Garnet 实验命令遗漏了
`--xor-low-bit=0`：gem5 的通用 memory-controller XOR hashing 把 tester
指定的 bit-complement/tornado 目的 router 改写了。

修复地址映射后，rate 0.8、warmup 20k、measurement 100k、3 seeds、escape
开启、NI watchdog 50k、K=8 policy 4、budget 64/max-degree 1 的 Garnet 长测为：

| Traffic/topology | Throughput mean | Latency mean | Hops | Express/packet | Escape delivered |
|---|---:|---:|---:|---:|---:|
| Bit-complement Mesh-XY | 0.128327 | 39755 | 6.548 | 0 | 34.5% |
| Bit-complement Mesh per-hop adaptive | 0.06617（2 个完成 run） | 50524 | 5.782 | 0 | 52.4% |
| Bit-complement Random | 0.176410 | 33243 | 5.495 | 0.540 | 17.1% |
| Bit-complement Uniform-ASPL | 0.183101 | 33467 | 6.104 | 0.715 | 19.8% |
| **Bit-complement-aware ASPL** | **0.275805** | **17391** | **3.480** | **0.696** | **0.9%** |
| Tornado Mesh-XY | 0.211563 | 23719 | 3.886 | 0 | 5.1% |
| Tornado Mesh per-hop adaptive | 0.211570 | 23725 | 3.886 | 0 | 5.1% |
| Tornado Random | 0.223649 | 21139 | 3.848 | 0.090 | 4.8% |
| **Tornado-aware ASPL** | **0.379366** | **2459** | **2.502** | **0.598** | **0.0%** |

表中的 Mesh per-hop adaptive 是额外的无 source-route baseline，不使用 K=8
policy 4；其余 express 配置和 Mesh-XY baseline 使用同一 committed-route
框架，以保证 mesh segment、VC 与 escape 机制一致。

相对同预算 Random，matched traffic-aware ASPL 的结果为：

- Bit-complement：throughput **+56.3%**，latency **-47.7%**；
- Tornado：throughput **+69.6%**，latency **-88.4%**。

相对 Mesh-XY，分别为 throughput **+114.9% / +79.3%**，latency
**-56.3% / -89.6%**。正式 Mesh-XY/Random/matched-ASPL 的 18 个 run 全部
正常到达 simulation limit，没有 watchdog 或 no-progress 记录。

作为额外 baseline，per-hop minimal adaptive 的 Bit-complement seed 2 在默认
50k threshold 触发 NI `Possible network deadlock` watchdog，另外两个 seed
完成且吞吐约 0.06617；把 threshold 提高到 150k 后三个 seed 都能完成。这仍
只能判定为严重 injection starvation，不足以证明全网 channel deadlock，但
无论按稳定性还是吞吐，它都不是比 matched-ASPL 更强的 Mesh baseline。

## 1. 原 Garnet 表实际跑了什么

`GarnetSyntheticTraffic::generatePkt()` 先构造 block address：

```text
block = intended_destination + 64 * per_source_packet_number
paddr = block << 6
```

因此 64 个 directory 的目标编号位于物理地址 `[11:6]`。但 Ruby 的默认
`--xor-low-bit=20` 为 64 个 directory 建立了六个 XOR mask：

```text
actual_destination = paddr[11:6] XOR paddr[25:20]
                   = intended_destination
                     XOR packet_number[13:8]
```

即每个源连续 256 个包仍去同一目的，随后目标被异或到另一个 directory；
经过 16384 个包会扫遍全部 64 个目的。这也解释了旧 `stats.txt` 中大量
source/destination cell 的计数恰好是 `256`。

旧 100k Mesh runs 的网络注入矩阵直接证明了这一点：

| 标称 traffic | 每源非零目的数 | 到达标称目的的比例 |
|---|---:|---:|
| Bit-complement | 63.13 / 64 | 1.30% |
| Tornado | 62.97 / 64 | 1.35% |

真正的 permutation traffic 必须是每源恰好一个目的、命中率 100%。旧结果
近似于一个带 256-packet temporal burst 的全目的扫描，而不是标称 traffic。
所以旧表比较的是“为 bit-complement/tornado 优化的 topology 在错误的近似
uniform traffic 上表现如何”，不能用于否定 traffic-aware placement。

## 2. 为什么旧表会出现反常排序

### 2.1 标成 Bit-complement 的 Uniform-ASPL 为什么略低于 Mesh

这个现象也不是“真正的 Bit-complement 不适合 ASPL”。旧 run 的实际 demand
是上述带 temporal burst 的近似全目的扫描。Uniform-ASPL 虽把平均 hops 从
Mesh 的 5.17 降到 4.28，并使用约 0.436 express traversals/packet，却把更多
traffic 汇聚到少数 express endpoint 及其 mesh ingress/egress corridor。policy 4
只观察 express reservation/VC occupancy，看不到这些 mesh corridor 的排队；
delivered escape fraction 因而从 Mesh 的 8.1% 升到 11.3%。结果是路径更短，
但 bottleneck service rate 略低，吞吐 0.1976 对 0.2043。

这只解释旧的错误 workload 在旧 topology/routing 上为何形成该数值，不能
推导真正 Bit-complement 的结论。修复映射后的同一 100k × 3-seed 长测中，
Uniform-ASPL 吞吐为 0.1831，已经高于 Mesh-XY 的 0.1283 和 Random 的
0.1764；matched Bit-complement-aware ASPL 则进一步达到 0.2758。

### 2.2 Bit-complement-aware ASPL 反而比 Mesh 差

Bit-complement-aware topology 把 wire budget 用在跨越二维镜像距离的边上。
如果真实 demand 是 `(x,y)->(7-x,7-y)`，这些边能显著缩短路径；但旧 Garnet
中 packet 的实际目的每 256 包变化一次并最终遍历全网，这些专用边不再与
demand 对齐。policy 4 仍会因为静态 hop reduction 选择部分 express candidate，
却经常把 packet 带到对当前实际目的无利的 endpoint/corridor。

因此旧 run 只有约 0.249 express traversals/packet，平均 hops 4.243，escape
delivery 10.9%，吞吐 0.1671。修复映射后 express usage 升到 0.696，平均 hops
降到 3.480，escape 降到 0.9%，吞吐升到 0.2758。这是 demand/topology 是否
匹配导致的机制变化，不是调权重制造的结果。

### 2.3 Tornado-aware ASPL 为什么尤其差

Tornado-aware topology 主要是每行内 length-3/4 的横向 express link，专门
服务 `(x,y)->((x+3) mod 8,y)`。错误的全目的扫描包含大量跨行目的，但这些
横向 endpoint 仍被 source-route candidates 使用，造成集中而无收益的导流。
旧 run 的 link-utilization CV 达 0.513、escape delivery 11.9%、平均 hops
4.488；与真正 tornado 无关的 Uniform-ASPL 反而更适合这个错误的泛化 demand。

修复后所有 packet 都保持在正确行内，matched edges 直接承担 0.598
express traversals/packet，平均 hops 降到 2.502，escape 为 0，吞吐达到
0.3794，已接近 tester 在 2:1 clock ratio 下的 offered throughput 0.4。

## 3. standalone 与 Garnet 是否一致

使用完全相同的 topology、XY mesh segments、K=8 policy 4、weights
0.375/0.625、20k+100k、3 seeds 后：

| Traffic/topology | Standalone th / lat | Garnet th / lat | Throughput 差异 |
|---|---:|---:|---:|
| Bit-complement Mesh | 0.127969 / 39792 | 0.128327 / 39755 | -0.28% |
| Bit-complement Random | 0.176539 / 33246 | 0.176410 / 33243 | +0.07% |
| Bit-complement-aware ASPL | 0.273650 / 17393 | 0.275805 / 17391 | -0.78% |
| Tornado Mesh | 0.210176 / 23923 | 0.211563 / 23719 | -0.66% |
| Tornado Random | 0.222390 / 21157 | 0.223649 / 21139 | -0.56% |
| Tornado-aware ASPL | 0.380641 / 2415 | 0.379366 / 2459 | +0.34% |

六个 matched 配置的 throughput 误差都小于 1%，latency 误差都小于 2%。所以：

1. `express_noc.cpp` 没有导致 V4 traffic-aware 设计过拟合；
2. 之前观察到的 standalone/Garnet 巨大差异来自两边实际运行的 traffic
   不同；
3. standalone 仍只保证 Phase-3 的 vnet0、单 flit、8x8 scope，不能外推到
   data packets 或其他协议。

## 4. 修复与防回归

`run_phase3_measurement_v2.py` 现在固定向 Garnet 传入：

```text
--xor-low-bit=0
```

这不是修改 gem5 的正确 memory hashing；它只是在 synthetic destination
实验中关闭会破坏显式目的编码的通用 channel hash。

runner 的 result schema 升为 6，并解析
`ctrl_traffic_distribution.n<src>.n<dest>`。对 bit-complement、tornado 和
CutStress，每次正常结束都要求所有实际注入 packet 100% 到达定义中的目的，
否则整次 run 直接失败，避免再次生成“标签正确、traffic 错误”的汇总表。

本轮不需要引入全图 Dijkstra，也没有为结果额外修改 routing。最终 winner
仍是可实现的原设计：离线 traffic-weighted greedy ASPL placement；运行时每个
source/destination 只保留 K=8 个静态候选，NI 用 express reservation 和本地
express output VC occupancy 选一个候选，mesh segment 使用 XY，32-cycle 后只
允许单向转入独立 XY escape VC。

## 5. 原始结果

- 修复后、默认 50k watchdog 的正式 Garnet 长测：
  `results/garnet_v5_fixed_long_d50k/`；
- 150k watchdog 的补充诊断：`results/garnet_v5_fixed_long/`；
- 同配置 standalone 长测：`standalone_noc/results/audit_v5/`；
- 旧的错误 Garnet runs 保留在 `results/garnet_v4_final/`，只用于复现和审计，
  不再作为 performance evidence。
