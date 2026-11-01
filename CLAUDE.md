# CLAUDE.md

Simulation playground for lab workcell control software: a workflow DSL is compiled into synchronized instruction blocks, planned by greedy or CP-SAT planners, executed on simulated instruments, and rendered as a self-contained HTML report. Nothing talks to hardware. Design: `docs/superpowers/specs/2026-10-30-workcell-scheduler-design.md` (the spec is the authority; some rulings refined it, see "Invariants").

## Commands

```
python -m venv .venv && .venv/bin/pip install -e .     # Python 3.12+; ortools, pyyaml, pygments, pytest
.venv/bin/pip install -e '.[dev]'                       # adds pytest, pytest-xdist
.venv/bin/pytest -q                                     # fast suite, parallel, ~20 s (skips slow)
.venv/bin/pytest -q -m slow                             # CP-SAT-heavy tests, ~30 s
.venv/bin/python -m workcell run examples/workcell.yaml examples/pcr_batch.py --out out/report.html
.venv/bin/python -m workcell scale --workflows 100 --time-limit 60 --out out/scale100.html   # ~4 min
.venv/bin/python -m workcell live examples/workcell.yaml examples/pcr_live.py --seed 0     # live, ~2 s
```

`--mode` is repeatable: `jit`, `greedy[:round_robin|:fifo]`, `greedy_hotel[:round_robin|:fifo]`, `cpsat[:makespan|:sum_completion]`. Exit code 2 = config/compile/input error; deadlocks and alarms are results (exit 0).

## Pipeline (one module per stage, pure data between stages)

`config.py` (YAML → `Workcell`) → `dsl.py` + `library.py` (Python workflow functions → `WorkflowSpec`) → `compiler.py` / `ir.py` (→ `CompiledBatch` of `Block` DAGs) → `planners/` (→ `Plan`) → `validate.py` → `sim/executor.py` + `sim/world.py` (→ `Trace`) → `stats.py` → `report/` (single HTML). `run.py` orchestrates; `cli.py` wraps it; `scenarios.py` generates scale workcells.

Planners never touch the simulator or report; a `Plan` (bound blocks + start times + bindings + status) is all downstream code sees.

## Live mode (`workcell/live/`) and the just-in-time dispatcher

- **Dispatcher** (`planners/dispatch.py`, `plan_dispatch`; spec `docs/superpowers/specs/2026-10-31-jit-dispatch-design.md`). It subclasses the greedy planner and reuses its locks, nests and borrows, and its one-workflow-per-stamper rule.
  - **Priority:** workflows under way first, then the most remaining work (`remaining_work`), then batch order.
  - **Bindings:** tried shortest route first.
  - **Parking:** a plate goes to the nearest parking spot with room (`parking_spots(near=...)`), both when its destination is full and under the idle rule. The idle rule fires at `max_idle_hold - 60` s idle, when the plate's next block is a single move out.
  - **Parked moves:** a parked plate's removal is rewritten (`park_pair`) only after binding, so two-arm routes are concrete.
  - **Actual times:** with `actual`, blocks run with actual durations. Decisions only read finished blocks' end times, so a dispatch with noisy times *is* a live run.
  - **v1:** mode `jit` (in the defaults, multi-arm ok).
- **`session.py` `run_live`:**
  1. Forecast: dispatch with planned times.
  2. As-run: dispatch with `noise.actual_block` times.
  3. Replay the as-run plan in `sim/executor.execute`.
  4. Validate it, and report `LiveStats`.

  No re-planning. `noise.py` draws depend only on seed, block id and instruction index.
- **Removed 2026-10-31:**
  - live CP-SAT re-planning (executor, plan-order graph, `ReplanState`)
  - grid and window
  - the CP-SAT idle-hold model

  Reason: a dispatcher matched or beat it in about 0.1 s. Plain CP-SAT stays for `run` (`deterministic=True` solves go to one persistent crash-isolated solver process, `_Worker`).
- **Times:** integer seconds. Only `@workflow(times=...)` workflows use rules 1–2 (op `seconds=`/thermocycle program sum, then the workflow table); everything else falls back to workcell `durations`, exactly as v1.
- **Idle hold** (`max_idle_hold`, live default 300 s):
  - `idle.py` defines gaps: a plate in an instrument nest between two of its own blocks.
  - `parking.py` builds park blocks in the greedy_hotel shape: `<removal>.park`, with the removal rewritten to start from the spot.
  - Storage ops are 60 s in `pcr_live.py`; hotels have none.
- **Lab steps** (`w.step(itype, op, plate[, dst], seconds=)`): generic timed steps on `STEP_TYPES` (config.py: liquid_handler, heated_shaker, plate_sealer, plate_peeler, centrifuge, plate_reader, vacuum_concentrator). Two plates only on `TWO_POSITION_TYPES` (head_stamper, liquid_handler: `.src`/`.dst`; one plate → `.dst`); a workflow keeps one variable per two-position type (`?lh`), numbered ones otherwise (`?hs0`…), and consecutive steps on the instrument type a plate already sits on stay there. The world only checks the plates are in the instrument. Demo: `examples/libprep_capture.py` + `libprep_workcell.yaml` (3 batches, `live`, ~2 s).
- **Multiple arms** (spec `docs/superpowers/specs/2026-10-31-multi-arm-design.md`): arms take `reach:` lists; only `handoff` instruments may be reached by several arms (config validates). `compiler.route` finds the arm chain; a cross-zone move is one relay block (all arms/handoffs on the route locked). When a move's route depends on a binding (`AmbiguousRoute`), the block carries `variants` (one concrete instruction list per binding; `Block.bind` picks; unbound `instructions` = first-candidates variant). CP-SAT sizes/locks variants per binding; its multi-arm serial warm start binds nearest candidates. greedy/greedy_hotel stay single-arm; the dispatcher handles any number.
- **Separate groups:** arms need not all connect. `Workcell.group(instrument)` is the set connected through arms and handoffs. A workflow's variable candidates and its parking spots are limited to its plates' group; plates starting in two groups is a `CompileError`.
  - Example: `examples/make_libprep_lines.py` generates `libprep_10k.yaml` (4 separate lines) for `libprep_10k.py` (105 batches).
  - Run it with `live --release-every 823`.
  - Linking lines through handoffs hurt: plates drifted across lines and needed multi-arm relays.
- **Release times:** `plan_dispatch(release={wf: t})`, or `run_live(release_every=)`. Releasing a whole day at once makes the dispatcher start everything and thrash.
- **Dispatcher speed:** it keeps the ready set, nest occupancy and busy/locked sets up to date incrementally; don't reintroduce whole-batch scans (`_Greedy._ready`/`_occupants`) on hot paths.
- **Validation:** the as-run schedule (actual starts/durations) must pass `validate_plan(asrun, batch, check_durations=False)` and replay with no alarm and no lock waits.

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
