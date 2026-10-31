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
.venv/bin/python -m workcell live examples/workcell.yaml examples/pcr_live.py --seed 0     # v2, ~40 s
.venv/bin/python tools/bench_mini.py 4                                                      # scheduler experiments, ~30 s
```

`--mode` is repeatable: `greedy[:round_robin|:fifo]`, `greedy_hotel[:round_robin|:fifo]`, `cpsat[:makespan|:sum_completion]`. Exit code 2 = config/compile/input error; deadlocks and alarms are results (exit 0).

## Pipeline (one module per stage, pure data between stages)

`config.py` (YAML → `Workcell`) → `dsl.py` + `library.py` (Python workflow functions → `WorkflowSpec`) → `compiler.py` / `ir.py` (→ `CompiledBatch` of `Block` DAGs) → `planners/` (→ `Plan`) → `validate.py` → `sim/executor.py` + `sim/world.py` (→ `Trace`) → `stats.py` → `report/` (single HTML). `run.py` orchestrates; `cli.py` wraps it; `scenarios.py` generates scale workcells.

Planners never touch the simulator or report; a `Plan` (bound blocks + start times + bindings + status) is all downstream code sees.

## v2 live mode (`workcell/live/`, branch v2)

- `noise.py` seeded actual durations (draw depends only on seed, block id, instruction index) · `order.py` plan-order graph (`plan_preds`: deps, per-instrument/plate order, nest handoffs) + `project`/`max_push` · `executor.py` event loop (jumps between events; follows the plan as an order; checks for long delays whenever any block finishes — a still-running overrun counts, estimated as ending now — and blames the most-overrun block; adopts a re-plan only if it beats sliding by ≥ threshold, else the projection becomes the new baseline) · `session.py` `run_live`, as-run plan, `LiveStats`.
- **Times:** integer seconds. Only `@workflow(times=...)` workflows use rules 1–2 (op `seconds=`/thermocycle program sum, then the workflow table); everything else falls back to workcell `durations`, exactly as v1.
- **CP-SAT in live mode:** `plan_cpsat(..., deterministic=True)` = 8 interleaved workers, seed 0, deterministic-time limit, solved in a child process with retry (OR-Tools 9.15 on Python 3.14 sometimes aborts with heap corruption). Same inputs → same plan, across processes. Initial plan is warm-started with `hint="serial"` (v2 never uses greedy). Re-plans: `replan_cpsat` (phase 1 makespan, phase 2 churn with makespan held).
- **Idle hold** (`max_idle_hold`, live default 300 s; spec `docs/superpowers/specs/2026-10-31-max-idle-hold-design.md`): `idle.py` defines gaps (a plate in an instrument nest between two of its own blocks) and compile-level `hold_chains`; `parking.py` builds park blocks (greedy_hotel shape: `<removal>.park`, removal rewritten to start from the spot). CP-SAT models each chain's removal as direct or parked at any hotel/storage nest; initial plans enforce the limit hard via `plan_idle_hold` (half budget without the limit, half with it warm-started — a cold limited solve barely beats the serial plan), re-plans soft. Objective adds park delay + `PARK_COST` per park (a total-idle-time term cost ~3 h of plan quality). The executor also re-plans on new projected idle violations and does "lab-tech parks": a waiting plate whose removal hasn't started by its deadline goes to the hotel at once (order graph edited in place — re-sorting from shifted starts mis-pairs nest handoffs). Storage ops are 60 s in `pcr_live.py`; hotels have none.
- **Lab steps** (`w.step(itype, op, plate[, dst], seconds=)`): generic timed steps on `STEP_TYPES` (config.py: liquid_handler, heated_shaker, plate_sealer, plate_peeler, centrifuge, plate_reader, vacuum_concentrator). Two plates only on `TWO_POSITION_TYPES` (head_stamper, liquid_handler: `.src`/`.dst`; one plate → `.dst`); a workflow keeps one variable per two-position type (`?lh`), numbered ones otherwise (`?hs0`…), and consecutive steps on the instrument type a plate already sits on stay there. The world only checks the plates are in the instrument. Demo: `examples/libprep_capture.py` + `libprep_workcell.yaml` (3 batches, `--time-limit 60`, ~5 min).
- **Multiple arms** (spec `docs/superpowers/specs/2026-10-31-multi-arm-design.md`): arms take `reach:` lists; only `handoff` instruments may be reached by several arms (config validates). `compiler.route` finds the arm chain; a cross-zone move is one relay block (all arms/handoffs on the route locked). When a move's route depends on a binding (`AmbiguousRoute`), the block carries `variants` (one concrete instruction list per binding; `Block.bind` picks; unbound `instructions` = first-candidates variant). CP-SAT sizes/locks variants per binding; no CP-SAT park option for variant removals; re-plans take started parks and their rewritten removals as given (`ReplanState.blocks`). Multi-arm serial warm start binds nearest candidates. Greedy planners stay single-arm.
- **Planning speed:** live CP-SAT plans on a grid (`grid=10`: durations rounded up, fixed times floored, `now` rounded up, starts scaled back; idle limit `(L - g + 1) // g` units) and only `window` seconds ahead (`_in_window`: fixed + unstarted blocks whose deps-only earliest start is < now + window; stays/borrows closed outside it end at `max(start, open_end)`; blocks placed at/after `open_end` are cut; `Plan.partial`). The executor extends the plan every window / 2 (reason `"horizon"`). Solves go to one persistent solver process (`_Worker`, same crash/retry semantics). Budgets are deterministic time (~2–5× wall); live defaults 8 initial / 0.5 per re-plan phase. Measured: a hand-added thermocycler bound and `relative_gap_limit` did not help.
- **Validation:** the as-run schedule (actual starts/durations) must pass `validate_plan(asrun, batch, check_durations=False)`. In live mode lock waits are normal (not a bug signal). `Plan.end()` uses planned durations — use actual ends for started blocks.

## Invariants (break these and the planners, validator, and executor disagree)

- **Blocks:** the compiler groups instructions into blocks that start and end with every plate resting in a nest. A block's lock set = `block.instruments` (derived from its instructions; every block `check_status`es each instrument whose nest it touches) ∪ `block.plates`. The compiler never emits acquire/release; the executor locks all-or-nothing.
- **Block ids** are `f"{workflow_id}/{n:02d}"` in compile order; workflow ids are `f"{name}-{batch_index}"`. Tests hard-code PCR ids `pcr-0/00..15`.
- **Instrument variables** are `?name` / `?name.suffix` (e.g. `?tc0`, `?stamp.src`); single-candidate vars are substituted at compile time. Nest ids: `<instrument>` or `stamp1.src`/`stamp1.dst`.
- **Occupancy:** a plate occupies a nest from the START of the block that places it to the START of the block that removes it (initial stays from t=0). `plan.plate_stays` is the one definition — validator, greedy, CP-SAT, and stats all use it. (The spec originally said "from the END"; that let a plate be placed onto an occupied nest.)
- **Validator is the oracle:** every planner's plan must pass `validate_plan(plan, batch) == []` and execute with no alarm and no lock waits. Planner-inserted blocks are only allowed as greedy_hotel `park` blocks (`inserted=True`, id `<block>.park`; the parked move keeps its id/dst, src becomes the hotel, gains the park dep).
- Times are integer seconds (v1 durations of 1 = 1 s; the v1 report still labels them "min"); durations come from the workcell's `durations` (`<type>.<op>`, then `<op>`, then `default`), or for `@workflow(times=...)` workflows from the workflow first. The example sets `check_status: 0`.

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
