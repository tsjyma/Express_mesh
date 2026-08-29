# Escape deadlock diagnostics

These experiments distinguish three different conditions that the original
Garnet NI watchdog conflates: an NI unable to inject, global network
no-progress, and failure to drain a finite packet set.

## Method

All primary cases use Uniform traffic, source routing, 20,000 warmup + 100,000
measurement cycles, three traffic seeds, a 50,000-cycle threshold, followed by
up to 500,000 cycles with traffic generation disabled.  The simulator records
the last flit movement and packet delivery at an NI watchdog and declares
global no-progress only after the whole network has made no flit movement for
50,000 cycles.  A run passes the strongest dynamic check when every request
generated before injection stops is eventually delivered.

Reproduce the 48 cases with:

```bash
python3 run_escape_diagnostics.py --workers 4
```

Every standalone run also enumerates all source/destination Escape XY paths,
constructs the VC3 channel-dependency graph, and rejects the configuration if
an Express channel is used or a topological sort finds a cycle.  Successful
results report `escape_cdg_acyclic: true`.

## Main result

| Mode | Cases | NI watchdog | Global no-progress | Fully drained |
|---|---:|---:|---:|---:|
| q+r + Escape, Mesh/Hybrid, rates 0.40/0.50/0.60 | 18 | 0 | 0 | 18 |
| RandomCandidate + Escape, Mesh/Hybrid, rates 0.40/0.50/0.60 | 18 | 0 | 0 | 18 |
| Legacy NoEscape, Mesh/Hybrid, rate 0.50 | 6 | 6 | 6 | 0 |
| Corrected-four-VC NoEscape, Mesh/Hybrid, rate 0.50 | 6 | 0 | 0 | 6 |

The legacy NoEscape cases are genuine global stalls in this cycle model.  The
NI watchdog fires near cycle 50,200--51,900; global no-progress is confirmed
roughly 100--170 cycles later.  At the NI warning, the network has already gone
about 49,800 cycles without either a flit move or a delivery.

Correcting the router-output VC range makes all six finite workloads drain.
This does not prove arbitrary adaptive routing deadlock-free; it demonstrates
that the checked-in NoEscape result is dominated by the VC-range bug and is
not a fair 3-adaptive+1-escape versus 4-adaptive comparison.

## Why RandomCandidate is close to the NI threshold

The longest consecutive NI busy streaks *before traffic generation stops* are:

| Mode/topology | rate 0.40 | rate 0.50 | rate 0.60 |
|---|---:|---:|---:|
| q+r Mesh | 12 | 15,419 | 20,916 |
| q+r Hybrid | 10 | 17,234 | 40,857 |
| RandomCandidate Mesh | 6 | 14,731 | 21,894 |
| RandomCandidate Hybrid | 41,650 | 43,662 | 49,548 |

RandomCandidate Hybrid rate 0.60 comes within 452 cycles of the fixed 50,000
threshold while the network is still live and later drains.  Small differences
in Garnet event/RNG interleaving can therefore explain why particular Garnet
seeds cross the threshold.  A fixed per-NI threshold is not a reliable
deadlock classifier in this saturated regime.

## Constructive false-positive example

With q+r Hybrid rate 0.50 and `escape_timeout=0`, seeds 1 and 2 trigger the NI
watchdog at cycles 50,415 and 81,726.  At both exact cycles:

- a flit moves (`cycles_since_flit_move = 0`);
- a packet is delivered (`cycles_since_delivery = 0`);
- global no-progress is false;
- after injection stops, all 1.918 million generated packets drain by cycles
  229,374 and 226,913.

These are definitively overload/starvation warnings, not routing deadlocks.
Immediate escape is also undesirable: it overloads the single escape VC.

## Timeout/performance sensitivity

Reproduce the 108 q+r cases with:

```bash
python3 run_escape_timeout_sweep.py --workers 4
```

Hybrid three-seed mean accepted throughput is:

| timeout | rate 0.40 | rate 0.50 | rate 0.60 |
|---:|---:|---:|---:|
| 0 | 0.1998 | 0.0818 (2 NI warnings) | 0.1233 |
| 8 | 0.1999 | 0.2497 | 0.1249 |
| 16 | 0.1999 | 0.2498 | 0.1291 |
| 32 (current) | 0.1998 | 0.1613 | 0.1412 |
| 64 | 0.1998 | 0.1632 | 0.1624 |
| 128 | 0.1998 | 0.1033 | 0.1036 |

The saturated system is highly nonlinear, so changing the timeout can change
which seeds fall onto a congested branch.  A deadlock proof only requires a
finite timeout, not timeout zero.  The lowest-risk correctness change is
therefore to keep timeout 32 and make the existing transition invariants
explicit; timeout tuning should be treated as a separate performance study
and confirmed in Garnet.

## Supported conclusion

For the Phase-3 scope (vnet 0, one-flit packets, consuming destinations), the
dynamic evidence supports the following design: VC0--VC2 are adaptive, VC3 is
escape-only, a blocked head irreversibly enters VC3 after a finite timeout,
and VC3 follows coordinate-only Mesh XY without Express links.  Under fair
arbitration the escape channel-dependency graph is acyclic, giving the formal
routing-deadlock argument.  The experiments support the premises but do not
replace the CDG proof or a corresponding finite-drain run in Garnet.
