# Workcell Playground v2 — Realistic Times, Randomness, Live Re-planning

Date: 2026-10-30
Branch: `v2` (from `master` at dc6470c)
Builds on: `docs/superpowers/specs/2026-10-30-workcell-scheduler-design.md` (v1) and the v1 code. Read `CLAUDE.md` first: its invariants still hold unless this document changes them.

Items marked **(Claude's call)** were not decided by Jared; everything else was.

## 1. Intent

v1 plans a batch once, with every step taking 1 time unit, and executes the plan exactly. Real labs aren't like that: step times vary, and sometimes a step runs long. v2 asks: what does it look like to run a batch with realistic step times that vary randomly, and re-arrange the schedule on the fly when a delay is big enough to matter?

**Success:** run 10 PCR workflows with realistic times and seeded randomness. Open a report showing what actually happened, with a marker at each re-plan. Rerun with the same seed and get the identical run.

## 2. Goals, non-goals, anti-goals

**Goals**
- Times in **integer seconds**, specified in the workflow definition.
- Random actual step times, seeded and reproducible.
- One planner: CP-SAT (minimize makespan). No mode comparison ("don't do an algorithm bake-off, just use the best one").
- Live execution: small delays are absorbed by waiting; long delays trigger a CP-SAT re-plan from the current state.
- Report built from what actually happened, with re-plan markers and a few extra stats.

**Non-goals**
- Further scheduler optimization work. Good-enough plans are the goal; ideas are parked in `docs/FUTURE.md`.
- A timing / calibration mode that benchmarks observed times and writes them back into the workflow. This is in `docs/FUTURE.md`.
- Instruments continuing to run while CP-SAT re-plans. The simulated clock pauses during a solve; `docs/FUTURE.md` has the real-time version.
- Comparing planners, or exposing greedy modes in v2's command.
- Injected instrument failures beyond what v1 already supports.

**Anti-goals**
- Breaking v1. The v1 `run`/`scale` commands, modes, and tests keep working.
- A run that isn't reproducible: same inputs plus the same seed must give an identical trace, including every re-plan.
- Re-plans that violate physical reality. The as-run schedule must satisfy every v1 validator invariant (occupancy, locks, move continuity, borrow spans) using actual durations.

## 3. Times

### 3.1 Units
All times become **integer seconds**: instruction durations, plan start times, trace times. v1's 1-unit durations are reinterpreted as 1 second, so v1 behavior and tests are unchanged numerically. *(Claude's call)*

### 3.2 Where times come from (decided: in the workflow)
Steps the user writes take their time from the call itself:
- `w.stamp(src, dst, seconds=45)`
- `w.thermocycle(plate, program=...)`: the time is the sum of the program's step minutes × 60. The current `PCR_PROGRAM` sums to 67 min = 4020 s.

Steps the compiler adds (arm moves, doors, retrieve/store, scans, status checks) get their times from a table attached to the workflow:

```python
TIMES = {"arm.move": 10, "open_door": 5, "close_door": 5, "scan": 4,
         "retrieve": 20, "store": 20, "check_status": 0}   # seconds

@workflow(times=TIMES)
def pcr(w, sample, primer): ...
```

Keys follow the workcell durations convention: `"<instrument_type>.<op>"` or `"<op>"`.

