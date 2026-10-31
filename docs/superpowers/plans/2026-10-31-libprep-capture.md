# Library Prep + Hybrid Capture Demo Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Three 96-sample gDNA plates go through Illumina library prep, IDT xGen hybrid capture, and consolidation into one well each, scheduled live on a new workcell.

**Architecture:** Add seven instrument types (config + simulator), a generic timed `w.step(...)` in the workflow language compiled like `stamp`/`thermocycle`, an example workcell and workflow following the protocol table in the spec, and report labels. Planner, idle hold, executor and validator work on blocks and need no changes.

**Tech Stack:** Python 3.14, OR-Tools CP-SAT, pytest.

**Spec:** `docs/superpowers/specs/2026-10-31-libprep-capture-design.md`

## Global Constraints

- New types (exact names): `liquid_handler`, `heated_shaker`, `plate_sealer`, `plate_peeler`, `centrifuge`, `plate_reader`, `vacuum_concentrator`.
- `liquid_handler` nests `<id>.src` and `<id>.dst` (capacity 1 each); the other new types one nest `<id>`.
- Step times in seconds from the spec table; thermocycler times = program sums (2175 s BLT PCR, 900 s TAG, 14430 s hybridization, 1005 s post-capture PCR).
- Transport times as `examples/pcr_live.py`: arm move 10, doors 5, scan 4, retrieve/store 60, check_status 0.
- v1 behavior and existing examples unchanged.
- Fast tier stays about where it is (~90 s); anything planning more than one batch goes in `@pytest.mark.slow`.

## Review Focus

- A `step` naming a two-plate op on a one-position type: clear `ValueError` at definition time, not a compile crash. Test: Task 2 `test_step_two_plates_needs_two_positions`.
- A plate staying on the same liquid handler across consecutive steps: no extra moves. Test: Task 2 `test_consecutive_lh_steps_no_move`.
- World sees a step whose plate is not in the instrument: alarm with a clear message. Test: Task 1 `test_step_plate_missing`.
- Unknown instrument type in `step`: compile error naming the type (exit code 2 from the CLI). Test: Task 2 `test_step_unknown_type`.
- Three batches share two liquid handlers: the as-run schedule never puts two plates in one LH nest (validator). Covered by the demo run's as-run validation.

---

### Task 1: Instrument types in config and simulator

**Files:** Modify `workcell/config.py`, `workcell/sim/world.py`. Test: `tests/test_config.py`, `tests/test_world.py`.

**Interfaces — Produces:**
- `INSTRUMENT_TYPES` includes the seven new types; `Workcell.nests()` as in Global Constraints.
- `World` handles any op on a new type as a generic step: `instr.args["plates"]` (list of barcodes) must each be in one of that instrument's nests, else `InstrumentError(..., f"plate {p} not in {instrument}")`.

- [ ] **Step 1: Tests**

```python
def test_new_type_nests():
    wc = parse_workcell({**SMALL, "instruments": SMALL["instruments"] + [
        {"id": "lh1", "type": "liquid_handler"}, {"id": "hs1", "type": "heated_shaker"}]})
    n = wc.nests()
    assert (n["lh1.src"].capacity, n["lh1.dst"].capacity, n["hs1"].capacity) == (1, 1, 1)

def test_step_plate_missing(): ...   # step on hs1 with plates=["S0001"] while S0001 in store1
    # -> InstrumentError "plate S0001 not in hs1"
def test_step_ok(): ...              # move S0001 to hs1, then step "shake" on hs1 -> no error
```

- [ ] **Step 2:** run `.venv/bin/pytest -q -n0 tests/test_config.py tests/test_world.py` — FAIL.
- [ ] **Step 3:** implement. **Step 4:** run — PASS; full fast tier PASS.
- [ ] **Step 5:** commit `feat: lab instrument types (liquid handler, shaker, sealer, peeler, centrifuge, reader, concentrator)`

### Task 2: `w.step` in the language and compiler

**Files:** Modify `workcell/dsl.py`, `workcell/compiler.py`. Test: `tests/test_step.py` (new).

