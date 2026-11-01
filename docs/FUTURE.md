# Future features

Ideas deliberately left out of the current version. Newest at the top of each section.

## Requested

- **Real library prep & capture workflow, starting from extracted gDNA.** Illumina library prep followed by IDT hybridization capture (xGen), as a realistic multi-step workflow for the workcell. *(Jared, 2026-10-31)* Rough sketch of what it would exercise (to confirm against the actual kit protocols):
  - Library prep: fragmentation/tagmentation, cleanups, indexing PCR, bead cleanups.
  - Capture: pooling libraries (many plates → one), long hybridization incubation (hours), streptavidin-bead capture, washes, post-capture PCR, cleanup, QC.
  - New instrument types likely needed: magnetic-bead handling / plate washer, a general liquid handler (beyond the 96-head stamper), heated shaker / incubator, plate sealer and peeler, centrifuge, QC reader.
  - New workflow-language needs: many-to-one pooling, reagent plates consumed across steps, steps with time windows (e.g. bead incubations that must not run long).
- **Timing mode (calibration).** Run a workflow with rough time estimates, benchmark the observed step times, and write the observed times back into the workflow definition. *(Jared, 2026-10-30)*

## Just-in-time dispatch (from the switch away from live CP-SAT)

- **Steps with time limits between them** (e.g. seal within N minutes of dispensing, beads must not sit). The dispatcher has no lookahead, so it can start step A and find step B's instrument busy. This is where lookahead or a planner earns its keep; add such steps to a workflow first and see where dispatch breaks.
- **Mid-use idle waits.** The idle rule parks only a plate whose next block is a move out. A plate waiting between two uses of the same instrument (e.g. a destination plate on the stamper while its primer is fetched) is never parked.
- **Forecast during the run.** Today the forecast is made once, with planned times. Re-running the dispatcher from the current state on every event (milliseconds) would give a live ETA per workflow.
- **CP-SAT as an advisor.** On unit-time PCR (arm-bound, no long steps), CP-SAT still beats dispatch (204 vs 236 on 10 PCRs). It could suggest priorities or bindings to the dispatcher offline. `run`'s CP-SAT now warm-starts from the dispatcher's plan (it lands next to it on the two-arm library prep: 41,188 vs 41,111 s at 20 s). An earlier try that kept the park options in the model found nothing.
- **Smarter priorities.** Workflows under way, then most remaining work, was the best of four simple rules tried. The mixed batch (3 library preps plus 4 PCRs) still varies about 50 min across seeds.

## Carried over from v1

- **Waiting stat can go negative.** "Waiting" = latency − Σ block durations, but a workflow's blocks can overlap; use latency − union of its block spans.
- **CP-SAT proves optimality more often.** Add redundant constraints (e.g., arm-load lower bound on makespan, per-workflow minimum duration).
- **Hotel handoffs** between workflows sharing a plate (skip store + retrieve).
- **Minimal deadlock cycle** (current cycle is a union over possible bindings, not guaranteed minimal).
