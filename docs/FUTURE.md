# Future features

Ideas deliberately left out of the current version. Newest at the top of each section.

## Requested

- **Timing mode (calibration).** Run a workflow with rough time estimates, benchmark the observed step times, and write the observed times back into the workflow definition. *(Jared, 2026-10-30)*

## Carried over from v1

- **Waiting stat can go negative.** "Waiting" = latency − Σ block durations, but a workflow's blocks can overlap; use latency − union of its block spans.
- **CP-SAT proves optimality more often.** Add redundant constraints (e.g., arm-load lower bound on makespan, per-workflow minimum duration).
- **CP-SAT chooses hotel parking** as an optional transport, instead of direct moves only.
- **Hotel handoffs** between workflows sharing a plate (skip store + retrieve).
- **Instruments keep running while re-planning** (solve time consumes wall-clock / sim time instead of pausing the clock).
- **Multiple arms / arm reach zones.**
- **Minimal deadlock cycle** (current cycle is a union over possible bindings, not guaranteed minimal).