**Lookup order, per instruction** *(Claude's call, for v1 compatibility)*:
1. the op's own `seconds=` argument (or the program sum, for a thermocycle)
2. the workflow's `times` table (`type.op`, then `op`)
3. the workcell's `durations` (`type.op`, then `op`, then `default`)

`@workflow` without arguments keeps working, as in v1. Rules 1–2 (including the thermocycle program sum) apply **only to workflows declared with `@workflow(times=...)`**. An untimed workflow, such as the library `pcr`, uses only rule 3, exactly as in v1. This keeps v1 tests and the committed v1 report unchanged, and it is why the v2 example defines its own timed PCR workflow *(Claude's call)*. Instructions in a block take the timing of the workflow that owns the block.

### 3.3 The v2 example
`examples/pcr_live.py` defines a timed PCR workflow (with its `TIMES` table and stamp seconds) and a 10-workflow `batch()`. Values *(Claude's call, all overridable)*:
- arm move 10 s, doors 5 s each, scan 4 s, retrieve/store 20 s each, check_status 0 s
- stamp 60 s
- thermocycle = `PCR_PROGRAM` (4020 s)

It uses the existing `examples/workcell.yaml`. Its `durations` are only the fallback.

## 4. Randomness

Each instruction's **actual** duration is drawn from a seeded distribution around its planned duration *(Claude's call on defaults; Jared approved the shape)*:
- **Usually:** planned × (1 + ε), with ε ~ Normal(0, 0.05), clipped so the result is ≥ 0.
- **With probability 0.02:** a long delay, planned × U(2, 5).
- The result is rounded to integer seconds. A 0-second instruction stays 0.

**Reproducibility:** the draw for an instruction depends only on (seed, block id, instruction index). It must not depend on execution order or on how many re-plans happened, so changing the re-plan threshold never changes the dice. The seed, the noise σ, the long-delay probability, and the long-delay range are all parameters.

## 5. Live execution

A new live executor runs a batch end to end:
1. **Initial plan:** CP-SAT on planned durations, makespan objective.
2. **Execute against actual durations,** following the current plan.
3. **Re-plan** when a long delay is detected.

### 5.1 Following a plan when times drift ("small delay: just wait")
The executor treats the plan as an **order**, not a timetable. *(Claude's call: this preserves validity)*
- A block may start once all of these hold:
  - its planned start time has passed
  - its dependencies have finished
  - every block planned before it on any shared instrument or plate has finished
  - for each nest it places a plate into, the block planned to remove the previous occupant has started
  - its locks are free
- Because the original plan was valid, keeping its per-resource order while only delaying starts stays valid and cannot deadlock.

Lock waits are normal in v2. Unlike v1, they are not a planner-bug signal.

### 5.2 Detecting a long delay (decided)
A delay is **long** if it would push some not-yet-started step's start later than its current planned start by more than the **re-plan threshold**. The default threshold is 300 s *(Claude's call)*. A delay that only eats slack is small, however large it is.

- **When to check** *(Claude's call)*: every time a block finishes, the executor projects the rest of the current plan. It propagates actual finish times through the dependency and resource-order graph of §5.1, assuming every remaining block takes its planned duration.
- **Trigger:** if any unstarted block's projected start minus its current planned start exceeds the threshold, re-plan.
- **Limitation:** an overrun is only noticed when the overrunning block finishes, not while it is still running. Mid-run detection is a possible future refinement.

### 5.3 Re-planning
At sim time `now`, CP-SAT solves the remaining work:
- **Fixed:** finished blocks are out of the model. Running blocks are fixed at their actual start, with their remaining time taken as planned.
- **Bindings:** instrument variable bindings already used by a started block stay fixed. Everything else is free to rebind (e.g. which thermocycler).
- **Plates and nests:** each plate's current location replaces its initial location. Occupancy and stays start from the current state.
- **Time:** every unstarted block starts at or after `now`.
- **Two phases (decided: use idle hardware, but no churn):**
  1. Minimize makespan, hinted with the current shifted plan and its bindings.
  2. With makespan held at phase 1's best value, minimize churn. Churn = 600 × (number of unstarted blocks' instrument bindings that differ from the current plan) + Σ |new start − projected start| over unstarted blocks. Plates that haven't started may move to another instrument (idle hardware gets used), but only when it buys an earlier finish. *(The 600 s weight per reassignment is Claude's call.)*
- **Adoption rule (decided):** the new plan replaces the current one only if its makespan beats the current projected makespan by at least the re-plan threshold. Otherwise execution keeps sliding on the current plan, and the event is recorded with status `"not_worth_it"`.
- **Budget ("good enough", decided):** the initial plan gets 30 deterministic-seconds (10 left 10-PCR plans ~40% above what 30 finds, which made later re-plans look like delay responses when they were just finishing the optimization), warm-started with a serial one-workflow-at-a-time plan (no greedy). Each re-plan phase gets 2 deterministic-seconds.
- **Clock:** the simulated clock pauses during the solve.
- **Fallback:** if CP-SAT returns no solution, keep following the current plan and record the failed re-plan.
- **Isolation:** deterministic solves run in a child process with retry, because OR-Tools 9.15 on Python 3.14 intermittently aborts with heap corruption. A retry returns the same answer.

**Determinism:** CP-SAT must be configured so the same model gives the same answer. Use a deterministic-time limit, or a single worker with a fixed seed, rather than a wall-clock limit. The method is the builder's call; the reproducibility requirement in §2 is not.

Reproducibility is guaranteed **within one environment**: the same machine and OR-Tools version *(Claude's call)*, and across separate processes, so rerunning the CLI with the same seed gives the same run.
- Tests compare two runs made in the same process, with wall-clock solve times excluded.
- Tests check properties of particular seeds (e.g. "at least one re-plan") rather than exact makespans.
- The committed example report does not need to regenerate byte-for-byte on other machines.

### 5.4 Output
- **Trace:** as in v1, plus actual instruction durations.
- **Plans:** the initial plan and each re-plan.
- **Re-plan events:** one per re-plan, recording:
  - the time
  - the triggering block
  - the observed delay
  - the projected push-back that crossed the threshold
  - the solve time and status
  - the projected makespan before and after

## 6. Validation (the oracle, extended)

The as-run schedule is the executed blocks at their actual start times with their actual durations. It must pass all v1 validator invariants:
- dependencies
- no instrument or plate overlap
- move continuity
- nest capacity under R4 occupancy
- borrow spans
- bindings consistent with what was executed

The validator needs a way to take actual durations, for example a per-block duration override. Comparisons of instructions against the compiled blocks must ignore duration. A clean run (no injected failure) must end with no alarm and every workflow complete. Tests should check this across several seeds, including seeds that trigger re-plans.

## 7. Stats

v1 stats are computed over the actual run, plus:
- the number of re-plans, including failed ones
- total delay, meaning the sum over blocks of actual minus planned duration
- initial planned makespan versus actual makespan
- the seed and the re-plan threshold

## 8. Report (decided: just what actually happened)

A single run, so no mode tabs. Everything is built from actual times:
- both Gantt charts, the animation, stats, source, and the compiled view
- **re-plan markers** on the timeline and scrubber, with a tooltip like "re-planned at 1:12:30 — thermocycle on tc2 ran 9:05 long; projected push 6:40 > 5:00" *(Claude's call)*
- all times shown as `h:mm:ss`
- a default playback speed scaled for seconds, so a ~2-hour run plays in about 2 minutes (≈60 s/s), still adjustable

The v1 multi-run report must keep working for the v1 commands.

## 9. Interface *(Claude's call)*

A new subcommand; v1's `run` and `scale` are unchanged:

```
python -m workcell live WORKCELL BATCH [--seed N=0] [--replan-threshold SECONDS=300]
    [--replan-time-limit S=2] [--time-limit S=30] [--noise 0.05] [--delay-prob 0.02]
    [--out PATH=out/live.html]
```

It prints a short summary: planned versus actual makespan, number of re-plans, completed workflows, and the report path. Library entry point: `run_live(workcell, specs, seed=..., ...) -> LiveSession`.

The README gets a v2 section. `docs/reports/` gets a live example report (`pcr10_live.html`) with a seed that produces at least one re-plan, linked from the README.

## 10. Constraints

- Python and dependencies are the same as v1, and the v1 test suite must keep passing.
- Reuse v1 pieces: compiler, `transport_instructions`, the CP-SAT model, validator, world, and report. Extend them rather than fork them. In particular, CP-SAT re-planning should be the v1 model plus "current state" inputs (fixed blocks, current plate locations, `now`), not a second model.
- The report stays self-contained.

## 11. Left to the builder

- Internal structure of the live executor and its module layout (e.g. `workcell/live.py` or `workcell/sim/live.py`).
- How the CP-SAT model accepts the current state (parameters versus a sub-batch).
- The deterministic CP-SAT configuration.
- Report visual details for re-plan markers.
