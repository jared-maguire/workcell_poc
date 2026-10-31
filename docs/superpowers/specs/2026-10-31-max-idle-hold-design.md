# Max idle hold: park plates instead of blocking instruments (v2 live mode)

## Intent

In the v2 live PCR runs, plates sit in the scanner, the stamper, or a thermocycler while they
wait for their next stop (usually a free thermocycler). The instrument holding them is idle
but blocked: no other workflow can use it. Jared's rule: **an instrument must not sit idle
and blocked by a plate for more than 5 minutes.** If the planner can see in advance that a
plate would wait longer than that, it moves the plate out *right away* to a hotel (or
storage), and brings it back when its next stop is free. No waiting for the 5 minutes to
pass first.

This is a hard rule for the planner, not a throughput heuristic: a plan that breaks it is
invalid. The cost is some extra arm moves and possibly a slightly longer run.

## Terms

- **Hotel:** deck positions. Putting a plate in or taking it out costs only the arm move
  (10 s in the example). No retrieve/store latency.
- **Storage:** a machine that files plates internally. `retrieve` and `store` take **60 s
  each** (Jared, 2026-10-31), on top of the arm move. Change the live example's `TIMES`
  from 20 to 60 for both ops.
- **Instrument nest:** any nest whose instrument is not storage or a hotel (scanner,
  stamper `src`/`dst`, thermocyclers). Storage and hotels are exempt from the rule; holding
  plates is their job.
- **Idle-hold gap:** while a plate sits in an instrument nest, the time between the end of
  one block that touches that plate and the start of the next block that touches it
  (including the block that moves it out). Concretely, for PCR plate E0001 on `stamp1.dst`:
  placed by `05`, used by `06` and `10`, moved out by `11`. Its gaps are `05→06`, `06→10`,
  `10→11`. Gaps are measured on the plate's own blocks, even if another workflow's block
  happens to use the same instrument meanwhile (my call: simple and checkable).
- **Leave gap:** the last gap of a stay, from the last block that uses the plate in that nest
  to the move that takes it out. **Mid-use gap:** any earlier gap (e.g. the sample waiting
  in `stamp1.src` while its partner plate is fetched).

## The rule

`max_idle_hold` (seconds, default **300**) bounds every idle-hold gap: gap ≤ limit
(equality allowed).

- **Mid-use gaps:** the planner must simply schedule the next step within the limit. No
  parking round-trip for these (my call: the planner controls the arm, so it can always
  sequence a workflow's own steps tightly; a round-trip would add complexity for little
  value).
- **Leave gaps:** the planner chooses, per stay, between
  - *direct:* the moving-out block starts within the limit, or
  - *park:* a park block moves the plate from the nest to a hotel or storage, starting
    within the limit, and the moving-out block is rewritten to start from the parking spot.
  The park choice is optional per stay; the planner takes it only when it needs to (or when
  it helps). Park location is chosen by the planner among all hotels and storage
  instruments; with the timings above it will almost always pick the hotel.
- **"Right away":** a parked plate should leave as soon as the arm can take it. Enforced as a
  secondary objective: minimize total idle-hold time, weighted well below the makespan
  (my call; a hard "park immediately" constraint can be infeasible when the arm is busy).
- Initial stays (plates in storage at t=0) are exempt (storage).
- Every leave in the compiler's output is a single-plate move block (`move`, `return`; one
  `PlateMove`, arm + the two nest owners). The park rewrite relies on this; if a future
  compiler change breaks it, the planner should refuse rather than guess. A final stay with
  no moving-out block has no leave gap (my call; every current workflow ends in storage).

## Representation (reuse what exists)

Parking reuses the v1 `greedy_hotel` representation the validator, simulator, and report
already understand: a planner-inserted block `<move block id>.park` (`inserted=True`,
`kind="park"`, one `PlateMove` nest → parking spot, instructions from
`transport_instructions`), and the original moving-out block keeps its id and destination
but its source becomes the parking spot and it gains a dep on the park block. CP-SAT now
produces these blocks too. The validator's rule "planner-inserted blocks are only allowed as
greedy_hotel park blocks" widens to "park blocks, from greedy_hotel or CP-SAT".

