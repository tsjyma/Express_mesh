# Standalone Express-Mesh NoC simulator

This directory is a small, dependency-free C++17 reproduction of the part of
Garnet exercised by `run_phase3_measurement_v2.py`.  It deliberately models the
script's actual setting rather than general gem5.  The original 8x8 defaults
remain regression-compatible, while the 20260831 experiment driver also
supports parameterized square meshes:

- square ExpressMesh topologies (including 8x8 and 16x16) from the same JSON
  schema;
- vnet 0, configurable packet serialization, VC count, nominal input-buffer
  depth, router latency, and mesh/express link latency;
- infinite protocol/source queues, NI VC backpressure, one-flit input buffers,
  downstream VC credits, two-stage separable switch arbitration, one-cycle
  routers and pipelined links;
- deterministic/custom adaptive routing, committed 0/1/2-express source
  routes, pressure-aware/q/q+r/random candidate policies, and the
  timeout-based XY escape VC;
- uniform-random, one-way and bidirectional CutStress, hotspot,
  bit-complement, tornado, and probabilistic heterogeneous-SoC traffic;
- warmup-without-drain and measurement-only statistics matching schema 5.

It is not a replacement for full-system gem5 or for Garnet data packets.  That
is intentional: the Phase 3 script fixes `--inj-vnet=0`, so carrying those
unused mechanisms would make quick experiments slower and harder to audit.
Final claims must still pass a long-window Garnet run.  With Garnet's
directory XOR hash disabled so that deterministic destinations are preserved,
the V5 100k-cycle Bit-complement/Tornado audit matches this standalone model
within 1% throughput and 2% latency for all six matched configurations.  See
`ADAPTIVE_ROUTING_V5_GARNET_AUDIT_ZH.md`.

That V5 result must not be generalized blindly to every later mechanism.  A
same-cycle ordering probe found that Garnet lazily snapshots q/r before its
periodic control event on alternating Ruby cycles.  Reproducing that event
order in standalone, without changing routing or reservation decisions, puts
all seven archived V8 high-load calibration groups within 1.7% in throughput.
Four latency groups are within 3%; two are only 3.04% and 3.13% away, while the
Tornado Greedy knee remains 12.8% high.  The raw comparison is retained under
`results/20260905/garnet_calibration_event_order_fix`; no traffic-specific
coefficient was fitted to hide the remaining saturation sensitivity.
Always-before-control snapshots, one-cycle-earlier credit return, and same-tick
tester-to-NI admission were also tested; each substantially worsened the
multi-case calibration and was therefore rejected.

## Build and smoke test

```bash
cd express_mesh_project/standalone_noc
make -j
make test
```

## One run

```bash
./express_noc \
  --topology-file ../results/phase1/aspl.json \
  --topology aspl --routing adaptive \
  --traffic uniform_random --rate 0.50 --seed 1 \
  --warmup-cycles 20000 --measurement-cycles 100000 \
  --source-route --source-route-policy 4 \
  --source-mesh-routing xy \
  --reservation-weight 0.375 --express-vc-weight 0.625 \
  --express-wire-budget 64 --express-max-degree 1 \
  --output result.json
```

The JSON contains the same main fields consumed by the Phase 3 summarizers.
Use `run_experiments.py` for a matrix with the familiar Python-script flags:

```bash
python3 run_experiments.py --workers 8 \
  --topologies mesh random aspl \
  --routings adaptive --traffics uniform_random \
  --rates 0.40 0.50 0.60 --seeds 1 2 3 \
  --source-route --source-route-policy 4 \
  --source-mesh-routing xy --reservation-weight 0.5 \
  --express-vc-weight 1.0 --express-waiter-weight 0
```

Policy 4 scores a committed candidate as
`static_latency + reservation_weight * in_flight_reservations +
express_vc_weight * occupied_express_VCs`.  The selected budget-64 design uses
weights 0.375 and 0.625.  The source decision remains adaptive, while every committed mesh
segment uses XY.  This combination avoids the old per-hop route oscillation
and excessive migration to escape.  The three weights are CLI parameters for
fast sweeps.

The original policy-4 view remains the default.  Incomplete express-pressure
information can be tested without replacing it, for example:

```bash
./express_noc ... \
  --express-info-mode distance-gossip \
  --express-info-period 1 --express-info-delay 1 \
  --express-info-bits 4 --express-admission-fraction 0.75
```

`distance-gossip` reads a periodic q/r snapshot after the base delay plus the
express entry's Manhattan distance to the source router.  The matched
standalone and Garnet study,
including failed coarse/local variants and the Random-expectation caveat, is
documented in `PARTIAL_EXPRESS_INFO_V7_RESULTS_ZH.md`.

V7 delays reads but still updates the remote reservation counter instantly.
The physically closed incremental mode replaces that write with a propagating
route-setup record, endpoint registration, a returned ACK, source-local
unacknowledged state, and delayed escape cancellation:

```bash
./express_noc ... \
  --reservation-weight 0.6 --express-vc-weight 1.0 \
  --express-info-mode distance-gossip \
  --express-info-period 1 --express-info-delay 1 \
  --express-info-bits 4 --express-reservation-mode registered
```

The legacy `instant` reservation mode remains the default. Registered mode
requires policy 4 and delayed distance gossip so an invalid hybrid cannot be
selected accidentally. Its protocol, failed alternatives, overhead limits,
and matched long experiments are documented in
`PHYSICAL_EXPRESS_INFO_V8_RESULTS_ZH.md`.

Policies 5 and 6 are intentionally kept as standalone upper-bound experiments:

- policy 5 runs per-packet Dijkstra with unit-weight mesh links and dynamic
  reservation/VC penalties only on express links;
- policy 6 runs per-packet Dijkstra with dynamic reservation/VC penalties on
  every mesh and express link.  It assumes global instantaneous link state and
  is therefore an oracle-like reference, not the proposed hardware design.

Candidate counts up to 512 are accepted.  This makes it possible to separate
"too few precomputed candidates" from "too little congestion information".
`--retain-mesh-candidate` optionally reserves one of those slots for the pure
mesh route, i.e. K=8 becomes top-7 express-assisted candidates plus mesh.  It
is off by default so historical top-8 experiments remain bit-for-bit
comparable.

The additional scaling switches are:

```text
--vcs-per-vnet N --buffer-depth N --packet-flits N
--router-latency N --mesh-link-latency N
--express-latency-mode topology|fixed|length-aware
--express-latency N --express-wire-per-cycle N --injection-period N
```

`--packet-flits > 1` is a packet-granular serialization approximation, not a
fully flit-accurate wormhole model.  `--buffer-depth` is recorded for matrix
compatibility; with the validated single-flit model, increasing it does not
create extra queue slots.  These two exploratory scaling rows therefore need
Garnet confirmation before being used as architectural claims.

`run_20260831_standalone_suite.py` is the resumable driver for the complete
matrix in `docs/20260831.md`; `summarize_20260831_standalone_suite.py` creates
the compact tables, SVGs, and informal Chinese data report.  Its event queues
use bounded circular calendars, so long saturated runs do not retain all past
events in memory.

The report-facing placement search is `run_unified_sa_curve_search.py`.  It
uses one SA-reheat configuration for every traffic and scores the full
0.05--0.80 injection-rate curve; only the known traffic demand changes.  The
current main experiment is reproducible with:

```bash
python3 express_mesh_project/run_unified_sa_curve_search.py --workers 4
python3 express_mesh_project/run_20260831_standalone_suite.py \
  --sections main --workers 4 --resume
python3 express_mesh_project/summarize_20260831_standalone_suite.py
```

`search_placement_sa.py` performs reversible simulation-guided placement
search without axis/stride restrictions.  `run_feasibility.py` additionally
generates `bitcomp_*` and `tornado_*` demand-aware greedy placements, while
`generate_traffic_aware_placements.py` provides the simpler direct-pair
baseline.  All of these use new output names and leave the V3 components and
topology files untouched.

The standalone simulator can still reproduce the old NoEscape output-VC range
bug by default.  `--correct-no-escape-vcs` is the fixed diagnostic mode; the
corresponding Garnet bug has now been fixed.

## Deadlock diagnostics

The NI watchdog alone cannot distinguish injection starvation from a stopped
network.  `--continue-after-ni-watchdog --drain-cycles N` additionally records
global flit/delivery progress and tests whether a finite generated workload
drains.  The repeatable 48-case and timeout-sensitivity experiments are:

```bash
python3 run_escape_diagnostics.py --workers 4
python3 run_escape_timeout_sweep.py --workers 4
```

See `ESCAPE_DIAGNOSTICS.md` for results and the precise deadlock conclusion.

The budget/topology/routing design-space search and the final standalone plus
Garnet results are documented in `ADAPTIVE_ROUTING_V3_RESULTS_ZH.md`.
The incremental ideal-path, reversible-placement, and traffic-aware results
are documented in `ADAPTIVE_ROUTING_V4_EXPERIMENTS_ZH.md`; its aggregate CSV,
JSON, and SVG curves are under `results/v4_summary/`.  The old V4 Garnet
permutation table used an invalid destination mapping and is superseded by
`ADAPTIVE_ROUTING_V5_GARNET_AUDIT_ZH.md`.
The fixed-routing comparison of traffic-aware Greedy, three simulation-guided
SA variants, a path-based multicommodity-flow proxy, and one-topology-for-many-
traffic placement is documented in `TRAFFIC_AWARE_PLACEMENT_V6_RESULTS_ZH.md`.
