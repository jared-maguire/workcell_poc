# Coarse Time Grid + Rolling Planning Window Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Faster live scheduling: CP-SAT plans on a 10 s grid and only a few hours ahead; measure on the existing three-batch library prep demos (one and two arms).

**Architecture:** `_Model` gains `grid` (seconds per model time unit) and `window` (seconds ahead). Durations are rounded up to the grid, fixed times floored, `now` rounded up, starts scaled back to seconds; the idle-hold bound becomes `(L - grid + 1) // grid` units so real gaps stay ≤ L. With a window, the model contains only fixed blocks and unstarted blocks whose earliest start (deps only, from now) is before `now + window`; stays/borrows whose closing block is outside are open to the model horizon; the plan is `partial`. The live executor extends the plan every `window / 2` (reason `"horizon"`, always adopted) and whenever nothing planned is left.

**Tech Stack:** Python, OR-Tools CP-SAT. **Spec:** user direction 2026-10-31 ("switch to 10-second planning and just a few hours into the future… observe scheduler performance on the existing 7 minute workflow"); no separate design doc.

## Global Constraints

- Live defaults: `grid=10`, `window=4 h`; CLI `--grid SECONDS`, `--window-hours H` (0 = no window). v1 `run` and `plan_cpsat`/`replan_cpsat` defaults (`grid=1`, `window=None`) unchanged.
- Plans stay valid in real seconds: rounding only lengthens blocks.
- `Plan.partial` (new, default False): the validator skips the completeness check for partial plans.

## Review Focus

- Back-to-back fixed blocks at non-grid times must not overlap in the model (floor both ends). Test: Task 1 `test_grid_replan_fixed_offgrid`.
- Idle-hold under the grid: real gaps ≤ limit. Test: Task 1 `test_grid_idle_hold_valid`.
- A plate placed inside the window whose removal is outside keeps its nest (open stay). Test: Task 2 `test_window_open_stays_block_nest`.
- Live run with a window never stalls with unplanned blocks left. Test: Task 2 `test_window_live_completes`.
- Determinism unchanged (same seed same run). Covered by existing tests + Task 2 live test run twice.

### Task 1: Grid
- Tests (`tests/test_grid.py`): `test_grid_plan_valid` (timed_batch(2), grid=10: valid, starts multiples of 10), `test_grid_replan_fixed_offgrid`, `test_grid_idle_hold_valid` → implement → full suite → commit.

### Task 2: Window
- Tests: `test_window_plan_partial` (timed_batch(3), window=1 h: partial, subset scheduled, validate == []), `test_window_open_stays_block_nest`, `test_window_live_completes` (execute_live with window: all blocks complete, as-run valid, some `"horizon"` re-plans) → implement → commit.

### Task 3: Live defaults, CLI, report, measurements
- `run_live(grid=10, window=4*3600)`, CLI flags, live payload/report show grid/window and "planned makespan" as "—" for a partial first plan; run both demos old vs new; README/CLAUDE.md/FUTURE.md with the measurements → commit.
