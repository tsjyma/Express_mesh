# Budgeted Express-Mesh Lab 4

This directory contains the project-specific experiment code, constrained
placements, compact result aggregates, figures, and Chinese report. The gem5
implementation lives in the surrounding source tree.

## Build

```bash
scons build/Garnet_standalone/gem5.opt -j2
```

If the runtime linker cannot find the environment's C++ libraries, prepend the
appropriate library directory to `LD_LIBRARY_PATH`.

## Fast standalone NoC experiments

`standalone_noc/` contains a dependency-free C++17 cycle model of the exact
vnet-0/one-flit Garnet subset used by `run_phase3_measurement_v2.py`.  It is
intended for fast sweeps before confirming selected points in gem5:

```bash
make -C express_mesh_project/standalone_noc -j
python3 express_mesh_project/standalone_noc/run_experiments.py \
  --workers 8 --topologies mesh random aspl --rates 0.50 0.60 0.80 \
  --seeds 1 2 3 --routings adaptive --traffics uniform_random \
  --source-route --source-route-policy 4 --source-mesh-routing xy \
  --reservation-weight 0.5 --express-vc-weight 1.0 \
  --express-waiter-weight 0
```

See `standalone_noc/VALIDATION.md` for the gem5 comparison and
`standalone_noc/AUDIT.md` for the original project-code audit, and
`standalone_noc/ADAPTIVE_ROUTING_V2.md` for the new algorithm, deadlock
argument, negative results, and confirmed Garnet measurements.  The audited
NoEscape output-VC off-by-one is fixed in Garnet; the standalone model retains
a compatibility switch for reproducing the old behavior.

For deterministic synthetic traffic, always use the Phase-3 runner rather
than constructing the gem5 command by hand.  It disables directory XOR
hashing and validates the observed source/destination matrix.  Without this,
Bit-complement and Tornado silently become near-all-destination traffic.  See
`standalone_noc/ADAPTIVE_ROUTING_V5_GARNET_AUDIT_ZH.md` for the corrected
100k-cycle Garnet results.

## Tests

From the gem5 repository root:

```bash
python3 -m unittest discover -s express_mesh_project/tests -v
```

## Main experiment

```bash
python3 express_mesh_project/run_phase3_measurement_v2.py \
  --workers 4 \
  --warmup-cycles 20000 \
  --measurement-cycles 100000 \
  --rates 0.40 0.50 0.60 \
  --seeds 1 2 3 \
  --topologies mesh random aspl \
  --routings adaptive \
  --traffics uniform_random \
  --source-route --source-route-policy 4
```

For Random placement seeds 2 and 3, additionally pass
`--random-placement-seed 2` or `3`.

## Included results

- `results/phase1/`: main constrained placements and offline metrics.
- `results/phase1_random_seed{2,3}/`: additional Random placements.
- `results/phase3_measurement_v2/validation_aggregate.json`: compact audit
  aggregate. Raw multi-gigabyte gem5 run directories are intentionally omitted.
- `results/phase3_measurement_v2/figures/`: four requested headline SVGs.
- `docs/EXPRESS_MESH_NEW_SCHEME_COMPARISON_REPORT_ZH.md`: concise report.

The optional P95 packet-latency and path-stretch metrics were not added because
the current Garnet statistics do not expose a packet-latency histogram. All
mandatory validation fields requested in `docs/feedback1.md` are included.