**Interfaces — Produces:**
- `Workflow.step(itype: str, op: str, *plates: PlateHandle, seconds: int, after=None) -> str`; `Op.kind == "step"`, params `{"itype", "op", "seconds"}`. One or two plates; two plates only for `liquid_handler`/`head_stamper` (else `ValueError`); `seconds` required (`TypeError` if missing).
- Compiler: an op block labeled `f"{op} {barcodes…}"` with `[check_status, Instruction(inst, op, {"plates": [...]}, seconds)]`. Instrument choice: two-position types use one variable per workflow per type (like `?stamp`: `?lh`), so a batch stays on one liquid handler; single-position types use numbered variables (`?hs0`, `?hs1`, … prefix = type initials: `hs`, `seal`, `peel`, `spin`, `read`, `dry`). One plate → `.dst` for two-position types, else the instrument nest. Unknown type → `CompileError(f"no {itype} instrument available …")` (existing message).

- [ ] **Step 1: Tests** (in `tests/test_step.py`, on SMALL + `lh1`, `hs1`, `seal1`):

```python
def test_step_compiles_to_op_block():   # step("heated_shaker","shake",p,seconds=60) -> block kind "op",
                                        # instructions [check_status, shake{"plates":[bc]}], duration 60
def test_lh_two_plate_step_nests():     # step("liquid_handler","pool",a,b) moves a→lh1.src, b→lh1.dst
def test_consecutive_lh_steps_no_move():# step(lh, "wash", b) after the pool: no move block between
def test_step_two_plates_needs_two_positions():  # step("heated_shaker","x",a,b,seconds=1) -> ValueError
def test_step_unknown_type():           # step("sequencer","run",a,seconds=1) -> CompileError at compile
def test_step_requires_seconds():       # step("heated_shaker","shake",a) -> TypeError
```

- [ ] **Step 2:** run `.venv/bin/pytest -q -n0 tests/test_step.py` — FAIL. **Step 3:** implement. **Step 4:** PASS; full fast tier PASS.
- [ ] **Step 5:** commit `feat: generic timed instrument steps in workflows`

### Task 3: The library prep + capture example

**Files:** Create `examples/libprep_workcell.yaml`, `examples/libprep_capture.py`. Test: `tests/test_libprep.py` (new).

**Interfaces — Produces:** `libprep_capture(batch: int)` workflow (`@workflow(times=TIMES)`), `batch()` returning three workflows (batches 1–3); helper `cycles(n, steps)` for thermocycler programs. Workcell per spec (instrument counts, hotel 30, plate pools: `G` gDNA ×3, reagent pools `RTAG`, `RSTP`, `RPCR`, `RIDX` ×3 each, empties ×12).

- [ ] **Step 1: Tests**

```python
def test_libprep_compiles(): # 1 batch: thermocycle durations [900, 2175, 14430, 1005];
                             # sum of op-block durations == spec table total (computed in test from the table)
def test_libprep_serial_plan_runs():  # serial_hint plan for 1 batch: validate_plan == [] and execute() clean
def test_libprep_three_batches_compile():  # 3 batches compile; distinct barcodes per batch
```

- [ ] **Step 2:** FAIL (no example). **Step 3:** write the workcell and workflow following the spec table row by row (one `w.step`/`stamp`/`thermocycle` per row entry, `store` per the plate-return list). **Step 4:** PASS.
- [ ] **Step 5:** commit `feat: library prep + hybrid capture example workflow and workcell`

### Task 4: Report labels, demo run, docs

**Files:** Modify `workcell/report/report.js` (`TYPE_LABEL` for the new types: "liquid handler", "shaker", "sealer", "peeler", "centrifuge", "plate reader", "concentrator"), `README.md`, `CLAUDE.md`. Create `docs/reports/libprep3_live.html`. Test: `tests/test_report.py` (labels present in built HTML), one `@pytest.mark.slow` one-batch `run_live` test in `tests/test_libprep.py` (completes, as-run errors == []).

- [ ] **Step 1:** tests — FAIL. **Step 2:** implement labels. **Step 3:** PASS.
- [ ] **Step 4:** run the demo: `.venv/bin/python -m workcell live examples/libprep_workcell.yaml examples/libprep_capture.py --seed 0 --time-limit 60 --out docs/reports/libprep3_live.html`. Expected: 3/3 completed, no as-run validation errors. If no initial plan: raise `--time-limit`, then merge consecutive same-instrument steps (spec fallbacks); if still none, stop and report.
- [ ] **Step 5:** check the report in the browser (floor plan shows every instrument with labels, Gantt readable, no console errors).
- [ ] **Step 6:** README section (command, printed numbers, report link); CLAUDE.md note on `step` and the new types.
- [ ] **Step 7:** commit `feat: library prep demo report; docs`
