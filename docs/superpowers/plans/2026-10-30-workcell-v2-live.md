# Workcell v2 (Live Re-planning) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run a batch with realistic per-step times (seconds, set in the workflow), seeded random actual durations, and live execution that waits out small delays and re-plans with CP-SAT on long ones, then report what actually happened.

**Architecture:** Timed workflows feed the existing compiler, which now produces durations in seconds. A new `workcell/live/` package holds the pieces of a run:
- `noise.py`: the actual-duration sampler.
- `order.py`: the plan-order graph and its projection.
- `executor.py`: the live event loop, built on the v1 `World`.
- `session.py`: `run_live`, as-run validation and live stats.

CP-SAT keeps the v1 model, extended to take a `ReplanState` (fixed past and running blocks, a time floor, fixed bindings) and a deterministic mode. The v1 report gains an optional `live` section.

**Tech Stack:** Python 3.14 in `.venv`, OR-Tools CP-SAT 9.15, pytest, vanilla JS.

**Spec:** `docs/superpowers/specs/2026-10-30-workcell-v2-live-design.md` (read `CLAUDE.md` for the v1 invariants)

## Global Constraints

- Use `.venv/bin/python` / `.venv/bin/pytest`. Work on branch `v2`. **All v1 tests must keep passing** after every task.
- All times are **integer seconds**. v1's 1-unit durations count as 1 s, with no numeric changes to v1.
- Duration lookup for an instruction, in order:
  1. the op's own `seconds=` argument, or the program sum for a thermocycle
  2. the workflow `times` table, trying `"<type>.<op>"` then `"<op>"`
  3. the workcell `durations`, trying `"<type>.<op>"`, then `"<op>"`, then `"default"`

  Rules 1–2 apply **only** to workflows declared `@workflow(times=...)`. Untimed workflows behave exactly as in v1.
- A thermocycle's time in a timed workflow is `round(sum(minutes for _, minutes in program) * 60)`. `PCR_PROGRAM` gives 4020.
- Noise defaults: `noise=0.05` (σ of the relative Normal), `delay_prob=0.02`, and `delay_range=(2.0, 5.0)` (multiplier).
  - A draw depends only on `(seed, block_id, instruction_index)`. Use `random.Random(f"{seed}:{block_id}:{index}")`.
  - Results are rounded to int and are ≥ 0. A 0 s instruction stays 0.
- Live defaults: `seed=0`, `replan_threshold=300`, `replan_time_limit=2.0` (deterministic seconds, **per phase**), `time_limit=10.0` (initial plan, warm-started with `hint="serial"`). Good-enough plans are the goal; no further scheduler tuning.
- **No churn** (user direction): a re-plan runs in two phases (makespan, then churn with makespan held), and is adopted only if it beats the current projected makespan by ≥ `replan_threshold`; otherwise the event status is `"not_worth_it"` and execution keeps sliding. Unstarted plates may be reassigned to other instruments (use idle hardware) when that buys an earlier finish.
- In live mode CP-SAT is deterministic: `num_workers=8`, `interleave_search=True`, `random_seed=0`, `max_deterministic_time = time_limit`, solved in an isolated child process with retry (see controller rulings R5, R7). Same inputs give the same result, across processes too.
- R4 occupancy, the block-id format, the lock model and the validator-as-oracle rules from `CLAUDE.md` all still hold. In v2, lock waits are normal and are not a bug signal.
- Commit trailers on every commit:
  ```
  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01Mkw5vs3LVo54ipZuHGysLE
  ```

## Review Focus

1. **A delay on the very last block.** No unstarted blocks remain, so nothing can be pushed and nothing re-plans. The run completes normally. (Test in Task 5.)
2. **CP-SAT returns no solution on a re-plan.** Execution keeps following the current plan, the `ReplanEvent` records `status="no_solution"`, and the run still completes. (Test in Task 5.)
3. **A running block has already overrun its planned end when another block triggers a re-plan.** Its `ReplanState` end is `max(now, start + planned)`, the re-plan is feasible, and the as-run schedule validates. (Test in Task 4.)
4. **A batch mixing timed and untimed workflows.** Each block takes its own workflow's timing. (Test in Task 1.)
5. **A zero-noise run** (`noise=0`, `delay_prob=0`). The executed spans equal the initial plan's starts and ends exactly, with zero re-plans. (Test in Task 5.)

---

### Task 1: Timed workflows

**Files:**
- Modify: `workcell/dsl.py`, `workcell/compiler.py`
- Create: `examples/pcr_live.py`, `tests/test_timed.py`

