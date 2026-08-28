We need one focused, more complete validation round for the Budgeted Express-Mesh project.

Read the latest experiment report and existing implementation first. Reuse the current ExpressMesh topology, candidate generation, source-route state, q/r instrumentation, escape VC implementation, and measurement scripts where possible.

Do NOT redesign the project broadly. Do NOT add UGAL, EWMA, regional congestion, full-global Mesh congestion, RL, new placement objectives, or new topology sizes in this round.

The main research question is:

Can a sparse Express-Mesh outperform a pure Mesh when:
(1) express-sequence planning uses the existing global q+r information,
(2) every ordinary Mesh segment uses the SAME local minimal-adaptive routing policy as the pure-Mesh baseline,
and
(3) we evaluate far enough into the congested/saturation regime?

A secondary question is:
How does an escape channel affect performance/progress at high injection under otherwise identical adaptive routing?

==================================================
1. REFACTOR ONE SHARED LOCAL MESH ADAPTIVE ROUTER
==================================================

Implement/refactor ONE common local Mesh adaptive primitive and use it in BOTH:

A. pure Mesh adaptive routing;
B. every Mesh segment inside Express source-routed paths.

Do not maintain two subtly different implementations.

Suppose the current router is v and the current Mesh waypoint is w.

The legal productive outputs are exactly:

    O(v,w) = { (v,u) :
               u is a normal Mesh neighbor and
               ManhattanDistance(u,w)
               = ManhattanDistance(v,w) - 1 }

Among legal outputs with usable adaptive VC resources, choose the output with the lowest LOCAL congestion.

Use the same congestion measure everywhere, preferably the existing normalized output-VC occupancy / available-credit measure already used by LegacyLocalAdaptive.

Tie breaking must use the same seeded policy for Mesh and Express experiments.

Important:
- This is minimal adaptive routing inside a Mesh segment.
- It never increases Manhattan distance to the current waypoint.
- Therefore every Mesh segment still has exactly its Manhattan hop count.
- No global Mesh-link congestion information is used.

For pure Mesh:
    waypoint = final destination.

For an Express source route with planned express sequence [e1,e2,...]:
    waypoint = tail(next express edge)
until that express is traversed;
after all express edges are consumed:
    waypoint = final destination.

At an express tail, traverse the committed express edge and advance the stage.

Do NOT replan the express sequence in the middle of a packet.

==================================================
2. EXPRESS PATH PLANNING: KEEP THE CURRENT COMPLETE-CANDIDATE MODEL
==================================================

Reuse the current precomputed candidate paths:

- at most K=8 candidates per ordered (src,dst);
- each candidate uses 0, 1, or 2 directed express links;
- every inter-express Mesh portion is represented by its Manhattan-minimal segment;
- no repeated-router / looping candidates;
- pure Mesh candidate always included.

Because Mesh segments now use local minimal adaptation instead of fixed XY,
their static hop count is unchanged, so the existing static path latency L(P)
remains valid.

At packet injection choose ONE express sequence and commit to it.

==================================================
3. TWO EXPRESS-SEQUENCE SELECTION POLICIES ONLY
==================================================

Implement/retain exactly these two main policies.

--------------------------------------------------
Policy A: GlobalExpressReservation (main algorithm)
--------------------------------------------------

For each directed express edge e:

    q[e] = current real congestion / waiting-flit count
           at that express output

    r[e] = packets already committed to use e
           but not yet arrived at e's tail

For candidate P:

    C_qr(P) =
        L(P)
        + sum_{e in P ∩ E_express} (q[e] + r[e])

Choose:

    P* = argmin_P C_qr(P)

Then immediately reserve every express edge in P*:

    r[e] += 1

When the packet reaches the tail of e and starts entering that express resource:

    r[e] -= 1

If the packet abandons the committed express route because it enters the
escape path, release all reservations for unconsumed express edges.

Do NOT add theta/lambda/rho or any other tuning parameter.

--------------------------------------------------
Policy B: RandomCandidate (routing baseline)
--------------------------------------------------

Use the EXACT SAME candidate set P_sd.

At injection, uniformly randomly choose one candidate from P_sd using the
experiment seed.

Do not use q or r to choose the candidate.

If the chosen candidate contains express edges, the packet still commits to
the same source-route/stage mechanism.

For accounting consistency, reservations may still be tracked for
instrumentation, but MUST NOT influence RandomCandidate's route choice.

This baseline answers:

    Are gains from q+r actually due to congestion-aware express planning,
    rather than merely having multiple possible source routes?

OPTIONAL ONLY IF ALREADY TRIVIAL TO RUN:
StaticMinCandidate = choose candidate with minimum L(P).
Do not spend implementation time on this if it is not already easy.

==================================================
4. ESCAPE VS NO-ESCAPE: CONTROL VARIABLES CAREFULLY
==================================================

For every main adaptive configuration, support two VC modes:

A. Escape:
    total = 4 VCs/vnet
    3 adaptive VCs
    1 reserved XY escape VC

    retain the existing escape timeout, currently 32 cycles.

    If a packet switches to escape:
        - release reservations for all unconsumed express edges;
        - abandon the remaining express sequence;
        - route directly to FINAL destination using Mesh XY;
        - never return from Escape to Adaptive.

B. NoEscape:
    total hardware budget must ALSO be 4 VCs/vnet.
    Use all 4 as ordinary adaptive VCs.
    There is no escape transition.

This comparison deliberately gives NoEscape one extra adaptive VC, so Escape
is NOT being helped by extra VC hardware. State this explicitly in the report.

Everything else must be identical:
- topology
- candidate generation
- local Mesh adaptive policy
- local congestion metric
- path-selection policy
- packet size
- buffers
- arbitration
- seeds
- measurement window

For NoEscape runs:
- add robust no-progress/deadlock detection;
- do not let gem5 hang forever;
- record the cycle of detected no-progress/deadlock;
- record delivered packets before failure.

The purpose is to see whether Escape becomes useful at high load, not to make
NoEscape look artificially bad.

==================================================
5. TOPOLOGIES
==================================================

Main comparison:

1. pure Mesh
2. Random Express
3. Uniform-Hybrid / current greedy optimized placement

If the existing Handcrafted topology requires essentially zero extra work,
it may be included as a secondary row, but do not let it expand the report.

For Random Express:
- do NOT cherry-pick one favorable placement;
- use at least 3 random placement seeds under the same B, r, d_min constraints;
- report mean ± std across placement seeds when comparing topology quality.

Keep:
    8x8
    B=16
    extra degree <=1
    current d_min
    ideal express latency = 1

Do NOT vary B/r/latency/network size in this round.

==================================================
6. FAIR ROUTING COMPARISON
==================================================

The pure-Mesh baseline must now use the SAME LocalMeshAdaptive primitive that
Express routes use inside each Mesh segment.

Therefore the fair headline comparison is:

    Mesh + LocalMeshAdaptive
vs
    Random Express
        + q+r express planning
        + LocalMeshAdaptive segments
vs
    Uniform-Hybrid
        + q+r express planning
        + LocalMeshAdaptive segments

Run the same comparison separately for:

    Escape
and
    NoEscape.

Do NOT use the old LegacyLocalAdaptive as the headline Mesh baseline if its
logic differs from the newly shared LocalMeshAdaptive primitive.

LegacyLocalAdaptive may remain as a compatibility/debug reference only.

==================================================
7. INJECTION-RATE SWEEP: ACTUALLY REACH SATURATION
==================================================

The previous round only tested configured rates up to 0.16, where accepted
throughput was still almost perfectly linear and max utilization was low.

This round MUST extend until a clear knee / saturation / instability is seen.

First run a quick 1-seed coarse sweep approximately over:

    0.08
    0.12
    0.16
    0.20
    0.24
    0.28
    0.32
    0.40

If accepted throughput is still linear and latency remains flat at 0.40,
continue upward (e.g. 0.50, 0.60, ...) until one of these is clearly observed:

- accepted throughput stops scaling linearly;
- latency rises sharply;
- link utilization approaches saturation;
- NoEscape loses progress.

Do not assume the command-line injectionrate equals actual packet throughput.
Continue recording actual injected/ejected packets/flits.

After locating the knee for each important family, run a denser sweep around
the common knee region.

Final main points:
- warmup ~= 20,000 network cycles
- measurement ~= 100,000 network cycles
- 3 traffic seeds minimum

Use shorter runs only for coarse scanning.

==================================================
8. EXPERIMENT MATRIX
==================================================

Do NOT take an unnecessary full Cartesian product.

Stage 1: find congestion regime
--------------------------------
Use:
    Uniform-Hybrid + q+r + LocalMeshAdaptive + Escape
    Mesh + LocalMeshAdaptive + Escape

Coarse-scan rates until a clear knee is found.

Stage 2: headline topology comparison
-------------------------------------
At the relevant full rate range compare:

    Mesh + LocalMeshAdaptive
    Random + q+r + LocalMeshAdaptive
    Uniform-Hybrid + q+r + LocalMeshAdaptive

First with Escape ON.

This is the MAIN result.

Stage 3: routing-policy ablation
--------------------------------
On the SAME Uniform-Hybrid topology compare:

    RandomCandidate + LocalMeshAdaptive
    q+r + LocalMeshAdaptive

with Escape ON.

This tests whether congestion-aware express-sequence planning matters.

Stage 4: escape ablation
------------------------
For:
    Mesh + LocalMeshAdaptive
    Uniform-Hybrid + q+r + LocalMeshAdaptive

compare:
    Escape
    NoEscape

especially near and above saturation.

If runtime permits, add Random topology here only as secondary data.

==================================================
9. KEY METRICS
==================================================

