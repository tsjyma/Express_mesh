# Pressure-aware committed routing (V2)

> **Historical result.** V2's escape/deadlock argument remains applicable,
> but its budget-16 topology and 0.5/1.0 pressure weights are superseded by
> the budget-64 Stride-ASPL design and 0.375/0.625 weights in
> `ADAPTIVE_ROUTING_V3_RESULTS_ZH.md`.

## Final mechanism

At injection, each packet considers up to eight loop-free routes containing
zero, one, or two directed express links.  Policy 4 chooses the minimum:

`static_latency(P) + 0.5 * sum(reservations[e]) + sum(occupied_vcs[e])`

`reservations[e]` counts packets committed to express edge `e` but not yet
traversing it.  `occupied_vcs[e]` is the number of its three ordinary output
VCs currently allocated.  A reservation is released when the packet enters
that express link, or when it irreversibly changes to escape.  Both signals
are bounded integer counters.  An implementation can sample each express
endpoint and distribute only the small set of express-edge counters
periodically; it does not need global mesh-link state or an online shortest
path/multicommodity-flow solver.

The selected route is committed.  Its ordinary mesh portions use X-then-Y,
not the former per-hop fully adaptive rule.  The overall routing is still
adaptive because the complete route is selected from current pressure at
each injection.  This is deliberately coarse-grained: experiments showed
that repeatedly changing direction inside mesh segments creates cyclic
contention and sends 31--44% of delivered packets into escape, whereas V2
uses about 2--6% near saturation.

## Deadlock argument

VC3 is reserved exclusively for escape whenever escape is enabled.  A packet
may move from VC0--VC2 to VC3 after a finite timeout, but never moves back.
Escape routing uses only base-mesh X-then-Y channels and never an express
link.  Therefore:

1. the escape channel-dependency graph is acyclic;
2. there is no dependency from escape back to an ordinary VC;
3. every blocked ordinary head eventually requests its escape successor;
4. Garnet's round-robin input/output arbiters are fair, and destinations
   consume packets.

Under these assumptions, a dependency cycle composed of ordinary channels
cannot remain closed: its heads eventually leave it for the acyclic escape
subnetwork.  A cycle containing escape is impossible because escape never
depends on an ordinary channel.  This is the standard escape-subnetwork
structure; the ordinary candidate routes themselves need not have an
acyclic union.

The implementation now makes the proof obligations explicit: source-route
mesh segments and escape both have dedicated coordinate-only XY routing;
VC3 is excluded from ordinary allocation and injection; transitions are
irreversible; remaining reservations are released at transition; and Garnet
rejects escape configurations with fewer than two VCs.  The old NoEscape
off-by-one is also fixed so disabling escape really exposes all four VCs.

The claim is network-routing deadlock freedom for the evaluated Garnet
configuration.  It does not mean latency is bounded under an indefinitely
overloaded injection process, nor does a per-NI injection watchdog by itself
constitute a sound deadlock detector.

## Experiments tried

The standalone screen included static, staging-q, q+r, weighted pressure,
random candidate, fully minimal per-hop adaptive, West-first, Odd-Even,
segment-level adaptive XY/YX, and monotonic phase-VC variants.  West-first,
Odd-Even, and adaptive XY/YX saturated substantially earlier.  Strict
phase-VC partitioning removed escape traffic but lost capacity.  Increasing
the candidate table from 8 to 64 had no measurable effect.  Two additional
cut-balanced placements also lost to ASPL-Greedy/Handcrafted and were not
kept.

The stable weight region was reservation weight 0.25--0.75 and express-VC
weight about 1.  Counting switch-arbiter waiters provided no repeatable gain,
so the Garnet implementation omits it.  The selected defaults are 0.5 and 1.

## Confirmed Garnet results

All numbers below are 20,000 warmup + 100,000 measurement cycles and three
traffic seeds.  No run reported deadlock/no-progress.

An additional finite-injection standalone check at Uniform rate 0.8 used
5,000 warmup + 20,000 measured cycles followed by up to 100,000 drain cycles.
All three seeds fully drained, the network became empty, every reservation
returned to zero, and the global progress detector never fired.  A Garnet
NoEscape smoke test after the VC-range fix also completed at throughput
0.24978 with zero escape transitions; this specifically checks that the
fourth ordinary VC is no longer accidentally excluded.

| Traffic / rate | Mesh | Greedy express | Gain vs Mesh | Random placements | Greedy vs Random |
|---|---:|---:|---:|---:|---:|
| Uniform / 0.6 | 0.26546 | ASPL 0.27998 | +5.47% | not rerun at this point | -- |
| Uniform / 0.8 | 0.26622 | ASPL 0.28155 | +5.76% | 0.26803--0.27341 | +2.98--5.04% |
| CutStress / 1.0 | 0.14093 | traffic-aware 0.14667 | +4.08% | 0.14299--0.14493 | +1.20--2.57% |

The secondary placement claim is statistical, not universal.  A short-window
screen of 200 independently generated legal Random placements had mean
throughput 0.27473, median 0.27473, 95th percentile 0.28013, and maximum
0.28399; ASPL-Greedy's same short point was about 0.28222.  Thus Greedy beats
98.5% of this Random sample and clearly beats its mean, but three rare Random
placements beat it.  The best rare placement also remained ahead in a long
standalone validation (0.28471 versus 0.28184).  Consequently the defensible
claim is “the Greedy objective reliably improves over random placement on
average,” not “every random draw is worse.”

At Uniform rate 0.8, ASPL uses about 0.097 express traversals per delivered
packet and 3.5% of delivered packets have entered escape.  Thus the gain is
not produced by flooding the express links or by moving most traffic to the
escape VC.

Hotspot does not materially improve: the two destination/ejection ports are
the bottleneck shared by every topology.  No routing algorithm or placement
of internal express links can create additional endpoint bandwidth.  The
defensible goal is therefore to win when network links are the bottleneck and
not regress materially when endpoints are the bottleneck, rather than claim a
strict win for every possible traffic/configuration.