## Where it applies

- **Live mode only** (my call). `run_live(..., max_idle_hold=300)` and
  `python -m workcell live ... --max-idle-hold 300` (`0` disables). The v1 `run` command,
  its greedy planners, and its CP-SAT modes are unchanged.
- **Initial plan** (`plan_cpsat`) and **every re-plan** (`replan_cpsat`) enforce the rule.
  The serial warm-start hint never waits, so it satisfies the rule without parking.
- **Validator:** `validate_plan(plan, batch, max_idle_hold=None)`; when set, any gap over
  the limit in a planned schedule is an error. Park blocks must follow the shape above.

## Live execution

Actual step times vary, so a plate can end up waiting longer than planned.

- **New re-plan trigger:** whenever the executor checks for delays (on every block finish),
  it also projects idle-hold gaps; if any projected gap exceeds the limit, it re-plans
  immediately, so the new plan can park the plate early. This uses the existing re-plan
  machinery (two-phase, low churn). The adoption rule changes for this trigger: a re-plan
  that removes a projected idle-hold violation is adopted even if it does not save
  `threshold` seconds (the rule is hard; the makespan threshold only governs delay-driven
  re-plans).
- **Re-plan feasibility (my call):** the initial plan treats the rule as a hard constraint.
  Re-plans treat it as a heavily penalized soft constraint (violation seconds × a weight far
  above any makespan gain), because overruns can make a gap unavoidable, mid-use gaps
  included, and a re-plan must never come back infeasible because of the rule. Phase 1
  minimizes violation first, then makespan; phase 2 holds both and minimizes churn.
  Already-started park blocks and their rewritten moves are fixed like any other started
  block.
- **No repeated triggers (my call):** the idle-hold trigger fires only for a projected
  violation that is new relative to the current baseline (a gap not already over the limit
  in the plan being followed). Adoption: a re-plan triggered this way is adopted if it
  lowers total projected violation seconds versus sliding, regardless of `threshold`.
  Otherwise the projection becomes the new baseline (same as "not worth it" today), so the
  same violation does not trigger again.
- **The executor must handle inserted blocks:** places that look blocks up by compiled id
  (`compiled[bid]`, order graph, delay attribution, `as_run_plan`) must accept `.park` blocks
  and rewritten moves.
- **As-run result:** gaps that still exceed the limit in what actually happened are not a run
  failure; they are counted. The as-run validation runs without the idle-hold check.

## Report and stats

- Park blocks appear in the as-run Gantt (as they already do for greedy_hotel).
- Live stats gain: parks (count), idle-hold violations in the as-run schedule (count and
  worst gap), and re-plans triggered by idle hold (separate from delay-triggered ones).
- Regenerate `docs/reports/pcr10_live.html` (seed 0) and update the README v2 example
  numbers (storage timing changes them regardless).

## Goals / non-goals

- Goal: no planned schedule ever leaves an instrument idle and blocked > 5 min; parks happen
  early, not at the 5-minute mark.
- Goal: same seed, same run (determinism across processes is unchanged).
- Non-goal: hotel capacity limits beyond what nests already model; arm travel distances;
  park round-trips for mid-use gaps; any change to v1 `run`.
- Anti-goal: scheduler churn. Idle-hold re-plans still go through phase 2 (least change).

## Acceptance

- 10-PCR live run, seed 0: initial plan valid with `max_idle_hold=300` and zero planned
  violations; run completes; as-run schedule valid; report shows park blocks.
- Fast test tier stays around a minute; CP-SAT-heavy checks go in the slow tier.
- If the bigger model makes the 30 s initial solve fail to find a plan, that is a finding to
  report, not to paper over by loosening the rule.

## Left to the builder

Park location is a planner choice among all hotels and storage (Jared said "hotels or
storage"); with 0 s hotel handling vs 60 s storage ops, storage parks will be rare, but the
option stays.

How the CP-SAT model expresses optional parks (optional intervals per leave gap, park
location as a choice among hotel/storage nests), exact objective weights, test design, and
how stats are named in the report.
