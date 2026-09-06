# Express-Mesh increment audit

> Status note: this file records the audit of commit `fe6468185d`.  The V2
> changes described in `ADAPTIVE_ROUTING_V2.md` fix finding 1, make source
> mesh segments explicitly XY for the selected policy, and replace staging-q
> with reservation plus express-output-VC pressure.  The historical findings
> below are retained so the reason for each change remains auditable.

This audit compares the latest project-only commit (`fe6468185d`) with its
gem5 parent and with the intended experiment described in `gpt_chat.md` and
the project documents.  Findings are ordered by impact.

## 0a. V8 micro-event ordering calibration

Garnet schedules the periodic express-information event at default event
priority, while synthetic testers run at CPU-tick priority.  Instrumentation
shows that, with the report's 1 GHz tester / 2 GHz Ruby clocks, a lazy NI query
captures q/r before the periodic registration-control event on exactly the
even half of Ruby cycles; on intervening cycles the periodic event applies
control first.  Standalone previously applied control before every snapshot.
It now reproduces the measured alternating order.  Three neighboring timing
hypotheses were tested and rejected.  Forcing every snapshot before control
improved one Tornado point but made Uniform Greedy throughput 4.7% too low and
latency 37.5% too high.  Advancing free-credit availability by
one cycle made standalone throughput 7--29% too high and latency 38--99% too
low.  Allowing a tester request to enter the NI in the generation tick instead
of after the modeled protocol cycle made Tornado Greedy/SA throughput 5--6%
too low and latency 51--77% too high.  Thus the original two-cycle effective
credit return and one-cycle tester-to-NI boundary are both required; the
measured discrepancy was specifically the q/r snapshot/control order.

After the snapshot-order correction, every archived V8 high-load calibration
group is within 1.7% Garnet throughput.  Remaining latency disagreement is
concentrated at the highly sensitive Tornado Greedy knee (12.8%); other groups
are within 3.14%, with four of seven within 3%.  This residual is reported,
not removed with a traffic-dependent tuning constant.

The instrumented 200-cycle warmup + 2k-cycle Tornado Greedy probe also gives a
direct like-for-like micro-timing check before the long-run saturated
trajectories diverge: standalone differs from Garnet by -0.41% in throughput
and -0.88% in latency; mean observed q differs by -1.03%, mean observed r by
+2.68%, express traversals by +0.10%, and the total live reservations by one
(33 versus 32).  This makes a remaining deterministic one-cycle pipeline error
unlikely.  At the long saturation knee, a sub-percent service-rate difference
instead accumulates into a much larger queueing-latency difference.

## 0. Critical follow-up: deterministic traffic was destroyed by directory hashing

The V4 Garnet commands omitted `--xor-low-bit=0`.  The tester embeds a
64-directory destination in physical-address bits 6--11, while gem5's generic
memory-controller default XORs those bits with address bits 20--25.  Because
the latter bits change with the per-source packet tag, a fixed
Bit-complement/Tornado destination swept over almost all 64 routers.

The Phase-3 runner now disables this hash for synthetic destination tests and
validates Garnet's measured source/destination matrix after every
deterministic-traffic run.  The corrected 100k-cycle audit makes standalone
and Garnet agree within 1% throughput for all matched configurations.  See
`ADAPTIVE_ROUTING_V5_GARNET_AUDIT_ZH.md`.

## 1. Critical: NoEscape does not use four ordinary router VCs

`NetworkInterface::calculateVC()` correctly uses all four injection VCs when
escape is disabled.  However, both `OutputUnit::has_free_vc()` and
`OutputUnit::select_free_vc()` set the non-escape end iterator to
`vc_base + m_vc_per_vnet - 1` unconditionally.  Since the loop uses `< end`,
router outputs can only allocate VC0--VC2 even in NoEscape mode.  The same
off-by-one also affects `OutputUnit::congestion()`.

Consequences:

- Escape is 3 adaptive + 1 escape VC, as intended.
- NoEscape is *not* 4 adaptive VCs end-to-end.  It injects on four VCs but can
  allocate only three on every router output.
- A packet injected in VC3 is not itself permanently stuck, because input and
  output VC indices are independent, but the advertised resource comparison
  is false and router capacity/congestion signals exclude 25% of the intended
  resources.
- The existing NoEscape watchdog failures cannot be used as evidence that an
  escape VC is intrinsically necessary.

The standalone model preserves this bug by default for reproducibility and
offers `--correct-no-escape-vcs` for the intended behavior.  Mesh, source-route
q+r, Uniform, rate 0.5, seed 1 gives:

| Mode | End cycle | Accepted throughput | Outcome |
|---|---:|---:|---|
| repository-compatible NoEscape | 50,219 | 0 (measurement stats unavailable) | watchdog |
| corrected four-VC NoEscape | 120,000 | 0.2500 | completed |

