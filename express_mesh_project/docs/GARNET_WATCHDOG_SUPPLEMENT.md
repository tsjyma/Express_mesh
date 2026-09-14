# Garnet watchdog supplement

From a clean checkout at the repository root, run:

```bash
python3 express_mesh_project/run_garnet_watchdog_supplement.py \
  --workers 2 --build-jobs 2 \
  2>&1 | tee garnet_watchdog_supplement.log
```

The script builds Garnet first, then runs **161 distinct cases** with a
200,000-cycle NI watchdog threshold. Section 4.5 contributes the exact 130
previously censored cases: 125 short escape-timeout runs, three ordinary
escape-off runs, and two LocalMeshAdaptive runs. The controlled two-VC
escape-off cycle that was independently shown to deadlock is excluded.

Section 4.6 contributes 31 cases: all ten combinations of topology seeds
1--5 and traffic seeds 5--6 for each of the three 16×16 Random rows, plus
the censored SoC Mesh seed 18. Running the complete five-layout Random cohort
makes this supplement self-contained and ensures that every
`16x16-B256-L4` sample uses the corrected length-dependent express-link
latency. Layouts are the first five numbered seeds, never selected by
performance.

The job resumes from per-case JSON caches if interrupted; rerun the same
command. A no-simulation preflight is available:

```bash
python3 express_mesh_project/run_garnet_watchdog_supplement.py --plan-only
```

The default output is
`express_mesh_project/results/garnet_watchdog_supplement_20260914/`.
Copy its `garnet_watchdog_supplement_results.tar.gz` back to the workstation.
The archive includes results, aggregates, original case uses, the exact
threshold, topology JSONs, progress, and command-failure logs. The original
large downloaded Garnet results archive is *not* needed on the remote host.
Only this new archive needs to be transferred for merging with the original
local data.

**Interpretation:** a 200,000-cycle NI threshold exceeds the longest
140,000-cycle run and prevents that watchdog from aborting the fixed window.
It does not turn an overloaded or permanently blocked network into a
steady-state measurement. Inspect offered versus accepted throughput,
backlog/latency, and completion status before using any recovered result in
the paper. Two concurrent processes are the safe default for the specified
four-core, 15-GiB, no-swap VM; four concurrent congested 16×16 runs can
exhaust memory.
