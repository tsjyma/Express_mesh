# Full Garnet paper experiment runner

`run_garnet_full_paper_suite.py` reproduces every simulated result used by the
paper except Tables 3--5. Those three tables already have checked-in Garnet
measurements, and behavior-identical points needed by another figure are
imported rather than simulated again. The four Uniform/$R=0.8$/seed-5 samples
are the sole exception: Figure 9 needs their full directed-link vectors, which
the compact table records do not contain, so those configurations are rerun
for Figure 9. The experiment definitions are taken
directly from `run_20260831_standalone_suite.py`, including all placements,
traffic seeds, injection rates, measurement windows, routing/information/
escape ablations, and scaling configurations.

On a clean Ubuntu checkout, first install the normal gem5 build dependencies
if they are not already present:

```bash
sudo apt update
sudo apt install -y build-essential scons python3-dev m4 \
  zlib1g-dev protobuf-compiler libprotobuf-dev libgoogle-perftools-dev
```

Then run the complete job from the repository root. The command builds Garnet
with three compiler jobs and runs four independent simulations concurrently,
which is appropriate for the specified 4-core, 15-GiB VM:

```bash
python3 express_mesh_project/run_garnet_full_paper_suite.py \
  --workers 4 --build-jobs 3 2>&1 | tee garnet_full_paper_suite.log
```

The runner is resumable by default. If the SSH session, VM, or experiment is
interrupted, run the same command again; completed cases are read from the
per-execution cache. A quick remote preflight is available without starting
the full experiment:

```bash
python3 express_mesh_project/run_garnet_full_paper_suite.py --plan-only
python3 express_mesh_project/run_garnet_full_paper_suite.py \
  --sections routing_ablation --max-executions 1 --workers 1
```

Do not use `--max-executions` for the paper run. `--sections` can be used to
split the job across machines, but each machine must use a different output
directory; the returned archives can then be merged during analysis.

The default result directory is
`express_mesh_project/results/garnet_full_paper_suite`. It contains:

- `case_cache/`: one atomic compact JSON file per unique Garnet execution;
- `executions.json`: deduplicated simulation results with all logical uses;
- `results.json`: expanded paper-facing rows;
- `aggregates.json` and `aggregates.csv`: convenient mean/SD summaries;
- `failures.json` and `failure_logs/`: cases that must be inspected or retried;
- `manifest.json` and `progress.json`: coverage, configuration, and timing;
- `garnet_full_paper_results.tar.gz`: the compact analysis archive to copy
  back locally (per-case resume caches remain in the result directory).

Raw gem5 configuration and statistics files are deleted only after their
metrics and directed-link vectors have been cached. Pass `--keep-raw` only for
debugging because it can exceed the VM's 100-GiB disk. Top-K route-table caches
are retained remotely to reduce repeated 16x16 startup work, but are omitted
from the transfer archive because they are reproducible.

For example, copy the final archive from the workstation with:

```bash
scp ubuntu@REMOTE_HOST:~/Express_mesh/express_mesh_project/results/\
garnet_full_paper_suite/garnet_full_paper_results.tar.gz .
```

The current five-layout 16×16 plan contains 9,610 logical samples,
deduplicated to 9,242 Garnet executions; 380 existing Table-3--5 runs are
imported, leaving 8,862 new runs. The initial downloaded archive used ten
16×16 Random layouts; its outstanding watchdog cases are handled separately
by [the supplement runner](GARNET_WATCHDOG_SUPPLEMENT.md).
Using the local calibration gives about 164 CPU-hours, or an optimistic 41
hours at perfect four-way utilization. Allow roughly 2--3 days on the stated
VM for 16x16 initialization, virtualization variance, result serialization,
and imperfect load balance.