The repository report lists watchdog cycles 50,245/50,229/50,235 for the same
Mesh cases, independently corroborating that this off-by-one drives the
reported behavior.

Recommended source fix: choose the end iterator from escape enablement.  When
escape is enabled, ordinary traffic uses `[base, base+3)` and escape uses
`[base+3, base+4)`; when disabled, ordinary traffic uses `[base, base+4)`.

## 2. High: `--routing` has no effect for source-routed experiments

With `--express-source-route`, `initializeSourceRoute()` marks every injected
packet `source_routed`.  `RoutingUnit::outportComputeExpressMesh()` then enters
the source-route branch before checking `isExpressAdaptive()`.  Mesh segments
always use `outportComputeLocalAdaptive()`, regardless of whether the run was
tagged `deterministic` or `adaptive`.

Therefore a Phase-3 matrix that combines `--source-route` with both routing
labels does not compare deterministic versus adaptive routing.  The labels
change a configuration bit that the executed path never reads.  Use only one
label for committed-source-route experiments, or explicitly implement two
different mesh-segment policies.

## 3. High: the Escape design is provable, but the current evidence/detector is not

For the Phase-3 one-flit scope, the intended structure is close to a standard
deadlock-free escape construction: VC3 is isolated, it follows Mesh XY, the
Adaptive-to-Escape transition is irreversible, and a finite timeout makes the
escape route eventually available.  Passing `"Local"` to the stock XY helper
suppresses its input-direction assertions and leaves a coordinate-only X-then-Y
choice; this is better made explicit in a dedicated escape-routing function.

The remaining gap is proof and validation rather than evidence that the
Escape channel itself cycles:

- assert the VC ranges and irreversible transition in code;
- build and topologically sort the VC3 channel-dependency graph;
- state the consuming-destination and fair-arbitration assumptions;
- use a finite-injection/drain test for the Phase-3 packet class;
- do not classify a per-NI injection stall as a network deadlock.

The standalone 48-case diagnostic found 36/36 Escape workloads globally live
and fully drainable.  It also produced NI-watchdog cases in which a flit moved
and a packet was delivered in the exact warning cycle, proving that this
watchdog can report overload/starvation rather than deadlock.  See
`ESCAPE_DIAGNOSTICS.md`.  Until the invariants/CDG check are added to Garnet,
the report should distinguish the formal design argument from implementation
validation and label existing warnings as suspected no-progress.

## 4. Medium: q measures a transient link staging queue, not downstream load

`expressQueue()` returns `OutputUnit::getOutQueue()->getSize()`.  This is the
short staging queue between crossbar traversal and a fully pipelined express
link.  It is not downstream input-buffer occupancy, free credits, or the
number of packets waiting for that express edge.  With one-flit packets and a
one-flit/cycle link, it is normally 0--2, matching the report's observations.

Thus policy 1/2 is better described as “staging-q (+ reservation)” than global
congestion-aware routing.  For a meaningful congestion signal, sample busy
downstream VCs/credits or an accumulated queue before switch allocation.

## 5. Medium: offline and runtime Uniform traffic differ

The offline `uniform_demand()` excludes `source == destination`.  Garnet's
runtime `UNIFORM_RANDOM_` draws from all 64 destinations without rejecting the
source, so 1/64 of runtime requests are local on average.  The effect is small
but systematic when matching predicted ASPL/load to measured hops and link
utilization.  Either reject self destinations at runtime or include them in
the offline demand matrix.

## 6. Medium: warmup reset does not make packet cohorts match

Resetting statistics without draining is appropriate for steady-state queues,
but counters describe different packet cohorts:

- planned/injected counts use events inside the measurement window;
- delivered bins and latency include packets planned/injected during warmup;
- live reservation state crosses the boundary while increment/decrement
  counters reset.

This explains nonzero differences between planned/delivered bins and why
`reservation_increments - reservation_decrements` need not equal final live
reservations.  Those deltas should not be presented as a closed accounting
identity unless the network is drained or boundary outstanding state is
recorded.

## 7. Low: experiment launcher contains a machine-specific library path

`run_phase3_measurement_v2.py` hard-codes `/home/jing/miniconda3/lib`.  The
current environment uses `/root/miniconda3/lib`, and the full gem5 build also
needed that directory in `LD_LIBRARY_PATH`.  Prefer inheriting the environment
or accepting a command-line/environment override.

## Scope limitation of the standalone reproduction

The C++ model intentionally reproduces only what the Phase-3 script executes:
vnet 0 and one-flit packets.  It does not claim equivalence for vnet 2's
five-flit packets, arbitrary node counts, other coherence protocols, power,
area, or full-system gem5.  Keeping this boundary explicit is necessary for
both speed and defensible validation.
