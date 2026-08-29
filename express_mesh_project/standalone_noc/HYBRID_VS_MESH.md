# Hybrid q+r + Escape versus Mesh baselines

This screening uses 20,000 warmup + 100,000 measurement cycles, three traffic
seeds, rates 0.02--0.60, and Uniform, CutStress, and Hotspot traffic.  Hybrid
uses committed source routes, q+r candidate selection, local-adaptive Mesh
segments, and timeout-32 Escape.  The Mesh baselines are deterministic,
non-source adaptive, and source-route/local-adaptive (the topology-isolating
baseline).  High-load Hotspot runs continue after NI warnings so every result
contains the full measurement window.

## Conclusion

Hybrid is not universally better.  At low load accepted throughput is fixed by
offered traffic, so only latency can improve.  At high load the result depends
strongly on traffic, routing baseline, and Escape pressure.

### Uniform

| rate | Hybrid | Mesh deterministic | Mesh adaptive | Mesh source/local-adaptive |
|---:|---:|---:|---:|---:|
| 0.40 throughput | 0.1998 | 0.1917 | 0.2000 | 0.1998 |
| 0.40 latency | 16.6 | 2149.9 | 17.6 | 17.5 |
| 0.50 throughput | 0.1613 | 0.2126 | 0.1395 | 0.1390 |
| 0.50 latency | 10012.8 | 7098.3 | 23749.7 | 23518.3 |
| 0.60 throughput | 0.1412 | 0.2178 | 0.1384 | 0.1383 |
| 0.60 latency | 30771.6 | 13688.0 | 31965.7 | 30446.6 |

Against adaptive/source-local Mesh, Hybrid improves saturated throughput by
about 2--16% in this standalone model.  Against deterministic Mesh it is 24%
lower at rate 0.50 and 35% lower at rate 0.60.  At rates <=0.30 throughput is
the same and Hybrid reduces latency by roughly 4% versus adaptive/source-local
Mesh.  The checked-in Garnet same-routing results are less dramatic: Hybrid
versus Mesh q+r improves Uniform throughput by 0.05%, 1.15%, and 1.37% at rates
0.40, 0.50, and 0.60, and reduces latency by 5.6%, 10.9%, and 3.8%.

### CutStress

Hybrid chooses no Express edge in any tested rate/seed.  Throughput and latency
are consequently identical to the Mesh source/local-adaptive baseline, within
noise of the other Mesh baselines.  The current Hybrid placement does not help
the row-mirrored cut workload.

### Hotspot

| rate | Hybrid throughput | Mesh deterministic | Mesh adaptive | Mesh source/local-adaptive |
|---:|---:|---:|---:|---:|
| 0.12 | 0.0599 | 0.0576 | 0.0598 | 0.0600 |
| 0.20 | 0.0601 | 0.0590 | 0.0601 | 0.0602 |
| 0.40 | 0.0600 | 0.0591 | 0.0600 | 0.0600 |
| 0.60 | 0.0603 | 0.0591 | 0.0603 | 0.0603 |

Hybrid is roughly tied with adaptive/source-local Mesh and about 1--2% above
deterministic Mesh after saturation.  At rate 0.12 it has 9.1% higher latency
than the topology-isolating Mesh source/local-adaptive baseline (257 versus
236 cycles).  Express use falls from about 8% of delivered packets at low load
to about 1.2% at rate 0.60, because the hotspot's final ingress/ejection
bottleneck cannot be bypassed by the selected long links.

## Mechanism

- The physical Express links help only source/destination pairs aligned with
  their placement.  They are unused for CutStress and rarely useful for the
  two-center Hotspot.
- At Uniform saturation, q+r increasingly rejects Express candidates:
  traversals fall from about 15% per delivered packet at low load to 6% at
  rate 0.60.
- q samples a short link staging queue, not downstream VC demand, so candidate
  scores do not reliably predict the true bottleneck.
- Source routing changes the routing algorithm as well as the topology.  Its
  Mesh segments are local-adaptive even when the run is labelled
  deterministic.  A pure deterministic Mesh avoids much of the adaptive-cycle
  and Escape pressure, making it a particularly strong Uniform baseline.
- At saturated Uniform load, roughly 31--44% of Hybrid/Mesh-adaptive deliveries
  use the single Escape VC.  Performance is then governed as much by Escape
  capacity and nonlinear congestion state as by the added links.

These standalone results locate mechanisms and counterexamples.  Headline
numbers, especially the Uniform rate-0.50 knee, should be confirmed in Garnet.
