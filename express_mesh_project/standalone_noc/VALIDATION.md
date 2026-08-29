# Validation against the repository Garnet results

The standalone engine is a statistical/cycle-model reproduction, not a
bit-for-bit replay of gem5's global event queue.  Both use `std::mt19937_64`,
but routing tie-break calls occur in a different interleaving, so equal seeds
do not imply identical packet traces.  Validation therefore compares three
seed means and internal mechanisms.

## Direct same-command check after a full Garnet build

The repository was built as `build/Garnet_standalone/gem5.opt` and both
engines were run on Mesh, Uniform rate 0.40, seed 1, source-route policy 2,
1,000-cycle warmup + 5,000-cycle measurement.  The standalone routing label
was `adaptive`; with source routing this exercises the same local-adaptive mesh
segments as Garnet (see `AUDIT.md`, finding 2).

| Metric | Standalone | Garnet | Difference |
|---|---:|---:|---:|
| Accepted throughput | 0.199209 | 0.198841 | +0.19% |
| Average packet latency (cycles) | 17.464 | 17.617 | -0.87% |
| Maximum internal-link utilization | 0.4762 | 0.4800 | -0.79% |
| Wall-clock time | 0.17 s | 6.58 s | 38.7x faster |

This is a deliberately short, independent direct run.  The longer table below
uses the checked-in three-seed results and tests behavior near saturation.

## Three-seed validation matrix

Configuration: 20,000-cycle warmup + 100,000-cycle measurement, Uniform,
source route policy 2 (q+r), escape enabled.  “Garnet” is the checked-in
schema-5 aggregate; “fast” is the standalone C++ engine.

| Topology | Rate | Fast accepted | Garnet accepted | Error | Fast latency | Garnet latency | Error |
|---|---:|---:|---:|---:|---:|---:|---:|
| Mesh | 0.40 | 0.199809 | 0.199845 | -0.02% | 17.54 | 17.65 | -0.66% |
| Mesh | 0.50 | 0.139024 | 0.141948 | -2.06% | 23,518 | 24,122 | -2.50% |
| Mesh | 0.60 | 0.138313 | 0.141838 | -2.48% | 30,447 | 31,532 | -3.44% |
| Hybrid | 0.40 | 0.199771 | 0.199944 | -0.09% | 16.61 | 16.67 | -0.35% |
| Hybrid | 0.50 | 0.161283 | 0.143580 | +12.33% | 10,013 | 21,491 | -53.41% |
| Hybrid | 0.60 | 0.141223 | 0.143778 | -1.78% | 30,772 | 30,334 | +1.44% |

All low-load and fully saturated cells are close; Hybrid 0.50 is the explicit
exception.  It sits on a bistable saturation knee: small arbitration/RNG
interleaving changes determine when a seed collapses.  The fast model still
reproduces the mechanism (express use drops and escape use rises), but this
single point must be confirmed in Garnet before it is used as a headline
number.  This is preferable to hiding the mismatch with a topology-specific
calibration constant.

Additional mechanism checks:

- Mesh 0.40 max link utilization: fast 0.474 versus Garnet 0.473.
- Hybrid express traversals/delivered at rates 0.40/0.50 are fast
  12.37%/7.30%; the report records about 12.48%/6.74%.
- Mesh NoEscape rate 0.50 watchdog: fast cycle 50,219; Garnet report cycles
  50,245/50,229/50,235.
- Correcting the NoEscape VC off-by-one changes the fast result from watchdog
  to completion at accepted throughput 0.2500, which is why the current
  Escape/NoEscape conclusion requires rerunning after the source fix.

Recommended use: use the standalone engine for parameter sweeps, ablations,
debugging, and locating knees; confirm the few selected paper points with
Garnet.  Do not claim bit-exact equivalence or use it for configurations outside
the vnet-0 one-flit scope stated in `README.md`.