**Interfaces:**
- Produces:
  - `workflow` works both as `@workflow` and as `@workflow(times={...})`.
  - `WorkflowSpec` gains `times: dict[str, int] | None = None`. A timed workflow has a dict there, possibly empty; an untimed one has `None`.
  - `Workflow.stamp(src, dst, after=None, seconds: int | None = None)`. A non-`None` `seconds` is stored as `params["seconds"]`.
  - In the compiler, the `Instruction.duration` of every instruction in a timed workflow's blocks follows the Global Constraints lookup. The op instructions `stamp` and `run_program` use rule 1. `transport_instructions(..., times: dict[str, int] | None = None)` gains an optional table for rule 2.
  - `examples/pcr_live.py` contains:
    - `TIMES = {"arm.move": 10, "open_door": 5, "close_door": 5, "scan": 4, "retrieve": 20, "store": 20, "check_status": 0}`
    - `@workflow(times=TIMES) def pcr_live(w, sample, primer)`, structured exactly like `library.pcr`, with both stamps at `seconds=60`
    - `batch()`, returning 10 workflows: `pcr_live(sample=f"S{i+1:04d}", primer=f"P{i % 2 + 1:04d}")`

- [ ] **Step 1: Write failing tests** in `tests/test_timed.py`. Load the example module with `load_batch("examples/pcr_live.py")`, using a path built from the test file's location.

```python
def test_timed_pcr_block_durations(workcell_small):
    specs, _ = load_batch(EXAMPLES / "pcr_live.py")
    b = compile_batch(workcell_small, specs[:1])
    d = {blk.id: blk.duration for blk in b.workflows[0].blocks}
    assert d["pcr_live-0/00"] == 30                                      # fetch: retrieve 20 + move 10
    assert [d[f"pcr_live-0/{i:02d}"] for i in (1, 2, 6, 11, 12, 15, 13)] == [4, 10, 60, 20, 4020, 40, 30]

def test_untimed_workflow_unchanged(compiled_small):
    assert compiled_small(1).block("pcr-0/12").duration == 1            # v1 rules only

def test_mixed_batch(workcell_small):
    specs, _ = load_batch(EXAMPLES / "pcr_live.py")
    b = compile_batch(workcell_small, [specs[0], pcr(sample="S0002", primer="P0002")])
    assert b.block("pcr_live-0/12").duration == 4020 and b.block("pcr-1/12").duration == 1

def test_times_table_and_seconds_override(workcell_small):
    @workflow(times={"arm.move": 7, "scan": 3})
    def t(w):
        p = w.own("S0001"); q = w.own("S0002"); w.stamp(src=p, dst=q, seconds=11)
    b = compile_batch(workcell_small, [t()])
    blk = b.workflows[0].blocks
    assert blk[0].duration == 1 + 7          # retrieve falls back to workcell default 1; move 7
    assert blk[1].duration == 3
    assert next(x for x in blk if x.kind == "op").duration == 11

def test_bare_decorator_still_works():
    @workflow
    def u(w):
        w.own("S0001")
    assert u().times is None
```

Expected durations: fetch 30, scan 4, move to stamper 10, stamp 60, move into the thermocycler 20, run 4020, return from the thermocycler 40, return from the stamper 30. Workflow ids come from the function name (`pcr_live-0`).

- [ ] **Step 2:** Run `.venv/bin/pytest tests/test_timed.py -v`. Expect FAIL.
- [ ] **Step 3: Implement.** Pass the owning spec's `times` into `_WorkflowCompiler` and `transport_instructions`. Keep `Workcell.duration` as the rule-3 fallback.
- [ ] **Step 4:** Run `tests/test_timed.py` and then the full suite. Expect PASS, with every v1 test unchanged.
- [ ] **Step 5:** Commit with the message `feat: timed workflows (seconds, per-workflow times)`.

---

### Task 2: Actual-duration sampler

**Files:**
- Create: `workcell/live/__init__.py`, `workcell/live/noise.py`, `tests/test_noise.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) NoiseParams(seed: int = 0, noise: float = 0.05, delay_prob: float = 0.02, delay_range: tuple[float, float] = (2.0, 5.0))`
  - `actual_duration(params: NoiseParams, block_id: str, index: int, planned: int) -> int`, using one `random.Random(f"{seed}:{block_id}:{index}")`:
    1. Draw `u = rng.random()`.
    2. If `u < delay_prob`, the result is `planned * rng.uniform(*delay_range)`.
    3. Otherwise it is `planned * (1 + rng.gauss(0, noise))`.
    4. Round, then take `max(0, ·)`. A `planned` of 0 returns 0.
  - `actual_block(params, block: Block) -> list[int]`, giving the per-instruction actual durations.

- [ ] **Step 1: Write failing tests** in `tests/test_noise.py`:

```python
def test_deterministic_and_order_independent():
    p = NoiseParams(seed=7)
    a = [actual_duration(p, "pcr-0/12", 1, 4020) for _ in range(3)]
    assert a[0] == a[1] == a[2]
    assert actual_duration(p, "pcr-0/12", 1, 4020) != actual_duration(NoiseParams(seed=8), "pcr-0/12", 1, 4020) \
        or actual_duration(p, "pcr-0/13", 1, 4020) != a[0]

def test_zero_stays_zero_and_no_noise_is_identity():
    assert actual_duration(NoiseParams(seed=1), "x/00", 0, 0) == 0
    assert actual_duration(NoiseParams(noise=0, delay_prob=0), "x/00", 3, 4020) == 4020

def test_distribution_shape():
    p = NoiseParams(seed=3)
    xs = [actual_duration(p, f"b/{i:04d}", 0, 1000) for i in range(5000)]
    longs = sum(x >= 2000 for x in xs) / len(xs)
    assert 0.01 < longs < 0.03
    normal = sorted(x for x in xs if x < 2000)
    assert 930 < normal[len(normal) // 2] < 1070 and min(xs) >= 0
```

