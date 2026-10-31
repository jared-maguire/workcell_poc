# Max Idle Hold Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** In live mode, no instrument sits idle holding a plate for more than `max_idle_hold` seconds (default 300): CP-SAT plans optional park detours to a hotel or storage, re-plans fix projected violations, and the report counts what actually happened.

**Architecture:** One shared definition of idle-hold gaps (`workcell/idle.py`) used by the validator, CP-SAT, and the live executor. Park blocks reuse the greedy_hotel shape (`<move>.park`, inserted, rewritten move starts from the parking spot), built by a new `workcell/parking.py`. CP-SAT gets per-leave park variants (direct, or park at each hotel/storage nest); initial plans enforce the limit hard, re-plans soft (penalized). The executor gains an idle-hold trigger and handles inserted blocks.

**Tech Stack:** Python 3.14, OR-Tools CP-SAT 9.15, pytest (+xdist).

**Spec:** `docs/superpowers/specs/2026-10-31-max-idle-hold-design.md`

## Global Constraints

- `max_idle_hold` in seconds, default **300**; gap ≤ limit is allowed. `None`/`0` disables everything (no park variants, no checks): behavior identical to today.
- Live mode only. v1 `run`, greedy planners, and the v1 example report are untouched; `plan_cpsat`/`replan_cpsat` default `max_idle_hold=None`.
- Storage `retrieve`/`store` = **60 s** in `examples/pcr_live.py` `TIMES`; hotels have no ops (arm move only).
- Park block shape (existing): id `f"{removal_id}.park"`, `kind="park"`, `inserted=True`, one `PlateMove(plate, nest, spot)`, deps = removal's compiled deps; the rewritten removal keeps id/kind/dst, `src = spot`, deps += park id, instructions = `transport_instructions(wc, plate, spot, dst, times=<workflow times>)`.
- Deterministic: sorted iteration everywhere the model is built (same seed → same run across processes).
- Fast tier stays ~1 min; CP-SAT runs over 3 workflows or with budgets > 1 det-s go in `@pytest.mark.slow`.

## Review Focus

- A plate already over the limit at re-plan time: the re-plan must still return a finished plan (soft constraint), never "infeasible". Test: Task 3 `test_replan_with_violation_already_past`.
- An adopted re-plan that drops a planned-but-unstarted park block: the executor must not run the stale park, and must run newly added parks. Test: Task 4 `test_adopted_replan_swaps_park_blocks`.
- A workcell with no hotel: parks go to storage (60 s ops) and the plan is still valid. Test: Task 2 `test_parks_to_storage_without_hotel`.
- `max_idle_hold=None`: plans identical to today (no `.park` blocks, same model). Test: Task 2 `test_no_limit_unchanged`.
- Variable nests (`?tc0`): a park out of a thermocycler binds to the chosen thermocycler. Test: Task 2 `test_one_thermocycler_forces_parks` (asserts park src is `tc1`).

---

### Task 1: Storage timing, idle-hold gaps, validator check

**Files:**
- Modify: `examples/pcr_live.py` (TIMES retrieve/store 60)
- Create: `workcell/idle.py`
- Modify: `workcell/validate.py`
- Test: `tests/test_timed.py` (update numbers), `tests/test_idle.py` (new)

