# Future features

Ideas deliberately left out of the current version. Newest at the top of each section.

## Requested

- **Real library prep & capture workflow, starting from extracted gDNA.** Illumina library prep followed by IDT hybridization capture (xGen), as a realistic multi-step workflow for the workcell. *(Jared, 2026-10-31)* Rough sketch of what it would exercise (to confirm against the actual kit protocols):
  - Library prep: fragmentation/tagmentation, cleanups, indexing PCR, bead cleanups.
  - Capture: pooling libraries (many plates → one), long hybridization incubation (hours), streptavidin-bead capture, washes, post-capture PCR, cleanup, QC.
  - New instrument types likely needed: magnetic-bead handling / plate washer, a general liquid handler (beyond the 96-head stamper), heated shaker / incubator, plate sealer and peeler, centrifuge, QC reader.
  - New workflow-language needs: many-to-one pooling, reagent plates consumed across steps, steps with time windows (e.g. bead incubations that must not run long).
- **Timing mode (calibration).** Run a workflow with rough time estimates, benchmark the observed step times, and write the observed times back into the workflow definition. *(Jared, 2026-10-30)*

## Live re-planning (from v2 observations)

- **Better overrun detection and estimates.** v2 checks for delays whenever any block finishes; a still-running overrun is noticed then, but only as "ends now" (optimistic), and not at all if nothing else finishes meanwhile. A timed "overdue" check at planned end + threshold, plus a pessimistic remaining-time estimate, would let re-plans move queued plates to idle instruments while the overrun is still happening.

- **Smarter overrun estimates for parking.** Lab-tech parks act only when a waiting plate hits its deadline; a remaining-time estimate for overdue steps (or asking the instrument, as a real thermocycler reports time left) would let re-plans park earlier and reshuffle other work. A "late by X, assume X more" estimate was tried and caused a re-plan storm.
- **Re-plan after a lab-tech park.** A lab-tech park frees the instrument, but the plan order still queues the instrument's next user behind the parked plate's unpark, so nobody else uses the freed instrument until then. A re-plan right after the park would let other work move in.
- **Mid-use idle waits.** The idle-hold rule bounds waits between a workflow's own steps on an instrument (e.g. a sample on the stamper while its partner plate is fetched) but never parks for them; step-time noise can push those a few seconds over the limit.

## Scheduling performance (from v2 observations)

Done: 10 s grid, 4 h rolling window, persistent solver process (demos 4–7 min → 40–130 s). Tried, no help: a hand-added thermocycler lower bound (slower or weaker) and a relative gap limit (only reached at the end).

- **Window selection ignores contention.** Blocks enter the window by deps-only earliest start, so many parallel workflows (10 PCRs) all fall in the first window. A resource-aware estimate (e.g. the previous plan's starts, or a quick list schedule) would keep windows small.
- **Idle-hold first plan from an infeasible warm start.** The hard-limit stage starts from a plan that breaks the limit; in a small budget it can settle for a much worse plan (two arms, whole batch: 20 h vs 12 h). Repairing the warm start into a compliant plan (insert parks where waits are long) before solving would fix the hint.
- **Idle-hold re-plan churn.** Bursts of small adopted idle-hold re-plans (every ~5 min) near the end of runs.
- **Re-plans that find nothing.** On two arms a few re-plans still return no solution even after a 4× retry (the rounded warm start).


With realistic times (4 s scans next to 4020 s thermocycles; horizon ~44,000 s), CP-SAT cannot find a first feasible plan for 10 PCR workflows unaided; with a serial warm start it reaches 21,413 s against a ~20,100 s thermocycler lower bound.

- **Two-level planning.** First decide which thermocycler each plate uses and in what order (the real bottleneck), then schedule the arm moves around that fixed assignment.

## Carried over from v1

- **Waiting stat can go negative.** "Waiting" = latency − Σ block durations, but a workflow's blocks can overlap; use latency − union of its block spans.
- **CP-SAT proves optimality more often.** Add redundant constraints (e.g., arm-load lower bound on makespan, per-workflow minimum duration).
- **Hotel handoffs** between workflows sharing a plate (skip store + retrieve).
- **Instruments keep running while re-planning** (solve time consumes wall-clock / sim time instead of pausing the clock).
- **Multiple arms / arm reach zones.**
- **Minimal deadlock cycle** (current cycle is a union over possible bindings, not guaranteed minimal).