- [ ] **Step 2:** Run the tests and confirm they fail.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run the tests and confirm they pass.
- [ ] **Step 5:** Commit with the message `feat: seeded actual-duration sampler`.

---

### Task 3: CP-SAT with current state, and deterministic mode

**Files:**
- Modify: `workcell/planners/cpsat.py`
- Create: `tests/test_cpsat_state.py`

**Interfaces:**
- Produces:
  - `@dataclass ReplanState(now: int, fixed: dict[str, tuple[int, int]], bindings: dict[str, dict[str, str]], hint: dict[str, int] = field(default_factory=dict))`. `fixed` maps a block id to `(start, end)` for done and running blocks. `bindings` holds the var bindings that must stay.
  - `plan_cpsat(batch, objective="makespan", time_limit=30.0, hint=True, state: ReplanState | None = None, deterministic: bool = False) -> Plan`
    - **Fixed blocks:** each block in `state.fixed` gets `start == s` and `end == e`, meaning its interval size is `e - s`, not its planned duration. Every other block gets `start >= state.now`.
    - **Bindings:** each `(wf, var) -> cand` in `state.bindings` forces `x[wf, var, cand] == 1`.
    - **Horizon:** `max([now] + fixed ends) + sum(planned durations of unfixed blocks)`.
    - **Hint:** if `state.hint` is non-empty, hint those starts and skip the greedy hint.
    - **Constraints:** everything else (deps, locks, R4 stays, borrows) is unchanged from v1. The fixed past is consistent because it actually happened under those rules.
    - `deterministic=True` sets `num_workers=1`, `random_seed=0` and `max_deterministic_time=time_limit`, and doesn't set a wall-time limit.
    - The returned `Plan.start` includes the fixed blocks, at their fixed starts.
- Consumes: v1 `_Model` internals. Extend `_Model.__init__(batch, state=None)`, using a per-block size map in place of `b.duration`.

- [ ] **Step 1: Write failing tests** in `tests/test_cpsat_state.py`. Add a factory fixture `timed_batch(n)` to `tests/conftest.py`: `compile_batch(workcell_small, load_batch(EXAMPLES / 'pcr_live.py')[0][:n])`, where `EXAMPLES` is the repo's `examples/` dir resolved from the conftest path. Later tasks reuse it.

```python
def test_deterministic_repeatable(timed_batch):
    b = timed_batch(3)
    p1 = plan_cpsat(b, time_limit=5, deterministic=True); p2 = plan_cpsat(b, time_limit=5, deterministic=True)
    assert p1.start == p2.start and p1.bindings == p2.bindings and validate_plan(p1, b) == []

def test_replan_respects_fixed_and_now(timed_batch):
    b = timed_batch(3); p0 = plan_cpsat(b, time_limit=5, deterministic=True)
    first = sorted(p0.start, key=p0.start.get)[:4]
    fixed = {bid: (p0.start[bid], p0.end(bid) + 7) for bid in first}       # they ran 7 s long
    now = max(e for _, e in fixed.values())
    bind = {wf: dict(v) for wf, v in p0.bindings.items()}
    p = plan_cpsat(b, time_limit=5, deterministic=True,
                   state=ReplanState(now=now, fixed=fixed, bindings=bind))
    assert p.status in ("optimal", "feasible")
    assert all(p.start[bid] == fixed[bid][0] for bid in fixed)
    assert all(p.start[bid] >= now for bid in p.start if bid not in fixed)
    assert p.bindings == bind

def test_running_block_overran(timed_batch):
    b = timed_batch(3); p0 = plan_cpsat(b, time_limit=5, deterministic=True)
    run = next(bid for bid in p0.start if b.block(bid).duration == 4020)
    s = p0.start[run]; now = s + 5000                                        # past its planned end
    done = {bid: (p0.start[bid], p0.end(bid)) for bid in p0.start if p0.end(bid) <= s and bid != run}
    state = ReplanState(now=now, fixed={**done, run: (s, max(now, s + 4020))},
                        bindings={wf: dict(v) for wf, v in p0.bindings.items()})
    p = plan_cpsat(b, time_limit=5, deterministic=True, state=state)
    assert p.status in ("optimal", "feasible")
```

- [ ] **Step 2:** Run the tests and confirm they fail.
- [ ] **Step 3:** Implement. Keep the v1 defaults identical: with `state=None` and `deterministic=False`, behaviour is unchanged.
- [ ] **Step 4:** Run the new tests, `tests/test_cpsat.py`, and the full suite. All should pass.
- [ ] **Step 5:** Commit with the message `feat: CP-SAT re-planning from current state; deterministic mode`.