For every final run record:

- configured injection rate
- actual injected packet throughput
- accepted/ejected packet throughput
- average packet latency
- P95 packet latency if available
- average hops
- path stretch if available
- max directed-link utilization
- P95 directed-link utilization
- express traversals per delivered packet
- fraction of delivered packets using:
    0 express
    exactly 1 express
    exactly 2 express
- escape fraction
- average cycles before escape
- deadlock/no-progress flag
- deadlock/no-progress cycle for NoEscape
- per-express-edge selected packet count
- average/max q[e]
- average/max r[e]

==================================================
10. WHAT RESULTS MATTER
==================================================

Do NOT force an expected ordering.

The desired-but-not-assumed topology result is:

    throughput(
        UniformHybrid + q+r + LocalMeshAdaptive
    )
    >
    mean throughput(
        Random + q+r + LocalMeshAdaptive
    )
    >
    throughput(
        Mesh + LocalMeshAdaptive
    )

But Random is NOT required to beat Mesh.

The actual success criteria are:

A. Topology value:
    Uniform-Hybrid must beat pure Mesh under the SAME LocalMeshAdaptive policy,
    especially in peak/saturation throughput.

B. Placement value:
    Uniform-Hybrid should beat the mean Random placement under the SAME routing.

C. q+r routing value:
    On the SAME Uniform-Hybrid topology,
    q+r should outperform RandomCandidate near congestion if global express
    congestion awareness is useful.

D. local Mesh adaptation value:
    Express performance should no longer be artificially limited by fixed XY
    routing inside ordinary Mesh segments.

E. escape value:
    Escape may have little effect at low load but should preserve progress /
    avoid severe degradation at high load if adaptive channel dependencies
    become problematic.

If NoEscape performs better at all tested rates and never loses progress,
report that honestly.

==================================================
11. MAIN FIGURES ONLY
==================================================

Generate only the following headline figures:

Figure 1:
    accepted throughput vs actual offered throughput
    for:
        Mesh
        Random Express (mean across placements)
        Uniform-Hybrid
    all using q+r where applicable,
    LocalMeshAdaptive,
    Escape ON.

Figure 2:
    average latency vs actual offered throughput
    for the same three configurations.

Figure 3:
    Uniform-Hybrid only:
        RandomCandidate
        q+r
    same LocalMeshAdaptive + Escape.
    Show throughput and/or latency near saturation.

Figure 4:
    Escape vs NoEscape near/high load for:
        Mesh
        Uniform-Hybrid q+r

Optionally one near-saturation link-utilization heatmap if it clearly explains
a result.

Do NOT generate dozens of plots.

==================================================
12. REPORT FORMAT: VERY SHORT
==================================================

Produce one concise Markdown report, target <= 1500 Chinese characters
excluding tables.

Structure:

# More Complete Validation

## 1. Experimental change
Maximum 6-8 lines:
- shared LocalMeshAdaptive
- q+r vs RandomCandidate
- Escape vs NoEscape
- wider rate range

## 2. Main topology result
One table and at most one paragraph.

Required table:

| Routing/topology | Peak accepted throughput | Knee | Latency near knee | Max util |
|---|---:|---:|---:|---:|
| Mesh + LocalAdaptive | | | | |
| Random + q+r + LocalAdaptive | | | | |
| UniformHybrid + q+r + LocalAdaptive | | | | |

## 3. Routing and escape ablation
One compact table:

| Comparison | Main observation |
|---|---|
| q+r vs RandomCandidate | |
| Escape vs NoEscape (Mesh) | |
| Escape vs NoEscape (Hybrid) | |

## 4. Verdict
Answer only these four questions:
1. Does Uniform-Hybrid beat Mesh under fair local adaptation?
2. Does Uniform-Hybrid beat Random placement?
3. Does q+r beat random candidate selection?
4. Does escape matter at high injection?

Do NOT include:
- long implementation diary
- code walkthrough
- TODO list
- literature survey
- detailed correctness discussion unless something failed
- dozens of secondary metrics
- unsupported speculation

Save raw CSVs, detailed logs, and secondary plots separately.

==================================================
13. CORRECTNESS CHECKS BEFORE FULL RUN
==================================================

Before expensive sweeps verify:

1. Pure Mesh LocalMeshAdaptive and Express-segment LocalMeshAdaptive call the
   SAME implementation.

2. A Mesh segment always decreases Manhattan distance by exactly one per hop.

3. 0/1/2-express committed source routes still execute correctly.

4. q/r reservation accounting still returns to zero after drained runs.

5. Escape releases all unused express reservations.

6. Escape never returns to Adaptive.

7. NoEscape contains no hidden XY/escape fallback.

8. Escape and NoEscape both use exactly 4 VCs/vnet total.

9. Actual injected/ejected throughput accounting is consistent.

Then run the wider Uniform validation and report the actual result, including
negative results.