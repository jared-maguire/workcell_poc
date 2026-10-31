# CLAUDE.md

Simulation playground for lab workcell control software: a workflow DSL is compiled into synchronized instruction blocks, planned by greedy or CP-SAT planners, executed on simulated instruments, and rendered as a self-contained HTML report. Nothing talks to hardware. Design: `docs/superpowers/specs/2026-10-30-workcell-scheduler-design.md` (the spec is the authority; some rulings refined it, see "Invariants").

## Commands

```
python -m venv .venv && .venv/bin/pip install -e .     # Python 3.12+; ortools, pyyaml, pygments, pytest
.venv/bin/pip install -e '.[dev]'                       # adds pytest, pytest-xdist
.venv/bin/pytest -q                                     # fast suite, parallel, ~1 min (skips slow)
.venv/bin/pytest -q -m slow                             # CP-SAT-heavy tests, ~1–2 min
.venv/bin/python -m workcell run examples/workcell.yaml examples/pcr_batch.py --out out/report.html
.venv/bin/python -m workcell scale --workflows 100 --time-limit 60 --out out/scale100.html   # ~4 min
.venv/bin/python -m workcell live examples/workcell.yaml examples/pcr_live.py --seed 0     # v2, ~1 min
```

`--mode` is repeatable: `greedy[:round_robin|:fifo]`, `greedy_hotel[:round_robin|:fifo]`, `cpsat[:makespan|:sum_completion]`. Exit code 2 = config/compile/input error; deadlocks and alarms are results (exit 0).

## Pipeline (one module per stage, pure data between stages)

`config.py` (YAML → `Workcell`) → `dsl.py` + `library.py` (Python workflow functions → `WorkflowSpec`) → `compiler.py` / `ir.py` (→ `CompiledBatch` of `Block` DAGs) → `planners/` (→ `Plan`) → `validate.py` → `sim/executor.py` + `sim/world.py` (→ `Trace`) → `stats.py` → `report/` (single HTML). `run.py` orchestrates; `cli.py` wraps it; `scenarios.py` generates scale workcells.

Planners never touch the simulator or report; a `Plan` (bound blocks + start times + bindings + status) is all downstream code sees.

## v2 live mode (`workcell/live/`, branch v2)

- `noise.py` seeded actual durations (draw depends only on seed, block id, instruction index) · `order.py` plan-order graph (`plan_preds`: deps, per-instrument/plate order, nest handoffs) + `project`/`max_push` · `executor.py` event loop (jumps between events; follows the plan as an order; detects long delays at block finish; adopts a re-plan only if it beats sliding by ≥ threshold, else the projection becomes the new baseline) · `session.py` `run_live`, as-run plan, `LiveStats`.
- **Times:** integer seconds. Only `@workflow(times=...)` workflows use rules 1–2 (op `seconds=`/thermocycle program sum, then the workflow table); everything else falls back to workcell `durations`, exactly as v1.
- **CP-SAT in live mode:** `plan_cpsat(..., deterministic=True)` = 8 interleaved workers, seed 0, deterministic-time limit, solved in a child process with retry (OR-Tools 9.15 on Python 3.14 sometimes aborts with heap corruption). Same inputs → same plan, across processes. Initial plan is warm-started with `hint="serial"` (v2 never uses greedy). Re-plans: `replan_cpsat` (phase 1 makespan, phase 2 churn with makespan held).
- **Validation:** the as-run schedule (actual starts/durations) must pass `validate_plan(asrun, batch, check_durations=False)`. In live mode lock waits are normal (not a bug signal). `Plan.end()` uses planned durations — use actual ends for started blocks.

## Invariants (break these and the planners, validator, and executor disagree)

- **Blocks:** the compiler groups instructions into blocks that start and end with every plate resting in a nest. A block's lock set = `block.instruments` (derived from its instructions; every block `check_status`es each instrument whose nest it touches) ∪ `block.plates`. The compiler never emits acquire/release; the executor locks all-or-nothing.
- **Block ids** are `f"{workflow_id}/{n:02d}"` in compile order; workflow ids are `f"{name}-{batch_index}"`. Tests hard-code PCR ids `pcr-0/00..15`.
- **Instrument variables** are `?name` / `?name.suffix` (e.g. `?tc0`, `?stamp.src`); single-candidate vars are substituted at compile time. Nest ids: `<instrument>` or `stamp1.src`/`stamp1.dst`.
- **Occupancy:** a plate occupies a nest from the START of the block that places it to the START of the block that removes it (initial stays from t=0). `plan.plate_stays` is the one definition — validator, greedy, CP-SAT, and stats all use it. (The spec originally said "from the END"; that let a plate be placed onto an occupied nest.)
- **Validator is the oracle:** every planner's plan must pass `validate_plan(plan, batch) == []` and execute with no alarm and no lock waits. Planner-inserted blocks are only allowed as greedy_hotel `park` blocks (`inserted=True`, id `<block>.park`; the parked move keeps its id/dst, src becomes the hotel, gains the park dep).
- Times are integer minutes; durations come from the workcell's `durations` (`<type>.<op>`, then `<op>`, then `default`). The example sets `check_status: 0`.

## Expected behavior that looks like a bug but isn't

- Pure `greedy` deadlocks on ≥2 PCR workflows under both priorities (round_robin at t=7, fifo at t=16). That is the point of the baseline.
- `greedy_hotel` lets a stamper serve one workflow at a time (parking alone still deadlocked). It can still deadlock if the hotel fills ("hotel full" in the wait reasons).
- CP-SAT stops at its time limit and is nondeterministic: makespans for the same batch vary run to run, and "feasible" means not proven optimal.
- Stats "waiting" = latency − Σ block durations and can be negative because a workflow's blocks overlap (known; follow-up).

## Tests

TDD, pytest. Fixtures in `tests/conftest.py` (`SMALL` workcell, `workcell_small`, `pcr_specs(n)`, `compiled_small(n)`); shared helpers (`sequential`, `VALID`, `data_of`) in `tests/helpers.py`, imported as `from helpers import ...`. New planner tests should assert `validate_plan(...) == []` and a clean `execute(...)`.

## Report

`workcell/report/` = `build.py` (Python → JSON contract) + `template.html` / `report.js` / `report.css`, inlined at build time. Must stay self-contained (no network URLs; a test enforces it). JSON is embedded with `<` escaped as `<`. For browser checks, `file://` is blocked in Playwright — serve the directory with `.venv/bin/python -m http.server` instead.

`docs/reports/pcr10.html` is a committed example report, served by GitHub Pages at https://jared-maguire.github.io/workcell_poc/docs/reports/pcr10.html. After report changes, regenerate it with the `run` command (`--out docs/reports/pcr10.html`) and update the README results table (CP-SAT numbers shift between runs). `out/` is gitignored scratch.
