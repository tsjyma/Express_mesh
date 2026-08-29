# Standalone Express-Mesh NoC simulator

This directory is a small, dependency-free C++17 reproduction of the part of
Garnet exercised by `run_phase3_measurement_v2.py`.  It deliberately models the
script's actual setting rather than general gem5:

- 8x8 ExpressMesh and the same topology JSON files;
- vnet 0, one-flit control packets, four VCs;
- infinite protocol/source queues, NI VC backpressure, one-flit input buffers,
  downstream VC credits, two-stage separable switch arbitration, one-cycle
  routers and pipelined links;
- deterministic/custom adaptive routing, committed 0/1/2-express source
  routes, pressure-aware/q/q+r/random candidate policies, and the
  timeout-based XY escape VC;
- uniform-random, CutStress, hotspot, bit-complement, and tornado traffic;
- warmup-without-drain and measurement-only statistics matching schema 5.

It is not a replacement for full-system gem5 or for Garnet data packets.  That
is intentional: the Phase 3 script fixes `--inj-vnet=0`, so carrying those
unused mechanisms would make quick experiments slower and harder to audit.
Final claims must still pass a long-window Garnet run.  With Garnet's
directory XOR hash disabled so that deterministic destinations are preserved,
the V5 100k-cycle Bit-complement/Tornado audit matches this standalone model
within 1% throughput and 2% latency for all six matched configurations.  See
`ADAPTIVE_ROUTING_V5_GARNET_AUDIT_ZH.md`.

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

Policies 5 and 6 are intentionally kept as standalone upper-bound experiments:

- policy 5 runs per-packet Dijkstra with unit-weight mesh links and dynamic
  reservation/VC penalties only on express links;
- policy 6 runs per-packet Dijkstra with dynamic reservation/VC penalties on
  every mesh and express link.  It assumes global instantaneous link state and
  is therefore an oracle-like reference, not the proposed hardware design.

Candidate counts up to 512 are accepted.  This makes it possible to separate
"too few precomputed candidates" from "too little congestion information".

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