---

### Task 4: Plan order and projection

**Files:**
- Create: `workcell/live/order.py`, `tests/test_order.py`

**Interfaces:**
- Produces:
  - `plan_preds(plan: Plan, batch: CompiledBatch) -> dict[str, list[tuple[str, str]]]`. It maps each block in `plan.start` to `(pred, when)` pairs, where `when` is `"end"` or `"start"`:
    - `"end"` covers the block's deps that are in the plan, plus, for each instrument in `block.instruments` and each plate in `block.plates`, the block planned immediately before it on that resource. Order is `(start, id)`.
    - `"start"` covers, for each move into a nest of capacity 1, the block that removes the nest's previous occupant (from `plate_stays` on `plan`). The block may start only after that removing block has *started*.
  - `project(plan, preds, actual_start: dict[str, int], actual_end: dict[str, int], now: int) -> dict[str, int]`, giving the projected start of every unstarted block in the plan.
    - Started blocks keep their actual start. Their end is `actual_end[bid]` if finished, else `max(now, actual_start[bid] + planned duration)`.
    - Unstarted blocks are processed in `(plan.start, id)` order, with `start = max(plan.start[b], now, max over preds of the pred's projected start or end)`.
  - `max_push(plan, projected) -> tuple[int, str | None]`, returning the largest `projected[b] - plan.start[b]` over unstarted blocks and the block that has it. It returns `(0, None)` when nothing is unstarted.

- [ ] **Step 1: Write failing tests** in `tests/test_order.py`, using `p0 = plan_cpsat(timed_batch(2), time_limit=5, deterministic=True)`:

```python
def test_preds_include_deps_and_resource_order(timed_batch):
    b = timed_batch(2); p = plan_cpsat(b, time_limit=5, deterministic=True); pr = plan_preds(p, b)
    for bid, blk in p.blocks.items():
        assert {d for d in blk.deps} <= {x for x, w in pr[bid] if w == "end"}
    arm = sorted((bid for bid, blk in p.blocks.items() if "arm1" in blk.instruments), key=lambda x: (p.start[x], x))
    for a, c in zip(arm, arm[1:]):
        assert (a, "end") in pr[c]

def test_projection_without_delay_is_plan(timed_batch):
    b = timed_batch(2); p = plan_cpsat(b, time_limit=5, deterministic=True); pr = plan_preds(p, b)
    proj = project(p, pr, actual_start={}, actual_end={}, now=0)
    assert proj == p.start and max_push(p, proj) == (0, None)

def test_delay_propagates(timed_batch):
    b = timed_batch(2); p = plan_cpsat(b, time_limit=5, deterministic=True); pr = plan_preds(p, b)
    first = min(p.start, key=lambda x: (p.start[x], x))
    proj = project(p, pr, {first: p.start[first]}, {first: p.end(first) + 400}, now=p.end(first) + 400)
    push, who = max_push(p, proj)
    assert push >= 400 and who is not None

def test_nothing_left_no_push(timed_batch):
    b = timed_batch(1); p = plan_cpsat(b, time_limit=5, deterministic=True)
    s = {bid: p.start[bid] for bid in p.start}; e = {bid: p.end(bid) + 999 for bid in p.start}
    assert max_push(p, project(p, plan_preds(p, b), s, e, now=max(e.values()))) == (0, None)
```

`max_push` returns `(0, None)` when the largest push is ≤ 0.

- [ ] **Step 2:** Run the tests and confirm they fail.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run the tests and confirm they pass.
- [ ] **Step 5:** Commit with the message `feat: plan-order graph and delay projection`.

---

### Task 4d: Two-phase, low-churn re-plan

**Files:**
- Modify: `workcell/planners/cpsat.py`
- Create: `tests/test_replan.py`