**Interfaces:**
- Produces:
  - `IdleGap(plate: str, nest: str, prev: str, next: str, length: int)` dataclass.
  - `idle_gaps(blocks: dict[str, Block], start: dict[str, int], end: dict[str, int], batch: CompiledBatch) -> list[IdleGap]`: for each plate, the blocks in `start` whose `plates` contain it, ordered by (start, id); track its location (initial, then each move's dst); while the location is an instrument nest (owner type not storage/hotel), each consecutive pair (prev, next) yields a gap `start[next] - end[prev]`. Sorted by (plate, start of prev).
  - `HoldChain(workflow: str, plate: str, nest: str, blocks: list[str])` and `hold_chains(batch: CompiledBatch) -> list[HoldChain]`: compile-level version — per workflow, per plate, the workflow's blocks touching it in compiled order, split into stays by moves; one chain per stay in a nest that is an instrument nest for every candidate of its var (`nest` may be `?tc0`). `blocks[0]` places the plate, `blocks[-1]` removes it (a single-move block); chains without a removal are dropped. Raise `ValueError` if a removal block has more than one move.
  - `violations(gaps, limit) -> list[IdleGap]` (length > limit).
  - `validate_plan(plan, batch, check_durations=True, max_idle_hold: int | None = None)`: when set, error `f"plate {g.plate} idles {g.length}s in {g.nest} between {g.prev} and {g.next} (limit {max_idle_hold}s)"` per violation, using `plan.start` / `plan.end`.

- [ ] **Step 1: Update `tests/test_timed.py` for storage 60 s, and write `tests/test_idle.py`**

```python
# test_timed.py: line 14-15 become
assert d["pcr_live-0/00"] == 70                                      # fetch: retrieve 60 + move 10
assert [d[f"pcr_live-0/{i:02d}"] for i in (1, 2, 6, 11, 12, 15, 13)] == [4, 10, 60, 20, 4020, 80, 70]
```

```python
# tests/test_idle.py
def test_hold_chains_pcr(timed_batch):
    chains = {(c.plate, c.nest): c.blocks for c in hold_chains(timed_batch(1))}
    p = "pcr_live-0/"
    assert chains[("E0001", "stamp1.dst")] == [p+"05", p+"06", p+"10", p+"11"]
    assert chains[("E0001", "?tc0")] == [p+"11", p+"12", p+"15"]
    assert chains[("S0001", "scan1")] == [p+"00", p+"01", p+"02"]
    assert not any(n in ("store1", "hotel1") for _, n in chains)

def test_serial_plan_has_no_violations(timed_batch):
    b = timed_batch(2); starts, binds = serial_hint(b)
    plan = Plan("x", bind_batch(b, binds), starts, binds, "feasible")
    assert violations(idle_gaps(plan.blocks, plan.start, {k: plan.end(k) for k in plan.start}, b), 300) == []
    assert validate_plan(plan, b, max_idle_hold=300) == []

def test_validator_flags_long_wait(timed_batch):
    # serial plan, then delay E0001's move to the thermocycler (0/11) and everything after it by 1000 s
    ...  # shift starts of 0/11, 0/12, 0/15 (and later blocks) by +1000
    errs = validate_plan(plan, b, max_idle_hold=300)
    assert any("E0001" in e and "stamp1.dst" in e and "0/10" in e and "0/11" in e for e in errs)
    assert validate_plan(plan, b) == []          # no limit → no idle errors
```

- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_idle.py tests/test_timed.py -q` — Expected: FAIL (import error / old numbers).
- [ ] **Step 3: Implement** TIMES change, `workcell/idle.py`, validator param.
- [ ] **Step 4: Run** same command — Expected: PASS. Then `.venv/bin/pytest -q` — any other test pinned to 20 s storage gets its number updated (not its meaning).
- [ ] **Step 5: Commit** `feat: idle-hold gaps, validator limit; storage ops 60 s`

### Task 2: Park blocks and hard idle-hold limit in CP-SAT

**Files:**
- Create: `workcell/parking.py`
- Modify: `workcell/planners/cpsat.py`
- Test: `tests/test_cpsat_idle.py` (new)

**Interfaces:**
- Consumes: `hold_chains`, `idle_gaps`, `violations` (Task 1).
- Produces:
  - `parking_spots(wc) -> list[str]`: nest ids of hotel then storage instruments, sorted within each type.
  - `park_pair(batch, removal: Block, spot: str) -> tuple[Block, Block]`: `removal` is a bound single-move block; returns (park, rewritten removal) in the shape in Global Constraints, using the workflow's `times` (from `batch.specs[i].times` for the workflow's index).
  - `park_durations(batch, removal: Block, spot: str) -> tuple[int, int]`: (park, rewritten removal) durations for an unbound removal, via `transport_instructions(..., var_types, times)`; var types as greedy computes them (`wc.instruments[cands[0]].type`).
  - `plan_parks(plan: Plan) -> dict[str, str]`: removal id → spot for every `.park` block in the plan.
  - `plan_cpsat(..., max_idle_hold: int | None = None)`; `_Model(batch, state=None, max_idle_hold=None, soft=False)`.

**Model (the part the signatures don't determine):** for each `HoldChain` with removal `D` and last use `U = blocks[-2]`:
- literals `park[D, spot]` for each spot, `direct[D] = 1 - Σ park` (at most one park).
- park start var `sK`, size `Σ park_size·lit`; `leave[D] = sK` if parked else `start[D]` (OnlyEnforceIf). `D`'s size becomes `direct_size·direct + Σ unpark_size·lit` (`self.size[D]` an expression; `self.end[D]` follows).
- `sK ≥ end[d]` for each `d` in `D.deps`, and `start[D] ≥ sK + park_size` when parked.
- Locks: `D` keeps its intervals on the arm, dst owner(s) and plate (variable size); its interval on the src owner is present only when direct (∧ binding literal for var owners); a park interval of size `park_size[spot]` on arm, src owner, plate, and `spot` present when `park[D, spot]`.
- Stay interval for the chain's nest ends at `leave[D]` instead of `start[D]`. Stays in hotel/storage stay skipped.
- Gaps: for consecutive chain blocks `(a, b)` with `b ≠ D`: `start[b] - end[a] ≤ L`; for the leave gap: `leave[D] - end[U] ≤ L`. Also add `start[b] ≥ end[a]` for consecutive chain blocks.
- Objective with a limit: `100 · makespan + Σ (all chain gaps)` ("right away", spec). Without a limit (`None`/`0`): model exactly as today (no chains, no literals).
- Solution → Plan: bind, then for each `D` with a chosen spot replace `blocks[D]` with the rewritten removal and add the park block with start `sK`.
- Hints: serial hint sets every `park` literal to 0.

- [ ] **Step 1: Write `tests/test_cpsat_idle.py`**

```python
ONE_TC = SMALL with tc2 removed      # parse_workcell(dict) variant built in the test module
NO_HOTEL = SMALL with hotel1 removed and tc2 removed

def solve(b, **kw):
    return plan_cpsat(b, time_limit=1, deterministic=True, hint="serial", max_idle_hold=300, **kw)

def test_one_thermocycler_forces_parks():            # 2 workflows, 1 thermocycler: 2nd E plate must wait ~67 min
    b = compile_batch(parse_workcell(ONE_TC), load_batch(EXAMPLES / "pcr_live.py")[0][:2])
    p = solve(b)
    assert p.finished and validate_plan(p, b, max_idle_hold=300) == []
    parks = plan_parks(p)
    assert parks and set(parks.values()) <= {"hotel1"}
    # every park leaves within the limit of the plate's last use
    for d, spot in parks.items():
        k = p.blocks[d + ".park"]
        assert k.moves[0].dst == spot and p.start[d + ".park"] >= p.end(p.blocks[d].deps[0])
    assert execute(p, b).alarm is None        # sim executor runs inserted park blocks cleanly

def test_parks_to_storage_without_hotel():
    b = compile_batch(parse_workcell(NO_HOTEL), ...[:2])
    p = solve(b)
    assert p.finished and validate_plan(p, b, max_idle_hold=300) == []
    assert set(plan_parks(p).values()) == {"store1"}

def test_no_limit_unchanged(timed_batch):
    b = timed_batch(2)
    a = plan_cpsat(b, time_limit=0.5, deterministic=True, hint="serial")
    c = plan_cpsat(b, time_limit=0.5, deterministic=True, hint="serial", max_idle_hold=None)
    assert a.start == c.start and not any(k.endswith(".park") for k in c.blocks)

def test_park_pair_shape(timed_batch):
    b = timed_batch(1); d = b.block("pcr_live-0/11").bind({"?tc0": "tc1"})
    park, moved = park_pair(b, d, "hotel1")
    assert (park.id, park.kind, park.inserted) == ("pcr_live-0/11.park", "park", True)
    assert [(m.src, m.dst) for m in park.moves] == [("stamp1.dst", "hotel1")]
    assert [(m.src, m.dst) for m in moved.moves] == [("hotel1", "tc1")] and park.id in moved.deps
    assert park.duration == 10 and moved.duration == 20     # check_status 0 + move 10; doors 5+5 + move 10
```

- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_cpsat_idle.py -q` — Expected: FAIL (imports).
- [ ] **Step 3: Implement** `workcell/parking.py` and the model changes.
- [ ] **Step 4: Run** the new tests, then `.venv/bin/pytest -q` — Expected: PASS (existing CP-SAT tests unchanged because the default is `None`).
- [ ] **Step 5: Commit** `feat: CP-SAT park detours with a hard idle-hold limit`

### Task 3: Soft limit and park decisions in re-plans

**Files:**
- Modify: `workcell/planners/cpsat.py`
- Test: `tests/test_replan.py` (add)

**Interfaces:**
- Produces:
  - `ReplanState.parks: dict[str, str | None] = {}` — removal id → spot (or `None` = direct) decisions that must stay (park block started, or removal started).
  - `ReplanState.hint_parks: dict[str, str | None] = {}` — the current plan's decisions, for hints and churn.
  - `replan_cpsat(batch, state, time_limit=2.0, reassign_weight=600, max_idle_hold=None)`.
- Model with `soft=True`: every gap bound becomes `excess ≥ gap - L`, `excess ≥ 0`. Phase 1 minimizes `(horizon + 1) · Σ excess + makespan`; phase 2 adds `Σ excess ≤ best_excess`, `makespan ≤ cap`, and churn gains `reassign_weight` per park decision that differs from `hint_parks`. Fixed `.park` blocks take their size from `state.fixed` like any fixed block.

- [ ] **Step 1: Add tests**

```python
def test_replan_with_violation_already_past(...):
    # ONE_TC, 2 workflows, serial initial plan; state at a time when E0002 has sat on stamp1.dst
    # for 900 s past its last use (blocks up to wf-1/10 fixed, wf-1/11 not started)
    new = replan_cpsat(b, st, time_limit=1, max_idle_hold=300)
    assert new.finished
    assert new.start["pcr_live-1/11.park" if "pcr_live-1/11" in plan_parks(new) else "pcr_live-1/11"] == st.now

def test_replan_keeps_fixed_parks(...):
    # an initial plan with parks (ONE_TC); state where one park block has started
    new = replan_cpsat(b, st, time_limit=1, max_idle_hold=300)
    assert plan_parks(new)[d] == "hotel1" and new.start[d + ".park"] == st.fixed[d + ".park"][0]
```

- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_replan.py -q` — Expected: new tests FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `.venv/bin/pytest -q` — Expected: PASS.
- [ ] **Step 5: Commit** `feat: soft idle-hold limit and park decisions in re-plans`

### Task 4: Live executor, session, CLI

**Files:**
- Modify: `workcell/live/executor.py`, `workcell/live/session.py`, `workcell/cli.py`
- Test: `tests/test_live_executor.py`, `tests/test_live_session.py`, `tests/test_cli.py` (add)

**Interfaces:**
- Consumes: Tasks 1–3.
- Produces:
  - `execute_live(batch, plan, noise, replan, threshold, max_idle_hold: int | None = None)`.
  - `ReplanEvent` gains `reason: str` (`"delay"` or `"idle_hold"`), `idle_before: int`, `idle_after: int | None` (total projected excess seconds).
  - `LiveResult.blocks: dict[str, Block]` — the block actually executed per id (as-run uses these, not `bind_batch`).
  - `LiveStats` gains `parks: int`, `idle_replans: int`, `idle_violations: int`, `worst_idle: int`.
  - `run_live(..., max_idle_hold: int | None = 300)`; CLI `--max-idle-hold 300` (`0` disables); printed line `idle hold: limit 300s, parks N, violations N (worst Xs)`.
- Executor rules:
  - Planned duration everywhere = `blocks[bid].duration` (the bound plan block), never `compiled[bid]` (park blocks have no compiled entry; rewritten removals differ). `_vars_of` only for compiled ids.
  - In `check_delay`: projected gaps over started ∪ projected times; a violation is *new* if the same (plate, prev, next) gap is within the limit in the current baseline (`start_at` + planned durations). Trigger if `push > threshold` or any new violation. Reason `"idle_hold"` when there is a new violation, else `"delay"`.
  - `ReplanState.parks` from started park blocks (→ their spot) and started removals (→ their spot or `None`); `hint_parks = plan_parks(plan)` with `None` for chains not parked.
  - Adopt if `before - after ≥ threshold`, or (reason is idle_hold and `idle_after < idle_before`). On adoption, rebuild `pending` from the new plan's unstarted blocks (dropping stale park ids, adding new ones with fresh `seq` numbers), and replace `blocks`/`start_at` for them.
  - `as_run_plan` uses `result.blocks`; as-run validation stays `check_durations=False` without the idle check; stats count violations of the as-run gaps.

- [ ] **Step 1: Add tests**

```python
def test_idle_hold_park_runs(...):        # ONE_TC, 2 workflows, QUIET noise
    r = execute_live(b, p0_limit(b), QUIET, cp_limit(b), 300, max_idle_hold=300)
    assert r.trace.alarm is None and any(k.endswith(".park") for k in r.trace.completed)
    assert validate_plan(as_run_plan(b, r), b, check_durations=False) == []

def test_overrun_triggers_idle_replan(...):
    # overrun the thermocycle of wf 0 (ONE_TC) by 900 s: wf 1's parked plate is fine, but a plate
    # planned to move directly now waits too long → an "idle_hold" re-plan is recorded
    assert any(e.reason == "idle_hold" for e in r.replans)

def test_adopted_replan_swaps_park_blocks(...):
    # replanner stub returns a plan whose parks differ from the initial plan's;
    # executed ids == the adopted plan's ids for unstarted blocks (no stale .park ran)

def test_live_session_idle_stats(...):    # run_live on 2 workflows, ONE_TC: stats.parks > 0, as-run errors == []
def test_cli_live_prints_idle_line(...):  # "idle hold: limit 300s" in output
```

- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_live_executor.py tests/test_live_session.py tests/test_cli.py -q` — Expected: new tests FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `.venv/bin/pytest -q` and `.venv/bin/pytest -q -m slow` — Expected: PASS.
- [ ] **Step 5: Commit** `feat: live idle-hold trigger, park execution, stats, CLI flag`

### Task 5: Report, example, docs

**Files:**
- Modify: `workcell/report/report.js` (live stats rows: parks, idle-hold re-plans, violations/worst; re-plan tooltip says "idle hold" for that reason), `README.md`, `CLAUDE.md`, `docs/FUTURE.md`, `docs/reports/pcr10_live.html`
- Test: `tests/test_report.py` (live data carries the new stats keys)

- [ ] **Step 1: Test** `test_live_report_idle_stats`: `report_data(session)["live"]["stats"]` has `parks`, `idle_violations`, `worst_idle`.
- [ ] **Step 2: Run** — FAIL. **Step 3:** implement. **Step 4:** run full fast tier — PASS.
- [ ] **Step 5:** regenerate `docs/reports/pcr10_live.html` (seed 0), check it in the browser (park blocks visible, floor plan shows plates in hotel1, no console errors); update README v2 numbers and CLAUDE.md (v2 section: idle hold, park blocks from CP-SAT, storage 60 s); remove "CP-SAT chooses hotel parking" from FUTURE.md carried-over list (now done for live mode).
- [ ] **Step 6: Commit** `feat: idle-hold stats in live report; docs and example`