**Interfaces:**
- Consumes: `ReplanState` (with `hint` = projected starts and `hint_bindings` = the current plan's bindings), `plan_cpsat(..., deterministic=True)`.
- Produces: `replan_cpsat(batch: CompiledBatch, state: ReplanState, time_limit: float = 2.0, reassign_weight: int = 600) -> Plan`.
  1. **Phase 1:** `plan_cpsat(batch, objective="makespan", time_limit=time_limit, deterministic=True, state=state)`. If it is not optimal or feasible, return it unchanged.
  2. **Phase 2:** the same model with `makespan <= phase1.makespan`. Minimize churn, defined as `reassign_weight × (number of (wf, var) whose binding differs from state.hint_bindings, over vars used by unstarted blocks)` plus `Σ |start[b] − state.hint[b]|` over unstarted blocks that have a hint. Hint phase 2 with phase 1's solution, and give it the same time limit. If phase 2 isn't optimal or feasible, return phase 1's plan.
  3. Both phases are deterministic and isolated, as `plan_cpsat` already is. The returned `Plan` sets `solver_status` to `"<phase1>/<phase2>"`.
  4. Refactor as needed so phase 2 reuses `_Model`. For example, add `_Model.churn_objective(state, cap, weight)` alongside `objective()`. Don't duplicate the model build.

- [ ] **Step 1: Write failing tests** in `tests/test_replan.py`, built on `timed_batch(3)` and `p0 = plan_cpsat(b, time_limit=5, deterministic=True, hint="serial")`.

Helper `state_after_overrun(b, p0, extra) -> ReplanState`:
- Take `run`, the earliest-ending block whose duration is 4020. Let `s = p0.start[run]` and `now = s + 4020 + extra`.
- `fixed` holds every block with `p0.end(bid) <= s`, at its planned `(start, end)`, plus `run: (s, now)`.
- `bindings` holds only the (wf, var) pairs that some fixed block's instructions or moves use, at their `p0` values.
- `hint` holds, for each unfixed block, `max(p0.start[bid], now)`.
- `hint_bindings` holds all of `p0.bindings`.

Tests:
- `test_no_delay_no_churn`: with `extra=0`, `r = replan_cpsat(b, st, time_limit=2)`. Assert that it is optimal or feasible, that `r.bindings == p0.bindings`, and that `r.start[bid] == st.hint[bid]` for every unfixed block.
- `test_idle_thermocycler_used`:
  - Pick `extra=3000`, so the run's thermocycler stays busy for 3000 s longer.
  - Assert that a workflow whose thermocycle hasn't started, and which `p0` bound to the same thermocycler as `run`, is rebound to the other thermocycler.
  - Assert that `r.makespan` is no greater than phase 1's makespan. Expose phase 1 through a `_last_phase1` attribute on the module, or by calling `plan_cpsat` directly with the same state.
- `test_phase2_failure_falls_back_to_phase1`: monkeypatch the phase 2 solve so it returns no solution, then assert that the result equals the phase 1 plan's starts and bindings.
- `test_deterministic`: two calls with the same state give equal starts and bindings.

Plans whose fixed blocks have actual sizes different from planned can't go through `validate_plan` (it uses planned durations). Their validity is checked end to end through the live executor in Task 6, so these tests don't call the validator on them.

- [ ] **Step 2:** Run the tests and confirm they fail.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run the tests and confirm they pass. Then run the full suite.
- [ ] **Step 5:** Commit with the message `feat: two-phase low-churn re-plan`.

---

### Task 5: Live executor

**Files:**
- Create: `workcell/live/executor.py`, `tests/test_live_executor.py`

**Interfaces:**
- Consumes: Task 2 `NoiseParams` and `actual_block`; Task 3 `ReplanState`; Task 4 `plan_preds`, `project` and `max_push`; v1 `World`, `Trace`, `InstructionEvent`, `MoveEvent` and `Alarm`.
- Produces:
  - `@dataclass ReplanEvent(time: int, trigger: str, delay: int, push: int, pushed_block: str, solve_seconds: float, status: str, makespan_before: int, makespan_after: int | None, adopted: bool, reassigned: int)`. `status` is the solver's (`optimal`, `feasible`, `no_solution`, …) or `not_worth_it`. `reassigned` counts the bindings that changed when the plan was adopted.
  - `@dataclass LiveResult(trace: Trace, plans: list[Plan], replans: list[ReplanEvent], actual: dict[str, list[int]], bindings: dict[str, dict[str, str]])`. `actual` holds the per-block instruction durations used, and `bindings` the bindings in force at the end.
  - `execute_live(batch, plan: Plan, noise: NoiseParams, replan: Callable[[ReplanState], Plan], threshold: int) -> LiveResult`
- **Event loop** (integer seconds):
  1. **Start blocks.** A pending block starts at `t` if all of these hold:
     - `t >= current_plan.start[b]`
     - every `"end"` pred has finished and every `"start"` pred has started
     - all its locks (instruments ∪ plates) are free

     Candidates are tried in `(plan.start, id)` order. A started block's instructions run back to back using `actual_block` durations, applied to `World` at their start times exactly as in the v1 executor.
  2. **Finish blocks.** When a block finishes, record its span. If any blocks are still unstarted, compute `push, who = max_push(plan, project(...))`. If `push > threshold`:
     1. Build a `ReplanState`:
        - `now = t`
        - `fixed`: done blocks get their actual `(start, end)`; running blocks get `(start, max(t, start + planned))`
        - `bindings`: the current plan's bindings, kept only for vars used by started blocks
        - `hint = projected`
     2. Call `replan(state)` and time it with `time.perf_counter`.
     3. Adopt the new plan only if it is optimal or feasible **and** `makespan_before − new.makespan >= threshold`. On adoption, rebuild `plan_preds` and bind the not-yet-started blocks from the new plan. A solved plan that doesn't clear the bar gets `status="not_worth_it"` and `adopted=False`. A failed solve keeps its solver status, with `adopted=False`.
     4. Append a `ReplanEvent` in both cases. `delay` is the trigger block's actual minus planned duration. `makespan_before` is `max` over projected ends. `makespan_after` is the new plan's makespan, or `None`.

     The clock does not advance during the solve.
  3. **Alarms.** An `InstrumentError` stops the run with an `Alarm`, as in v1.
  4. **Stalls.** If the loop finds no event but blocks are pending, stop. The trace shows them as never started; this indicates a bug, and the tests guard against it.

- [ ] **Step 1: Write failing tests** in `tests/test_live_executor.py`. As-run validation arrives in Task 6, so these tests check the trace directly.

```python
def p0(b): return plan_cpsat(b, time_limit=5, deterministic=True, hint="serial")
def cp(b): return lambda st: replan_cpsat(b, st, time_limit=2)
QUIET = NoiseParams(noise=0, delay_prob=0)

def overrun(block_id, extra):
    """actual_block replacement: planned durations, plus `extra` s on block_id's longest instruction."""
    def f(noise, blk):
        ds = [i.duration for i in blk.instructions]
        if blk.id == block_id:
            ds[ds.index(max(ds))] += extra
        return ds
    return f

def test_zero_noise_matches_plan(timed_batch):
    b = timed_batch(3); p = p0(b); r = execute_live(b, p, QUIET, cp(b), 300)
    assert r.replans == [] and r.trace.alarm is None
    assert {bid: s for bid, (s, e) in r.trace.spans.items()} == p.start
    assert all(e == p.end(bid) for bid, (s, e) in r.trace.spans.items())

def test_noisy_run_completes(timed_batch):
    b = timed_batch(3); r = execute_live(b, p0(b), NoiseParams(seed=1), cp(b), 300)
    assert r.trace.alarm is None and r.trace.completed == {blk.id for blk in b.all_blocks()}

def test_long_delay_triggers_replan(timed_batch):
    b = timed_batch(3)
    with patch("workcell.live.executor.actual_block", overrun("pcr_live-0/12", 900)):
        r = execute_live(b, p0(b), QUIET, cp(b), 300)
    assert r.replans and r.replans[0].trigger == "pcr_live-0/12" and r.replans[0].push > 300
    assert r.trace.alarm is None
    assert len(r.plans) == 1 + sum(e.adopted for e in r.replans)
    assert all(e.status == "not_worth_it" for e in r.replans if not e.adopted and e.makespan_after is not None)

def test_huge_threshold_never_replans(timed_batch):
    b = timed_batch(3); r = execute_live(b, p0(b), NoiseParams(seed=1, delay_prob=0.2), cp(b), 10**9)
    assert r.replans == [] and r.trace.alarm is None

def test_failed_replan_keeps_plan(timed_batch):
    b = timed_batch(3)
    bad = lambda st: Plan("cpsat:makespan", {}, {}, {}, "no_solution", solver_status="UNKNOWN")
    with patch("workcell.live.executor.actual_block", overrun("pcr_live-0/12", 900)):
        r = execute_live(b, p0(b), QUIET, bad, 300)
    assert r.replans and all(e.status == "no_solution" and e.makespan_after is None for e in r.replans)
    assert r.trace.alarm is None and len(r.plans) == 1
    assert r.trace.completed == {blk.id for blk in b.all_blocks()}

def test_same_seed_same_run(timed_batch):
    b = timed_batch(3)
    r1 = execute_live(b, p0(b), NoiseParams(seed=4, delay_prob=0.1), cp(b), 120)
    r2 = execute_live(b, p0(b), NoiseParams(seed=4, delay_prob=0.1), cp(b), 120)
    assert r1.trace.spans == r2.trace.spans
    assert [(e.time, e.trigger) for e in r1.replans] == [(e.time, e.trigger) for e in r2.replans]

def test_delay_on_last_block_no_replan(timed_batch):
    b = timed_batch(1); p = p0(b); last = max(p.start, key=lambda x: (p.end(x), x))
    with patch("workcell.live.executor.actual_block", overrun(last, 5000)):
        r = execute_live(b, p, QUIET, cp(b), 300)
    assert r.replans == [] and r.trace.alarm is None
```

`executor.py` must import `actual_block` at module level (`from workcell.live.noise import actual_block`), so the patch target works.

- [ ] **Step 2:** Run the tests and confirm they fail.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run the tests and confirm they pass. Then run the full suite.
- [ ] **Step 5:** Commit with the message `feat: live executor with delay detection and re-planning`.

---

### Task 6: `run_live`, as-run validation, live stats

**Files:**
- Create: `workcell/live/session.py`, `tests/test_live_session.py`
- Modify: `workcell/validate.py` (add a `check_durations` flag), `workcell/run.py` (`Session.live`)

**Interfaces:**
- Produces:
  - `validate_plan(plan, batch, check_durations: bool = True)`. When `False`, the instruction comparison against compiled blocks ignores `duration`, and only block-level durations come from `plan.blocks`. Existing behaviour stays the default.
  - `as_run_plan(batch, result: LiveResult) -> Plan`:
    - `blocks` are the bound compiled blocks under `result.bindings`, with each instruction's `duration` replaced by `result.actual[bid]`, for executed blocks only.
    - `start` comes from the trace span starts.
    - `status` is `"complete"` if every compiled block completed and there is no alarm. Otherwise it is `"alarm"` if there was an alarm, and `"incomplete"` if not.
    - `mode="live"`.
  - `@dataclass LiveStats(seed: int, threshold: int, replans: int, adopted_replans: int, not_worth_it: int, failed_replans: int, reassignments: int, total_delay: int, planned_makespan: int, actual_makespan: int)`. `total_delay` is the sum over executed blocks of actual minus planned block duration.
  - `@dataclass LiveSession(session: Session, result: LiveResult, live: LiveStats, errors: list[str])`. `session` is a v1 `Session` with exactly one `RunResult("live", as_run, trace, compute_stats(as_run, trace, batch), errors)` and `session.live` set to the report payload (Task 7).
  - `Session` gains `live: dict | None = None`, the last field with a default, so v1 construction still works.
  - `run_live(workcell, specs, *, seed=0, noise=0.05, delay_prob=0.02, delay_range=(2.0, 5.0), replan_threshold=300, replan_time_limit=2.0, time_limit=10.0, workcell_source="", batch_source="") -> LiveSession`. It runs:
    1. compile
    2. the initial `plan_cpsat(..., deterministic=True, time_limit=time_limit, hint="serial")`. If that isn't optimal or feasible, raise `RuntimeError("initial plan failed: <status>")`.
    3. `execute_live` with a `replan` callback that calls `replan_cpsat(batch, st, time_limit=replan_time_limit)`
    4. `as_run_plan`
    5. `errors = validate_plan(as_run, batch, check_durations=False)`
    6. stats
  - The `session.live` payload is `{"seed", "threshold", "replans": [asdict(e) for e in ...], "stats": asdict(LiveStats)}`.

- [ ] **Step 1: Write failing tests** in `tests/test_live_session.py`:

```python
@pytest.mark.parametrize("seed", [0, 1, 2, 3])
def test_live_runs_validate(workcell_small, seed):
    specs, _ = load_batch(EXAMPLES / "pcr_live.py")
    ls = run_live(workcell_small, specs[:4], seed=seed, delay_prob=0.1, replan_threshold=120,
                  time_limit=10, replan_time_limit=3)
    assert ls.errors == [] and ls.session.runs[0].stats.completed == 4
    assert ls.live.actual_makespan == ls.session.runs[0].stats.makespan

def test_some_seed_replans_and_still_validates(workcell_small):
    specs, _ = load_batch(EXAMPLES / "pcr_live.py")
    runs = [run_live(workcell_small, specs[:4], seed=s, delay_prob=0.1, replan_threshold=60,
                     time_limit=10, replan_time_limit=3) for s in range(6)]
    assert any(r.live.replans > 0 for r in runs) and all(r.errors == [] for r in runs)
    assert all(r.live.replans == r.live.adopted_replans + r.live.not_worth_it + r.live.failed_replans for r in runs)

def test_validator_duration_flag(timed_batch):
    b = timed_batch(1); p = plan_cpsat(b, time_limit=5, deterministic=True)
    r = execute_live(b, p, NoiseParams(seed=2), lambda st: p, 10**9)
    asrun = as_run_plan(b, r)
    assert validate_plan(asrun, b, check_durations=False) == []
    assert asrun.status == "complete" and set(asrun.start) == {blk.id for blk in b.all_blocks()}

def test_same_seed_same_session(workcell_small):
    specs, _ = load_batch(EXAMPLES / "pcr_live.py")
    a = run_live(workcell_small, specs[:3], seed=5, time_limit=5, replan_time_limit=2)
    c = run_live(workcell_small, specs[:3], seed=5, time_limit=5, replan_time_limit=2)
    assert a.result.trace.spans == c.result.trace.spans and a.live.replans == c.live.replans
```

- [ ] **Step 2:** Run the tests and confirm they fail.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run the tests, then the full suite. All should pass.
- [ ] **Step 5:** Commit with the message `feat: run_live, as-run validation, live stats`.

---

### Task 7: Live report

**Files:**
- Modify: `workcell/report/build.py`, `workcell/report/report.js`, `workcell/report/report.css`, `workcell/report/template.html` (if needed)
- Test: `tests/test_report.py`

**Interfaces:**
- Consumes: `Session.live` (Task 6).
- Produces: `report_data(session)` adds a `"live"` key holding `session.live`, or `null`. The JS changes apply only when `D.live` is set; v1 reports render exactly as before.
  - **Time format:** h:mm:ss everywhere a time is shown (axes, tooltips, the cursor readout, stats).
  - **Single run:** the mode tabs are hidden.
  - **Speed:** the choices are `[10, 30, 60, 120, 300, 600]` s/s, defaulting to 60.
  - **Re-plan markers:** a marker on both Gantts and on the scrubber at each `replans[i].time`. There are three styles: adopted (solid), not worth it (outline), and failed (muted). An adopted re-plan's tooltip adds `, reassigned N` when N > 0. Tooltip: `re-planned at H:MM:SS — <trigger label> ran <M:SS> long; projected push <M:SS> > <threshold M:SS>; finish <old> → <new>`. A not-worth-it re-plan reads `kept plan at H:MM:SS — re-plan would save only <M:SS>`, and a failed one `re-plan failed (<status>) at …`.
  - **Stats:** a "Live" stats block showing seed, threshold, re-plans (adopted / not worth it / failed), reassignments, total delay, planned makespan and actual makespan.
- [ ] **Step 1: Write failing tests** in `tests/test_report.py`:

```python
def test_live_report_data(workcell_small):
    specs, _ = load_batch(EXAMPLES / "pcr_live.py")
    ls = run_live(workcell_small, specs[:3], seed=1, delay_prob=0.2, replan_threshold=60, time_limit=5, replan_time_limit=2)
    d = data_of(build_report(ls.session))
    assert d["live"]["seed"] == 1 and isinstance(d["live"]["replans"], list)
    assert set(d["live"]["stats"]) >= {"replans", "adopted_replans", "not_worth_it", "reassignments", "total_delay", "planned_makespan", "actual_makespan"}
    assert len(d["runs"]) == 1 and d["runs"][0]["mode"] == "live"

def test_v1_report_has_no_live(session):
    assert data_of(build_report(session))["live"] is None
```

- [ ] **Step 2:** Run the tests and confirm they fail.
- [ ] **Step 3:** Implement the Python and JS changes.
- [ ] **Step 4:** Run `tests/test_report.py` and the full suite. All should pass.
- [ ] **Step 5: Visual check.** Write a live report for 10 workflows with `seed` and threshold values that produce at least one re-plan. Serve `out/` with `.venv/bin/python -m http.server` (file:// is blocked), and check it in Playwright:
  - no console errors
  - h:mm:ss everywhere
  - the markers appear, with correct tooltips
  - the speed defaults to 60 s/s
  - no tabs

  Also open a v1 report and confirm it is unchanged: "min" units, tabs, and 2 min/s. Save screenshots to `out/`. Stop the server afterwards with `pkill -f "http[.]server"`.
- [ ] **Step 6:** Commit with the message `feat: live report (h:mm:ss, re-plan markers, live stats)`.

---

### Task 8: `live` CLI, example report, docs

**Files:**
- Modify: `workcell/cli.py`, `README.md`, `CLAUDE.md`, `docs/FUTURE.md`
- Create: `docs/reports/pcr10_live.html`
- Test: `tests/test_cli.py`

**Interfaces:**
- Produces:
  - **CLI:** `python -m workcell live WORKCELL BATCH [--seed 0] [--replan-threshold 300] [--replan-time-limit 2] [--time-limit 10] [--noise 0.05] [--delay-prob 0.02] [--out out/live.html]`.
    - It prints `seed`, `planned makespan`, `actual makespan` (both h:mm:ss), `re-plans: N (A adopted, K not worth it, F failed)`, `completed: X/Y`, and then `report: <path>`.
    - The exit code is 0, or 2 for config, compile and input errors, as with `run`.
- [ ] **Step 1: Write failing tests:**

```python
def test_cli_live(tmp_path):
    out = tmp_path / "l.html"
    r = subprocess.run([sys.executable, "-m", "workcell", "live", "examples/workcell.yaml", "examples/pcr_live.py",
                        "--seed", "1", "--time-limit", "10", "--replan-time-limit", "3", "--out", str(out)],
                       capture_output=True, text=True, cwd=ROOT)
    assert r.returncode == 0, r.stderr
    assert "re-plans:" in r.stdout and "completed: 10/10" in r.stdout and out.exists()

def test_cli_live_bad_file():
    r = subprocess.run([sys.executable, "-m", "workcell", "live", "examples/workcell.yaml", "nope.py"],
                       capture_output=True, text=True, cwd=ROOT)
    assert r.returncode == 2
```

- [ ] **Step 2:** Run the tests and confirm they fail.
- [ ] **Step 3:** Implement the subcommand.
- [ ] **Step 4:** Run the tests and the full suite. All should pass.
- [ ] **Step 5: Example report.**
  1. Find the lowest seed in 0–20 whose default-parameter run of `examples/pcr_live.py` produces at least one adopted re-plan.
  2. Generate `docs/reports/pcr10_live.html` with that seed.
  3. In `README.md`, add a "v2: live re-planning" section with:
     - what it does (2–3 sentences)
     - the exact command, including the seed
     - the printed summary
     - a link to `https://jared-maguire.github.io/workcell_poc/docs/reports/pcr10_live.html`
  4. In `CLAUDE.md`, add:
     - the `live` command
     - the `workcell/live/` modules and their roles
     - the timing lookup rule
     - deterministic CP-SAT in live mode
     - "lock waits are normal in live"
     - the as-run validation (`check_durations=False`)
  5. In `docs/FUTURE.md`, add: "Detect an overrun while the block is still running (v2 only notices at block finish)."
- [ ] **Step 6:** Commit with the message `feat: live CLI, example live report, docs`.
