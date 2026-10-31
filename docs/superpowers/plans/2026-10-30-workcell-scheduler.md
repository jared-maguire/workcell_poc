# Workcell Playground Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Python simulation playground that compiles DAG workflows into synchronized instruction blocks, plans them with greedy / greedy+hotel / CP-SAT planners, executes plans on stateful simulated instruments, and renders a self-contained HTML report (Gantt ×2, synced animation, stats, source, compiled output).

**Architecture:** Pipeline of pure-data stages: `config` (YAML → `Workcell`) → `dsl` (Python workflow functions → `WorkflowSpec`) → `compiler` (→ `CompiledBatch` of `Block` DAGs with resource sets and instrument variables) → `planners` (→ `Plan`: bound blocks + start times + status) → `validate` (invariant checker) → `sim` (executor + simulated instruments → `Trace`) → `stats` → `report` (single HTML file). Planners never touch the simulator or report; everything downstream consumes `Plan`/`Trace` only.

**Tech Stack:** Python 3.14 in `.venv` (already created; `ortools 9.15`, `pyyaml`, `pygments`, `pytest` installed and verified), vanilla JS + SVG for the report.

**Spec:** `docs/superpowers/specs/2026-10-30-workcell-scheduler-design.md`

## Global Constraints

- Run everything with `.venv/bin/python` / `.venv/bin/pytest`. Package dir `workcell/`, tests in `tests/`.
- All time is integer minutes. Default instruction duration is `1` (`durations.default`). Duration lookup: `"<type>.<op>"`, then `"<op>"`, then `"default"`.
- Example workcell sets `check_status: 0` (a read, not a physical action) *(Claude's call; everything else stays 1 min per the spec)*.
- Instrument types (exact strings): `arm`, `storage`, `hotel`, `barcode_scanner`, `head_stamper`, `thermocycler`. Exactly one arm supported in v1.
- Nest ids: `<instrument>` for single-nest instruments and storage/hotel; `stamp1.src` / `stamp1.dst` for head stampers. Nest owner = text before the first `.`.
- Instrument variables are strings starting with `?` (e.g. `?tc0`); var nests are `?var` or `?var.suffix`.
- Every instrument is a unary lock. Plates are locks. Nests have capacities (1 for scanner/thermocycler/stamper nests; `slots` for storage/hotel).
- Compiler never emits acquire/release instructions; the executor locks a block's instruments + plates all-or-nothing.
- Mode strings (exact): `greedy` (= `greedy:round_robin`), `greedy:fifo`, `greedy_hotel` (= `greedy_hotel:round_robin`), `greedy_hotel:fifo`, `cpsat` (= `cpsat:makespan`), `cpsat:makespan`, `cpsat:sum_completion`.
- Report is one self-contained HTML file: no network URLs in `src`/`href`, data embedded as JSON.

## Review Focus

1. **Batch names a barcode not in inventory, or of the wrong type** — a clear `CompileError` naming the barcode and workflow, not a `KeyError`. (Test in Task 3.)
2. **Empty-plate pool runs out** (e.g. 30 workflows, 20 empties) — `CompileError` naming the type and the first workflow left without one. (Test in Task 3.)
3. **CP-SAT hits the time limit with no solution** — `Plan(status="no_solution")`, and the executor, stats, and report all render it without crashing. (Tests in Tasks 9 and 11.)
4. **Run stops partway (alarm or deadlock)** — stats count incomplete workflows as `None` latency, and the report renders the partial timeline with the alarm/deadlock marked. (Tests in Tasks 10 and 11.)
5. **A plate is used after it was stored, or `after=` forms a cycle** — the DSL/compiler rejects it with a message naming the workflow. (Tests in Tasks 2 and 3.)

---

### Task 1: Project scaffold and workcell config

**Files:**
- Create: `pyproject.toml`, `workcell/__init__.py`, `workcell/config.py`, `tests/conftest.py`, `tests/test_config.py`

**Interfaces:**
- Produces:
  - `INSTRUMENT_TYPES: frozenset[str]`
  - `@dataclass(frozen=True) Instrument(id: str, type: str, slots: int | None = None)`
  - `@dataclass(frozen=True) Nest(id: str, instrument: str, capacity: int)`
  - `@dataclass(frozen=True) Plate(barcode: str, type: str, location: str, contents: str = "")`
  - `@dataclass(frozen=True) FailureSpec(op: str, kind: str = "fault", instrument: str | None = None, plate: str | None = None)`
  - `@dataclass Workcell(instruments: dict[str, Instrument], plates: dict[str, Plate], durations: dict[str, int], failures: list[FailureSpec])` with methods `nests() -> dict[str, Nest]`, `nest_owner(nest_id: str) -> str`, `of_type(t: str) -> list[str]` (ids in definition order), `duration(instrument_type: str, op: str) -> int`
  - `parse_workcell(data: dict) -> Workcell`, `load_workcell(path: str | Path) -> Workcell`; raise `ConfigError(ValueError)`
  - Fixture `workcell_small` in `tests/conftest.py`: the example layout below, built via `parse_workcell`.

- [ ] **Step 1: Write `pyproject.toml`** (name `workcell`, `requires-python = ">=3.12"`, deps `ortools`, `pyyaml`, `pygments`; `[tool.pytest.ini_options] testpaths=["tests"]`), then `.venv/bin/pip install -e .`

- [ ] **Step 2: Write the fixture and failing tests**

`tests/conftest.py` defines `SMALL = {...}` and fixture `workcell_small` returning `parse_workcell(SMALL)`:

```python
SMALL = {
    "instruments": [
        {"id": "arm1", "type": "arm"},
        {"id": "store1", "type": "storage", "slots": 2000},
        {"id": "hotel1", "type": "hotel", "slots": 50},
        {"id": "scan1", "type": "barcode_scanner"},
        {"id": "stamp1", "type": "head_stamper"},
        {"id": "tc1", "type": "thermocycler"},
        {"id": "tc2", "type": "thermocycler"},
    ],
    "durations": {"default": 1, "check_status": 0},
    "plates": [
        {"generate": "sample", "prefix": "S", "count": 10, "location": "store1"},
        {"generate": "primer", "prefix": "P", "count": 2, "location": "store1"},
        {"generate": "empty", "prefix": "E", "count": 20, "location": "store1"},
    ],
}
```

`tests/test_config.py`:

```python
def test_nests(workcell_small):
    n = workcell_small.nests()
    assert n["stamp1.src"].capacity == 1 and n["stamp1.dst"].instrument == "stamp1"
    assert n["tc1"].capacity == 1 and n["scan1"].capacity == 1
    assert n["store1"].capacity == 2000 and n["hotel1"].capacity == 50
    assert "arm1" not in n
    assert workcell_small.nest_owner("stamp1.src") == "stamp1"

def test_generated_plates(workcell_small):
    p = workcell_small.plates
    assert p["S0001"].type == "sample" and p["S0010"].location == "store1"
    assert "S0011" not in p and p["E0020"].type == "empty"

def test_duration_lookup():
    wc = parse_workcell({**SMALL, "durations": {"default": 1, "check_status": 0, "thermocycler.run_program": 10}})
    assert wc.duration("thermocycler", "run_program") == 10
    assert wc.duration("barcode_scanner", "check_status") == 0
    assert wc.duration("arm", "move") == 1

def test_of_type(workcell_small):
    assert workcell_small.of_type("thermocycler") == ["tc1", "tc2"]

def test_failures_parsed():
    wc = parse_workcell({**SMALL, "failures": [{"op": "scan", "plate": "S0003", "kind": "barcode_mismatch"}]})
    assert wc.failures == [FailureSpec(op="scan", plate="S0003", kind="barcode_mismatch")]

@pytest.mark.parametrize("bad, msg", [
    ({"instruments": [{"id": "x", "type": "toaster"}]}, "toaster"),
    ({"instruments": SMALL["instruments"] + [{"id": "tc1", "type": "thermocycler"}]}, "duplicate"),
    ({"plates": [{"barcode": "S1", "type": "sample", "location": "tc1"}]}, "storage"),
])
def test_config_errors(bad, msg):
    with pytest.raises(ConfigError, match=msg):
        parse_workcell({**SMALL, **bad})
```

- [ ] **Step 3: Run** `.venv/bin/pytest tests/test_config.py -v` — expect FAIL (import error).

- [ ] **Step 4: Implement `workcell/config.py`.** Generator form produces barcodes `f"{prefix}{i:04d}"` for `i` in `1..count`. Plates must start in a `storage` instrument. `load_workcell` = `yaml.safe_load` + `parse_workcell`.

- [ ] **Step 5: Run** the tests — expect PASS.

- [ ] **Step 6: Commit** `git add -A && git commit -m "feat: workcell config loader"`

---

### Task 2: Workflow DSL and PCR library workflow

**Files:**
- Create: `workcell/dsl.py`, `workcell/library.py`, `tests/test_dsl.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) PlateHandle(id: str)` (local id `"p0"`, `"p1"`, ...); `@dataclass(frozen=True) NewPlate(type: str)`
  - `@dataclass PlateDecl(id: str, mode: Literal["own","borrow"], barcode: str | None, pool: str | None)`
  - `@dataclass Op(id: str, kind: Literal["stamp","thermocycle","store"], plates: tuple[str, ...], params: dict, after: tuple[str, ...])`. For stamp, `plates == (src, dst)`. `id` is `"op0"`, `"op1"`, ...; methods return the `Op` id (`str`).
  - `class Workflow`: `own(x: str | NewPlate) -> PlateHandle`, `borrow(barcode: str) -> PlateHandle`, `new_plate(type: str) -> NewPlate`, `stamp(src, dst, after=None) -> str`, `thermocycle(plate, program, after=None) -> str`, `store(plate, after=None) -> str`. `after` takes one op id or a list of them.
  - `@dataclass WorkflowSpec(name: str, args: dict, plates: dict[str, PlateDecl], ops: list[Op], func: Callable)`
  - `workflow` decorator: `pcr(sample=..., primer=...)` runs the function against a fresh `Workflow` and returns a `WorkflowSpec`.
  - `load_batch(path) -> tuple[list[WorkflowSpec], str]` (specs plus the file's source text). It imports the file by path and calls its `batch()`.
  - `workcell/library.py`: `PCR_PROGRAM` and `pcr(w, sample, primer)`, exactly as in spec §5.

- [ ] **Step 1: Write failing tests** in `tests/test_dsl.py`:

```python
def test_pcr_spec_shape():
    spec = pcr(sample="S0001", primer="P0001")
    assert spec.name == "pcr" and spec.args == {"sample": "S0001", "primer": "P0001"}
    assert [(d.mode, d.barcode, d.pool) for d in spec.plates.values()] == [
        ("own", "S0001", None), ("borrow", "P0001", None), ("own", None, "empty")]
    assert [o.kind for o in spec.ops] == ["stamp", "stamp", "thermocycle", "store", "store", "store"]
    assert spec.ops[1].after == ("op0",)

def test_borrowing_a_new_plate_rejected():
    @workflow
    def bad(w):
        w.borrow(w.new_plate("empty"))
    with pytest.raises(ValueError, match="own"):
        bad()

def test_use_after_store_rejected():
    @workflow
    def bad(w):
        p = w.own("S0001"); w.store(p); w.thermocycle(p, program=[])
    with pytest.raises(ValueError, match="after it was stored"):
        bad()

def test_stamp_same_plate_rejected():
    @workflow
    def bad(w):
        p = w.own("S0001"); w.stamp(src=p, dst=p)
    with pytest.raises(ValueError):
        bad()

def test_load_batch(tmp_path):
    f = tmp_path / "b.py"
    f.write_text("from workcell.library import pcr\n"
                 "def batch():\n    return [pcr(sample='S0001', primer='P0001')]\n")
    specs, src = load_batch(f)
    assert len(specs) == 1 and "def batch" in src
```

- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_dsl.py -v` and confirm it fails.
- [ ] **Step 3: Implement `workcell/dsl.py` and `workcell/library.py`.** The DSL records only the explicit `after` edges. Implicit same-plate edges belong to the compiler. Raise `ValueError` for an unknown `after` id. Load the batch with `importlib.util.spec_from_file_location`.
- [ ] **Step 4: Run** the tests and confirm they pass.
- [ ] **Step 5: Commit** with `feat: workflow DSL and PCR workflow`.

---

### Task 3: IR and compiler

**Files:**
- Create: `workcell/ir.py`, `workcell/compiler.py`, `tests/test_compiler.py`
- Modify: `tests/conftest.py`. Add the fixtures `pcr_specs(n)` (factory: `[pcr(sample=f"S{i+1:04d}", primer=f"P{i % 2 + 1:04d}") for i in range(n)]`) and `compiled_small(workcell_small, n)` (factory).

**Interfaces:**
- Consumes: Task 1 `Workcell` and Task 2 `WorkflowSpec`.
- Produces (in `ir.py`):
  - `@dataclass Instruction(instrument: str, op: str, args: dict, duration: int)`
  - `@dataclass PlateMove(plate: str, src: str, dst: str)`
  - `@dataclass Block(id: str, workflow: str, kind: Literal["fetch","scan","move","op","return","park"], label: str, instructions: list[Instruction], moves: list[PlateMove], plates: set[str], deps: list[str], inserted: bool = False)` with properties `duration` (sum of durations) and `instruments` (set of instruction instruments), plus the method `bind(bindings: dict[str, str]) -> Block`. `bind` returns a copy with every `?var` and `?var.suffix` resolved in instruments, move nests, and string arg values.
  - `resolve(name: str, bindings: dict[str, str]) -> str`. It raises `KeyError` for an unbound var.
  - `@dataclass CompiledWorkflow(id: str, name: str, args: dict, blocks: list[Block], variables: dict[str, list[str]], owned: set[str], borrowed: set[str], barcodes: dict[str, str])`. `barcodes` maps plate handle to barcode. It also has the method `borrow_span(plate) -> tuple[str, str]`, giving the ids of the first and last blocks that touch the plate.
  - `@dataclass CompiledBatch(workcell: Workcell, workflows: list[CompiledWorkflow], specs: list[WorkflowSpec])` with `all_blocks() -> list[Block]` (workflow order, then block order), `block(id) -> Block`, and `initial_locations() -> dict[str, str]` (plate to nest, covering only the plates the batch uses).
- Produces (in `compiler.py`):
  - `class CompileError(ValueError)`
  - `compile_batch(workcell: Workcell, specs: list[WorkflowSpec]) -> CompiledBatch`
  - `transport_instructions(workcell: Workcell, plate: str, src: str, dst: str, var_types: dict[str, str] | None = None) -> list[Instruction]`. Greedy+hotel reuses this (Task 8).

**Compiler rules** (from spec §6, plus Claude's calls):
- Workflow id is `f"{spec.name}-{i}"`, where `i` is the batch index. Block id is `f"{wf_id}/{n:02d}"`, in emission order.
- `new_plate(type)` gets the first barcode of that type, in inventory order, that no other workflow uses explicitly and that hasn't been allocated yet. Allocation goes in batch order.
- Variables are created per workflow. `?stamp` is one per workflow, since the destination plate stays in the stamper. `?scanK` is one per fetch. `?tcK` is one per thermocycle op. A variable with exactly one candidate is substituted at compile time, so it never shows up in `variables`. Only one arm is allowed. With no instrument of a needed type, raise `CompileError`.
- Op dependencies are the explicit `after` edges plus the previous op on each of the op's plates. Each op's first blocks (the start of each transport chain) and its op block depend on the last block of every op it depends on. Within a transport chain, each block depends on the one before it.
- Plate location is tracked per plate. To reach the nest an op needs:
  - From storage, the chain is `fetch` (storage → `?scanK`), then `scan`, then `move` (scanner → nest).
  - From any other nest, it's a single `move`.
  - A plate already in the right nest needs no transport.
  - `store` emits one `return` block to the plate's original storage location. Any plate not stored by the end of the workflow gets an automatic `return`.
- Stamp nests are `?stamp.src` and `?stamp.dst`. The thermocycle nest is `?tcK`. The op block holds the op instruction alone, after the check.
- `transport_instructions` emits, in this order:
  1. `check_status` on the source owner, then the arm, then the destination owner
  2. `retrieve(plate=)` if the source is storage
  3. `open_door` on each thermocycler endpoint
  4. `move(plate=, src=, dst=)` on the arm
  5. `close_door` on each thermocycler endpoint
  6. `store(plate=)` if the destination is storage

  Owner types for var names come from `var_types`.
- Errors (all `CompileError`):
  - unknown barcode: `"unknown plate barcode 'X' (workflow pcr-3)"`
  - wrong type for `own`/`borrow`: the error names the barcode
  - empty pool exhausted: `"no unused plates of type 'empty' left (needed by pcr-7)"`
  - a plate owned by one workflow and used by any other: `"plate S0001 owned by pcr-0 is also used by pcr-1"`
  - a cycle in the op DAG: the error names the workflow

- [ ] **Step 1: Write failing tests** in `tests/test_compiler.py`. Block ids for pcr-0 are `00`–`15`, in this order:
  - S: fetch, scan, move
  - E: fetch, scan, move
  - stamp a
  - P: fetch, scan, move
  - stamp b
  - E move to tc, run
  - return S, return P, return E

```python
def test_pcr_block_kinds(compiled_small):
    wf = compiled_small(1).workflows[0]
    assert wf.id == "pcr-0" and wf.barcodes["p2"] == "E0001"
    assert [b.kind for b in wf.blocks] == (
        ["fetch", "scan", "move"] * 2 + ["op"] + ["fetch", "scan", "move", "op"]
        + ["move", "op"] + ["return"] * 3)
    assert wf.variables == {"?tc0": ["tc1", "tc2"]}
    assert wf.owned == {"S0001", "E0001"} and wf.borrowed == {"P0001"}

def test_fetch_instructions(compiled_small):
    b = compiled_small(1).block("pcr-0/00")
    assert [(i.instrument, i.op) for i in b.instructions] == [
        ("store1", "check_status"), ("arm1", "check_status"), ("scan1", "check_status"),
        ("store1", "retrieve"), ("arm1", "move")]
    assert b.instructions[-1].args == {"plate": "S0001", "src": "store1", "dst": "scan1"}
    assert b.moves == [PlateMove("S0001", "store1", "scan1")] and b.duration == 2

def test_tc_blocks_use_var_and_doors(compiled_small):
    batch = compiled_small(1)
    load, run, ret_e = batch.block("pcr-0/11"), batch.block("pcr-0/12"), batch.block("pcr-0/15")
    assert [(i.instrument, i.op) for i in load.instructions][3:] == [
        ("?tc0", "open_door"), ("arm1", "move"), ("?tc0", "close_door")]
    assert load.moves == [PlateMove("E0001", "stamp1.dst", "?tc0")]
    assert run.instructions[-1].op == "run_program" and run.instruments == {"?tc0"}
    assert ret_e.duration == 4 and ret_e.instructions[-1].op == "store"

def test_dependencies(compiled_small):
    blk = compiled_small(1).block
    assert set(blk("pcr-0/06").deps) == {"pcr-0/02", "pcr-0/05"}
    assert "pcr-0/06" in blk("pcr-0/07").deps          # primer fetch waits for stamp a
    assert "pcr-0/06" in blk("pcr-0/13").deps          # return S after stamp a
    assert "pcr-0/12" in blk("pcr-0/15").deps          # return E after cycling

def test_bind_resolves_vars(compiled_small):
    b = compiled_small(1).block("pcr-0/11").bind({"?tc0": "tc2"})
    assert "tc2" in b.instruments and b.moves[0].dst == "tc2"
    with pytest.raises(KeyError):
        resolve("?tc0", {})

def test_shared_primer_and_empty_allocation(compiled_small):
    batch = compiled_small(3)
    assert [w.barcodes["p2"] for w in batch.workflows] == ["E0001", "E0002", "E0003"]
    assert batch.workflows[0].borrowed == batch.workflows[2].borrowed == {"P0001"}
    assert batch.workflows[0].borrow_span("P0001") == ("pcr-0/07", "pcr-0/14")

@pytest.mark.parametrize("specs, msg", [
    (lambda: [pcr(sample="S9999", primer="P0001")], "unknown plate barcode 'S9999'"),
    (lambda: [pcr(sample="P0001", primer="P0002")], "P0001"),
    (lambda: [pcr(sample="S0001", primer="P0001"), pcr(sample="S0001", primer="P0001")], "owned by pcr-0"),
])
def test_compile_errors(workcell_small, specs, msg):
    with pytest.raises(CompileError, match=msg):
        compile_batch(workcell_small, specs())

def test_empty_pool_exhausted(workcell_small):
    wc = parse_workcell({**SMALL, "plates": SMALL["plates"][:2] + [
        {"generate": "empty", "prefix": "E", "count": 2, "location": "store1"}]})
    with pytest.raises(CompileError, match="no unused plates of type 'empty' left \\(needed by pcr-2\\)"):
        compile_batch(wc, [pcr(sample=f"S{i+1:04d}", primer="P0001") for i in range(3)])

def test_cycle_rejected(workcell_small):
    @workflow
    def cyc(w):
        p = w.own("S0001"); q = w.own("S0002")
        a = w.thermocycle(p, program=[]); w.thermocycle(q, program=[], after=a)
    spec = cyc(); spec.ops[0].after = (spec.ops[1].id,)   # force a cycle
    with pytest.raises(CompileError, match="cyc-0"):
        compile_batch(workcell_small, [spec])
```

- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_compiler.py -v` and confirm it fails.
- [ ] **Step 3: Implement `workcell/ir.py`.**
- [ ] **Step 4: Implement `workcell/compiler.py`** following the rules above.
- [ ] **Step 5: Run** the tests and confirm they pass.
- [ ] **Step 6: Commit** with `feat: IR and compiler`.

---

### Task 4: Plan model and validator

**Files:**
- Create: `workcell/plan.py`, `workcell/validate.py`, `tests/test_validate.py`

**Interfaces:**
- Consumes: Task 3 `CompiledBatch`, `Block.bind`.
- Produces:
  - `@dataclass Wait(block: str, reason: str)`, `@dataclass Deadlock(time: int, waits: list[Wait])`
  - `@dataclass Plan(mode: str, blocks: dict[str, Block], start: dict[str, int], bindings: dict[str, dict[str, str]], status: Literal["complete","deadlock","optimal","feasible","no_solution"], deadlock: Deadlock | None = None, solve_seconds: float = 0.0, objective: str | None = None)`, with:
    - `end(block_id) -> int`
    - `makespan: int`: the max end, or 0 if nothing was scheduled
    - `finished: bool`: true when status is complete, optimal or feasible
    - `ordered() -> list[Block]`: scheduled blocks by `(start, id)`
  - `bind_batch(batch: CompiledBatch, bindings: dict[str, dict[str, str]]) -> dict[str, Block]` (keyed by block id; a workflow with no entry gets `{}`)
  - `plate_stays(plan: Plan, initial: dict[str, str]) -> list[tuple[str, str, int, int | None]]`: tuples of `(plate, nest, arrive, leave)`. A plate arrives at the end of the block that moved it there and leaves at the start of the block that moves it away. The initial stays arrive at 0, and `None` means the plate is still there.
  - `validate_plan(plan: Plan, batch: CompiledBatch) -> list[str]`: human-readable errors, with an empty list meaning valid.

**Validator checks**, applied to scheduled blocks:
- no unresolved `?` names
- each dependency is scheduled and ends at or before the dependent block's start (message contains `"dependency"`)
- no two overlapping blocks share an instrument (message names the instrument) or a plate
- each plate's move chain is continuous from its initial location (each move's `src` is where the plate currently is)
- concurrent stays at a nest never exceed its capacity (message names the nest)
- the borrow spans for a plate never overlap across workflows
- if `plan.finished`, every compiled block id is present and scheduled

- [ ] **Step 1: Write failing tests** in `tests/test_validate.py`, using this helper:

```python
VALID = ["00","01","02","03","04","05","06","13","07","08","09","10","14","11","12","15"]

def sequential(batch, order, bindings={"pcr-0": {"?tc0": "tc1"}}, status="complete"):
    blocks = bind_batch(batch, bindings); t = 0; start = {}
    for s in order:
        bid = f"pcr-0/{s}"; start[bid] = t; t += blocks[bid].duration
    return Plan(mode="test", blocks=blocks, start=start, bindings=bindings, status=status)

def test_valid_sequential(compiled_small):
    b = compiled_small(1)
    assert validate_plan(sequential(b, VALID), b) == []

def test_dependency_violation(compiled_small):
    b = compiled_small(1); p = sequential(b, VALID); p.start["pcr-0/07"] = 0
    assert any("dependency" in e for e in validate_plan(p, b))

def test_instrument_overlap(compiled_small):
    b = compiled_small(1); p = sequential(b, VALID); p.start["pcr-0/13"] = p.start["pcr-0/06"]
    assert any("stamp1" in e for e in validate_plan(p, b))

def test_nest_capacity(compiled_small):
    b = compiled_small(1)
    order = ["00","01","02","03","04","05","06","07","08","09","13","10","14","11","12","15"]
    assert any("stamp1.src" in e for e in validate_plan(sequential(b, order), b))

def test_unbound_var(compiled_small):
    b = compiled_small(1)
    with pytest.raises(KeyError):
        sequential(b, VALID, bindings={})

def test_missing_blocks_when_finished(compiled_small):
    b = compiled_small(1)
    assert validate_plan(sequential(b, VALID[:-1]), b)
    assert validate_plan(sequential(b, VALID[:-1], status="deadlock"), b) == []

def test_plate_stays(compiled_small):
    b = compiled_small(1); p = sequential(b, VALID)
    stays = plate_stays(p, b.initial_locations())
    assert ("S0001", "store1", 0, 0) in stays
    assert any(s[0] == "S0001" and s[1] == "scan1" for s in stays)
```

- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_validate.py -v` and confirm it fails.
- [ ] **Step 3: Implement `workcell/plan.py` and `workcell/validate.py`.** `bind_batch` lets the `KeyError` from an unbound var propagate.
- [ ] **Step 4: Run** the tests and confirm they pass.
- [ ] **Step 5: Commit** with `feat: plan model and validator`.

---

### Task 5: Simulated instruments

**Files:**
- Create: `workcell/sim/__init__.py`, `workcell/sim/world.py`, `tests/test_world.py`

**Interfaces:**
- Consumes: Task 1 `Workcell`, `FailureSpec`; Task 3 `Instruction` (concrete).
- Produces:
  - `class InstrumentError(Exception)`, with attributes `instrument`, `op`, `message`.
  - `class World(workcell: Workcell, locations: dict[str, str])` with:
    - `apply(instr: Instruction) -> None`, which raises `InstrumentError`
    - `location(plate) -> str`
    - `occupants(nest) -> list[str]`
    - `door_open(tc_id) -> bool`
    - `presented(storage_id) -> str | None`
    - `snapshot() -> dict`: plate locations, door states, presented trays

**Instrument rules** (any violation raises `InstrumentError` with a specific message):
- `check_status`: the instrument exists and isn't faulted. It fails if a matching `FailureSpec` has kind `fault`.
- **Storage:**
  - `retrieve(plate)`: the plate must be in this storage and the tray empty. It moves to the tray.
  - `store(plate)`: the plate must be on the tray. It goes back inside.
- **Arm:** `move(plate, src, dst)`
  - The plate must be at `src`. If `src` is a storage, the plate must be on its tray.
  - Every thermocycler endpoint must have its door open.
  - `dst` must have spare capacity. If `dst` is a storage, the plate lands on the tray, which must be empty.
- **Scanner:** `scan(expect)`: the plate in the scanner nest must equal `expect`. A matching `barcode_mismatch` failure simulates a misread and raises.
- **Head stamper:** `stamp(src, dst)`: `src` must be in `<id>.src` and `dst` in `<id>.dst`.
- **Thermocycler:**
  - `open_door` and `close_door` raise if the door is already in that state.
  - `run_program(program)`: the door must be closed and exactly one plate inside.
- **Failure matching:** `op` equals the instruction op, the `instrument` (when given) equals it, and the `plate` (when given) appears among the string arg values.

- [ ] **Step 1: Write failing tests** in `tests/test_world.py`. Use a helper `I(inst, op, **args)` that builds an `Instruction(inst, op, args, 1)`.

```python
def world(wc, **locs):
    return World(wc, {"S0001": "store1", "E0001": "store1", **locs})

def test_fetch_scan_sequence(workcell_small):
    w = world(workcell_small)
    for i in [I("store1","retrieve",plate="S0001"), I("arm1","move",plate="S0001",src="store1",dst="scan1"),
              I("scan1","scan",expect="S0001")]:
        w.apply(i)
    assert w.location("S0001") == "scan1" and w.presented("store1") is None

@pytest.mark.parametrize("instrs, msg", [
    ([I("arm1","move",plate="S0001",src="store1",dst="scan1")], "tray"),
    ([I("store1","retrieve",plate="S0001"), I("store1","retrieve",plate="E0001")], "tray"),
    ([I("store1","retrieve",plate="S0001"), I("arm1","move",plate="S0001",src="store1",dst="tc1")], "door"),
    ([I("tc1","open_door"), I("tc1","open_door")], "already open"),
    ([I("tc1","close_door")], "already closed"),
    ([I("stamp1","stamp",src="S0001",dst="E0001")], "stamp1.src"),
    ([I("tc1","run_program",program=[])], "no plate"),
])
def test_illegal(workcell_small, instrs, msg):
    w = world(workcell_small)
    with pytest.raises(InstrumentError, match=msg):
        for i in instrs: w.apply(i)

def test_full_nest(workcell_small):
    w = world(workcell_small, S0002="scan1")
    w.apply(I("store1","retrieve",plate="S0001"))
    with pytest.raises(InstrumentError, match="scan1"):
        w.apply(I("arm1","move",plate="S0001",src="store1",dst="scan1"))

def test_barcode_mismatch_injection(workcell_small):
    wc = dataclasses.replace(workcell_small, failures=[FailureSpec(op="scan", plate="S0001", kind="barcode_mismatch")])
    w = world(wc, S0001="scan1")
    with pytest.raises(InstrumentError, match="barcode"):
        w.apply(I("scan1","scan",expect="S0001"))
```

- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_world.py -v` and confirm it fails.
- [ ] **Step 3: Implement `workcell/sim/world.py`.** Keep one dispatch table keyed by instrument type. Thermocycler doors start closed.
- [ ] **Step 4: Run** the tests and confirm they pass.
- [ ] **Step 5: Commit** with `feat: simulated instruments`.

---

### Task 6: Executor and trace

**Files:**
- Create: `workcell/sim/executor.py`, `tests/test_executor.py`

**Interfaces:**
- Consumes: Task 4 `Plan`, Task 3 `CompiledBatch.initial_locations()`, Task 5 `World`.
- Produces (all dataclasses):
  - `InstructionEvent(time: int, block: str, instrument: str, op: str, args: dict, duration: int)`
  - `MoveEvent(time: int, plate: str, src: str, dst: str)`
  - `Alarm(time: int, block: str, instrument: str, op: str, message: str)`
  - `Trace(events: list[InstructionEvent], moves: list[MoveEvent], spans: dict[str, tuple[int, int]], lock_waits: list[tuple[str, int]], alarm: Alarm | None, end_time: int, initial_locations: dict[str, str], final_locations: dict[str, str])`. `lock_waits` holds `(block, minutes)` pairs.
  - `execute(plan: Plan, batch: CompiledBatch) -> Trace`

**Executor algorithm** (discrete events on integer minutes):
- Blocks are taken in `plan.ordered()` order.
- At each time step `t`:
  1. Release the locks of blocks that end at `t`.
  2. Start every pending block whose planned start is at or before `t`, whose dependencies have all ended, and whose instruments and plates are all free. Acquire all of its locks at once.
- A block that starts later than planned records a lock wait for the difference.
- Each instruction runs at its own start time: the block start plus the durations of the instructions before it. Process instructions across blocks in `(time, block start order)` order, using `World.apply`.
- On `InstrumentError`, record the `Alarm`, stop immediately, and leave the running blocks' spans ending at the alarm time.
- `end_time` is the time of the last event that completed.

- [ ] **Step 1: Write failing tests** in `tests/test_executor.py`, reusing `sequential` and `VALID` from Task 4 (move them to `tests/helpers.py`):

```python
def test_valid_plan_runs_clean(compiled_small):
    b = compiled_small(1); p = sequential(b, VALID); t = execute(p, b)
    assert t.alarm is None and t.lock_waits == []
    assert t.end_time == p.makespan
    assert set(t.final_locations.values()) == {"store1"}
    assert t.spans["pcr-0/00"] == (0, 2)

def test_capacity_violation_raises_alarm(compiled_small):
    b = compiled_small(1)
    order = ["00","01","02","03","04","05","06","07","08","09","13","10","14","11","12","15"]
    t = execute(sequential(b, order), b)
    assert t.alarm is not None and "stamp1.src" in t.alarm.message and t.alarm.block == "pcr-0/09"

def test_injected_mismatch_halts(compiled_small, workcell_small):
    wc = dataclasses.replace(workcell_small, failures=[FailureSpec(op="scan", plate="S0001", kind="barcode_mismatch")])
    b = compile_batch(wc, [pcr(sample="S0001", primer="P0001")])
    t = execute(sequential(b, VALID), b)
    assert t.alarm.block == "pcr-0/01" and all(e.time <= t.alarm.time for e in t.events)

def test_overlapping_plan_waits_for_locks(compiled_small):
    b = compiled_small(1); p = sequential(b, VALID); p.start["pcr-0/03"] = 0
    t = execute(p, b)
    assert any(blk == "pcr-0/03" and mins > 0 for blk, mins in t.lock_waits)

def test_partial_plan(compiled_small):
    b = compiled_small(1)
    t = execute(sequential(b, VALID[:5], status="deadlock"), b)
    assert t.alarm is None and len(t.spans) == 5
```

- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_executor.py -v` and confirm it fails.
- [ ] **Step 3: Implement `workcell/sim/executor.py`.**
- [ ] **Step 4: Run** the tests and confirm they pass.
- [ ] **Step 5: Commit** with `feat: plan executor`.

---

### Task 7: Greedy planner, deadlock detection, planner registry

**Files:**
- Create: `workcell/planners/__init__.py`, `workcell/planners/greedy.py`, `tests/test_greedy.py`

**Interfaces:**
- Consumes: Task 3 `CompiledBatch` and `transport_instructions`; Task 4 `Plan`, `Deadlock`, `Wait`, `validate_plan`.
- Produces:
  - `plan_greedy(batch: CompiledBatch, priority: Literal["round_robin","fifo"] = "round_robin", park: bool = False) -> Plan`, with mode string `"greedy:<priority>"`, or `"greedy_hotel:<priority>"` when `park=True`
  - `make_plan(batch: CompiledBatch, mode: str, time_limit: float = 30.0) -> Plan` in `planners/__init__.py`. It parses the mode strings listed in Global Constraints and raises `ValueError` for unknown modes, listing the valid ones. It wires `cpsat` modes in Task 9; until then they raise `ValueError`.

**Greedy algorithm** (pin these details exactly, since the deadlock tests depend on them):

```
t = 0; running = {}; done = set(); bindings = {wf: {} for wf}; occupancy[nest] = initial plate counts
loop:
  for each block in running whose end == t: mark done; release instrument/plate locks
  repeat until no block starts in a full pass:
    candidates = ready blocks (all deps done, not started), ordered by priority:
      fifo: (workflow index, block index)
      round_robin: workflows in rotation starting after the workflow that most recently started a block
                   (first pass at t=0 starts at workflow 0), then block index
    for c in candidates:
      try to bind c's unbound vars: for each var, first candidate instrument (in variables[] order)
        such that the resulting concrete block's instruments are free and its dst nests have capacity
      startable iff: all instruments free, all plates unlocked, every move dst has capacity
        (count plates currently in the nest + plates reserved by running blocks), and for borrowed
        plates the borrow is free or already held by c's workflow
      if startable: start at t; lock; for each move: occupancy[src] -= 1, occupancy[dst] += 1 (reserve at start);
        record borrow holder at the borrow span's first block, release at end of its last block;
        commit var bindings for c's workflow; break and restart the pass
  if every block done: status "complete"
  elif running empty: status "deadlock"; Deadlock(time=t, waits=[Wait(block, reason) for each ready block])
       where reason names what blocks it, e.g. "nest stamp1.src full (S0001 of pcr-0)", "instrument arm1 busy",
       "nest scan1 full (S0002 of pcr-1)"; stop
  else t = min end over running
```

Return `Plan(blocks=<bound blocks for every compiled block whose vars are bound, plus inserted>, start=..., bindings=..., status=...)`. On a deadlock, `blocks` contains only the bound blocks that were scheduled, since the unscheduled ones may have unbound vars.

- [ ] **Step 1: Write failing tests** in `tests/test_greedy.py`:

```python
def test_fifo_two_workflows_complete_and_valid(compiled_small):
    b = compiled_small(2); p = plan_greedy(b, priority="fifo")
    assert p.status == "complete" and p.mode == "greedy:fifo"
    assert validate_plan(p, b) == []
    t = execute(p, b); assert t.alarm is None and t.lock_waits == []

def test_round_robin_deadlocks_on_two_pcr(compiled_small):
    b = compiled_small(2); p = plan_greedy(b)          # default round_robin
    assert p.status == "deadlock" and p.deadlock.time == 7
    blocked = {w.block: w.reason for w in p.deadlock.waits}
    assert "stamp1.src" in blocked["pcr-1/02"] and "scan1" in blocked["pcr-0/03"]
    assert validate_plan(p, b) == []
    assert len(execute(p, b).spans) == 5

def test_thermocycler_binding_spreads(compiled_small):
    p = plan_greedy(compiled_small(2), priority="fifo")
    assert {p.bindings["pcr-0"]["?tc0"], p.bindings["pcr-1"]["?tc0"]} == {"tc1", "tc2"}

def test_make_plan_modes(compiled_small):
    b = compiled_small(2)
    assert make_plan(b, "greedy").mode == "greedy:round_robin"
    assert make_plan(b, "greedy:fifo").status == "complete"
    with pytest.raises(ValueError, match="greedy_hotel"):
        make_plan(b, "bogus")
```

- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_greedy.py -v` and confirm it fails.
- [ ] **Step 3: Implement `workcell/planners/greedy.py` and the registry.** Leave a `park` hook in the candidate loop for Task 8, and leave it inactive for now.
- [ ] **Step 4: Run** the tests and confirm they pass. If `deadlock.time` differs from 7, re-check the round-robin rotation rule before changing the test. Work the trace out by hand: A fetches S (0–2) and scans it (2–3). A moves S to stamp1.src (3–4). B fetches S (4–6) and scans it (6–7). At 7, B's S has nowhere to go.
- [ ] **Step 5: Commit** with `feat: greedy planner with deadlock detection`.

---

### Task 8: Greedy + hotel parking

**Files:**
- Modify: `workcell/planners/greedy.py`
- Test: `tests/test_greedy.py`

**Interfaces:**
- Consumes: Task 3 `transport_instructions`, Task 7's engine.
- Produces: the `plan_greedy(..., park=True)` behavior, and `make_plan` modes `greedy_hotel[:priority]`.

**Parking rule.** A park block is created when all of these hold:
- `park=True`
- a candidate is a `move` block
- its only failing condition is that its destination nest is full
- its plate currently sits in a nest whose owner is neither storage nor hotel
- the arm, the plate's current nest owner, and the hotel are all free

When that happens:
- Create `Block(id=f"{c.id}.park", workflow=c.workflow, kind="park", label=f"park {plate} → {hotel}", inserted=True, deps=[], moves=[PlateMove(plate, cur, hotel)], instructions=transport_instructions(...))`, and start it now.
- Rewrite `c` in place. Keep its id, set its source to `hotel`, regenerate its instructions, and append the park id to its deps. Change its label to start with `"unpark"`.
- Park a given block at most once.
- Use the first hotel that has capacity.

- [ ] **Step 1: Write failing tests** (append):

```python
def test_hotel_breaks_deadlock(compiled_small):
    b = compiled_small(2); p = plan_greedy(b, park=True)
    assert p.status == "complete" and p.mode == "greedy_hotel:round_robin"
    parks = [blk for blk in p.blocks.values() if blk.inserted]
    assert parks and all(blk.kind == "park" and blk.moves[0].dst == "hotel1" for blk in parks)
    assert validate_plan(p, b) == []
    t = execute(p, b); assert t.alarm is None and t.lock_waits == []

def test_hotel_completes_ten(compiled_small):
    b = compiled_small(10); p = make_plan(b, "greedy_hotel")
    assert p.status == "complete" and validate_plan(p, b) == []
    assert execute(p, b).alarm is None
```

- [ ] **Step 2: Run** the tests and confirm they fail.
- [ ] **Step 3: Implement the parking rule.**
- [ ] **Step 4: Run** `.venv/bin/pytest tests/test_greedy.py -v` and confirm it passes.
- [ ] **Step 5: Commit** with `feat: greedy+hotel parking mode`.

---

### Task 9: CP-SAT planner

**Files:**
- Create: `workcell/planners/cpsat.py`, `tests/test_cpsat.py`
- Modify: `workcell/planners/__init__.py` (wire `cpsat` modes)

**Interfaces:**
- Consumes: Task 3 `CompiledBatch`; Task 4 `Plan`, `bind_batch`; Task 7 `plan_greedy` (hint).
- Produces: `plan_cpsat(batch: CompiledBatch, objective: Literal["makespan","sum_completion"] = "makespan", time_limit: float = 30.0, hint: bool = True) -> Plan`, with mode `f"cpsat:{objective}"`. Status is `"optimal"`, `"feasible"`, or `"no_solution"`. A `no_solution` plan has empty `start` and `blocks`. `solve_seconds` is filled in.

**Model** (spec §7; direct transports only, no parking):
- **Blocks:** one fixed-size interval per compiled block. Horizon is the sum of all block durations.
- **Bindings:** a bool `x[wf, var, cand]` per variable candidate, with exactly one true per `(wf, var)`. Each block that uses the var gets an optional interval per candidate, present when that candidate's bool is true.
- **Instruments and plates:** a `NoOverlap` per concrete instrument, built from the blocks' fixed and optional intervals, and a `NoOverlap` per plate.
- **Dependencies:** `start[b] >= end[d]` for each dependency `d` of `b`.
- **Stays:**
  - Each move into a nest by block A, with the plate's next move out by block D, makes a stay interval from `end[A]` to `start[D]`, with size ≥ 0.
  - When the nest is a var nest, the stay is an optional interval per candidate.
  - Initial stays in storage are skipped. So are all storage and hotel stays, since those have effectively unlimited capacity.
  - For nests with capacity 1, add a `NoOverlap` over all stays at that nest.
- **Borrow spans:** per borrowed plate, one interval per borrowing workflow from `start[first]` to `end[last]`, with a `NoOverlap` across them.
- **Objectives:** `makespan` minimizes the max end. `sum_completion` minimizes the sum, over workflows, of each workflow's last block end.
- **Hint:** if `hint`, run `plan_greedy(batch, priority="fifo", park=False)`. If it completes, `AddHint` its starts and bindings.
- **Solver:** `num_search_workers = 8`, `max_time_in_seconds = time_limit`.

- [ ] **Step 1: Write failing tests** in `tests/test_cpsat.py`:

```python
def test_cpsat_valid_and_executes(compiled_small):
    b = compiled_small(2); p = plan_cpsat(b, time_limit=10)
    assert p.status in ("optimal", "feasible") and p.mode == "cpsat:makespan"
    assert validate_plan(p, b) == []
    t = execute(p, b); assert t.alarm is None and t.lock_waits == []

def test_cpsat_beats_or_ties_greedy(compiled_small):
    b = compiled_small(4)
    g = plan_greedy(b, priority="fifo"); c = plan_cpsat(b, time_limit=20)
    assert c.makespan <= g.makespan

def test_sum_completion_objective(compiled_small):
    b = compiled_small(3)
    def total(p): return sum(max(p.end(x.id) for x in w.blocks) for w in b.workflows)
    s = plan_cpsat(b, objective="sum_completion", time_limit=20)
    assert validate_plan(s, b) == [] and total(s) <= total(plan_greedy(b, priority="fifo"))

def test_no_solution_when_time_limit_tiny(compiled_small):
    p = plan_cpsat(compiled_small(10), time_limit=0.001, hint=False)
    assert p.status in ("no_solution", "feasible", "optimal")
    if p.status == "no_solution":
        assert p.start == {} and not p.finished

def test_registry(compiled_small):
    assert make_plan(compiled_small(1), "cpsat:sum_completion", time_limit=5).mode == "cpsat:sum_completion"
```

- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_cpsat.py -v` and confirm it fails.
- [ ] **Step 3: Implement `workcell/planners/cpsat.py`** and register its modes.
- [ ] **Step 4: Run** the tests and confirm they pass. Then run `.venv/bin/pytest -v` and confirm the full suite passes.
- [ ] **Step 5: Commit** with `feat: CP-SAT planner`.

---

### Task 10: Stats and run orchestration

**Files:**
- Create: `workcell/stats.py`, `workcell/run.py`, `tests/test_stats.py`

**Interfaces:**
- Consumes: Tasks 1–9.
- Produces:
  - `@dataclass Stats(status: str, makespan: int, completed: int, total: int, latency: dict[str, int | None], mean_latency: float | None, max_latency: int | None, waiting: dict[str, int | None], utilization: dict[str, float], solve_seconds: float, lock_waits: int, alarm: str | None)`
    - `latency` per workflow is the last span end minus the first span start, or `None` if not every block completed.
    - `waiting` is the latency minus the sum of the workflow's block durations.
    - `utilization` per instrument is the busy minutes in trace spans divided by the trace `end_time`, or 0 when `end_time == 0`.
  - `compute_stats(plan: Plan, trace: Trace, batch: CompiledBatch) -> Stats`
  - `@dataclass RunResult(mode: str, plan: Plan, trace: Trace, stats: Stats, errors: list[str])`
  - `@dataclass Session(workcell: Workcell, batch: CompiledBatch, runs: list[RunResult], workcell_source: str, batch_source: str)`
  - `run_session(workcell, specs, modes: list[str], time_limit: float, workcell_source: str = "", batch_source: str = "") -> Session`, which runs compile, then plan and validate per mode, then execute and stats.
  - `run_files(workcell_path, batch_path, modes, time_limit) -> Session`

- [ ] **Step 1: Write failing tests** in `tests/test_stats.py`:

```python
def test_stats_complete(compiled_small):
    b = compiled_small(2); p = plan_greedy(b, priority="fifo"); s = compute_stats(p, execute(p, b), b)
    assert s.completed == s.total == 2 and s.makespan == p.makespan
    assert all(v is not None and v > 0 for v in s.latency.values())
    assert 0 < s.utilization["arm1"] <= 1

def test_stats_deadlock_partial(compiled_small):
    b = compiled_small(2); p = plan_greedy(b); s = compute_stats(p, execute(p, b), b)
    assert s.status == "deadlock" and s.completed == 0 and s.latency["pcr-0"] is None and s.max_latency is None

def test_stats_no_solution(compiled_small):
    b = compiled_small(1)
    p = Plan(mode="cpsat:makespan", blocks={}, start={}, bindings={}, status="no_solution")
    s = compute_stats(p, execute(p, b), b)
    assert s.makespan == 0 and s.completed == 0 and s.utilization["arm1"] == 0

def test_run_session(workcell_small, pcr_specs):
    sess = run_session(workcell_small, pcr_specs(2), ["greedy", "greedy_hotel", "cpsat"], time_limit=10)
    assert [r.mode for r in sess.runs] == ["greedy:round_robin", "greedy_hotel:round_robin", "cpsat:makespan"]
    assert all(r.errors == [] for r in sess.runs)
```

- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_stats.py -v` and confirm it fails.
- [ ] **Step 3: Implement** `stats.py` and `run.py`.
- [ ] **Step 4: Run** the tests and confirm they pass.
- [ ] **Step 5: Commit** with `feat: stats and run orchestration`.

---

### Task 11: HTML report

**Files:**
- Create: `workcell/report/__init__.py`, `workcell/report/build.py`, `workcell/report/template.html`, `workcell/report/report.js`, `workcell/report/report.css`, `tests/test_report.py`

**Interfaces:**
- Consumes: Task 10 `Session`.
- Produces:
  - `report_data(session: Session) -> dict`, the JSON contract below
  - `build_report(session: Session) -> str`, the full HTML
  - `write_report(session: Session, path) -> Path`

**JSON contract.** The data is embedded as `<script id="workcell-data" type="application/json">`, with `</` escaped as `<\/`. The CSS and JS are inlined into the template.

```
{ "instruments": [{"id","type","nests":[{"id","capacity"}]}],
  "workflows":   [{"id","name","args","owned":[],"borrowed":[]}],
  "sources":     {"workflows": "<pygments html of inspect.getsource for each distinct workflow func>",
                  "batch": "<pygments html>", "workcell": "<pygments html>"},
  "compiled":    [{"workflow","variables":{},"blocks":[{"id","kind","label","duration","deps":[],
                    "plates":[],"instruments":[],"instructions":[{"instrument","op","args","duration"}]}]}],
  "runs": [{"mode","status","stats":{...Stats fields...},"errors":[],
            "blocks":[{"id","workflow","kind","label","inserted","start","end","instruments":[],"plates":[]}],
            "moves":[{"time","plate","src","dst"}], "initial_locations":{},
            "alarm": null | {"time","block","instrument","op","message"},
            "deadlock": null | {"time","waits":[{"block","reason"}]},
            "bindings":{}}] }
```

The run `blocks` take their start and end times from `trace.spans`, meaning what actually happened. Args values that aren't JSON-serializable, such as a program's tuples, are converted with `list`.

**Page sections.** Use the brainstorm mockup at `.superpowers/brainstorm/*/content/renditions.html` as the visual reference.
1. **Header and mode tabs:** one tab per run, with a status badge: complete, optimal, feasible, deadlock, no solution, or alarm.
2. **Stats:** a side-by-side comparison table across all runs, covering makespan, completed/total, mean and max latency, solver status and time, lock waits, and alarm. The active run also gets utilization bars per instrument.
3. **Gantt by instrument:** one row per instrument, with bars colored by workflow. A bar's tooltip shows the block label and instructions. Planner-inserted (park) blocks are hatched.
4. **Gantt by workflow:** one row per workflow, with bars colored by instrument type and a gray background span for waiting.
5. **Animated floor plan:**
   - Instruments sit in an auto grid, grouped by type. Each nest is a slot.
   - Plates are labeled by barcode and colored by owning workflow. Borrowed plates have an outline. A plate's position at time `t` comes from `initial_locations` plus every move up to `t`, and a plate in transit is drawn at its destination.
   - Busy instruments are highlighted.
   - Controls: play/pause, a speed selector, and a scrubber.
6. **Shared cursor:** one current time `t` drives everything. A vertical line moves across both Gantts. Clicking or dragging on either Gantt, or on the scrubber, sets `t`.
7. **Cross-links:** clicking a Gantt bar selects its block, which scrolls to and highlights it in the compiled view. Clicking a block in the compiled view highlights its bars.
8. **Source:** the workflow functions, batch and workcell, all syntax-highlighted.
9. **Compiled output:** per workflow, the list of blocks with their kind, label, deps, resource set, and instructions. Variables are shown along with each run's bindings.
10. **Alarm and deadlock:**
    - An alarm or deadlock time gets a red marker on both Gantts and the scrubber.
    - A deadlock panel lists each waiting block and its reason, and highlights those blocks.
    - An alarm panel shows its message.
    - A `no_solution` run shows a message instead of charts.

- [ ] **Step 1: Write failing tests** in `tests/test_report.py`:

```python
@pytest.fixture
def session(workcell_small, pcr_specs):
    return run_session(workcell_small, pcr_specs(2), ["greedy", "greedy_hotel", "cpsat"], time_limit=10,
                       workcell_source="instruments: []", batch_source="def batch(): ...")

def data_of(html):
    m = re.search(r'<script id="workcell-data" type="application/json">(.*?)</script>', html, re.S)
    return json.loads(m.group(1).replace("<\\/", "</"))

def test_report_data_contract(session):
    d = data_of(build_report(session))
    assert [r["mode"] for r in d["runs"]] == ["greedy:round_robin", "greedy_hotel:round_robin", "cpsat:makespan"]
    assert d["runs"][0]["deadlock"]["time"] == 7 and d["runs"][0]["deadlock"]["waits"]
    assert any(b["inserted"] for b in d["runs"][1]["blocks"])
    assert len(d["compiled"][0]["blocks"]) == 16
    assert "highlight" in d["sources"]["workflows"]

def test_self_contained(session):
    html = build_report(session)
    assert not re.search(r'(src|href)\s*=\s*["\']https?://', html)

def test_alarm_and_no_solution_render(workcell_small, pcr_specs):
    wc = dataclasses.replace(workcell_small, failures=[FailureSpec(op="scan", plate="S0002", kind="barcode_mismatch")])
    sess = run_session(wc, pcr_specs(2), ["greedy_hotel"], time_limit=5)
    empty = Plan("cpsat:makespan", {}, {}, {}, "no_solution"); tr = execute(empty, sess.batch)
    sess.runs.append(RunResult("cpsat:makespan", empty, tr, compute_stats(empty, tr, sess.batch), []))
    d = data_of(build_report(sess))
    assert d["runs"][0]["alarm"]["block"].startswith("pcr-1")
    assert d["runs"][1]["status"] == "no_solution" and d["runs"][1]["blocks"] == []
```

- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_report.py -v` and confirm it fails.
- [ ] **Step 3: Implement `build.py`** to produce the data and inline the template.
- [ ] **Step 4: Implement `template.html`, `report.css`, and `report.js`** covering sections 1–10.
- [ ] **Step 5: Run** the tests and confirm they pass.
- [ ] **Step 6: Visual check.** Write a report with `write_report(run_session(...10 PCR, modes greedy/greedy_hotel/cpsat...), "out/check.html")`. Open it with the Playwright browser tools, then:
  - Confirm there are no console errors.
  - Click play and confirm the cursor moves on both Gantts in sync with the plates.
  - Click a Gantt bar and confirm its compiled block highlights.
  - Switch to the greedy tab and confirm the deadlock panel and marker are shown.
  - Take a screenshot.

  Fix anything broken.
- [ ] **Step 7: Commit** with `feat: HTML report`.

---

### Task 12: CLI, examples, end-to-end

**Files:**
- Create: `workcell/cli.py`, `workcell/__main__.py`, `examples/workcell.yaml`, `examples/pcr_batch.py`, `tests/test_cli.py`
- Modify: `README.md` (create: what this is, plus a quickstart command)

**Interfaces:**
- Consumes: Task 10 `run_files`, Task 11 `write_report`.
- Produces:
  - **CLI `run`:** `python -m workcell run WORKCELL BATCH [--mode M]... [--time-limit S=30] [--out PATH=out/report.html]`. The default modes are `greedy greedy_hotel cpsat:makespan cpsat:sum_completion`. It prints a stats table with one row per mode (mode, status, makespan, completed/total, mean latency, solve seconds), followed by `report: <path>`. The exit code is 0 even when a mode deadlocks or alarms, since those are results. It's 2 on a `ConfigError` or `CompileError`, with the message printed to stderr.
  - **`examples/workcell.yaml`:** the `SMALL` layout from Task 1, written as YAML with comments.
  - **`examples/pcr_batch.py`:** imports `pcr` from `workcell.library`, and defines `batch()` returning 10 workflows with `primer=f"P{i % 2 + 1:04d}"`.

- [ ] **Step 1: Write failing tests** in `tests/test_cli.py`:

```python
def test_cli_end_to_end(tmp_path):
    out = tmp_path / "r.html"
    r = subprocess.run([sys.executable, "-m", "workcell", "run", "examples/workcell.yaml", "examples/pcr_batch.py",
                        "--mode", "greedy", "--mode", "greedy_hotel", "--mode", "cpsat", "--time-limit", "20",
                        "--out", str(out)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "deadlock" in r.stdout and "complete" in r.stdout and out.exists()

def test_cli_compile_error(tmp_path):
    bad = tmp_path / "b.py"
    bad.write_text("from workcell.library import pcr\ndef batch():\n    return [pcr(sample='S9999', primer='P0001')]\n")
    r = subprocess.run([sys.executable, "-m", "workcell", "run", "examples/workcell.yaml", str(bad)],
                       capture_output=True, text=True)
    assert r.returncode == 2 and "S9999" in r.stderr
```

- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_cli.py -v` and confirm it fails.
- [ ] **Step 3: Implement** the CLI, the examples, and the README.
- [ ] **Step 4: Run** the tests and confirm they pass. Then run `.venv/bin/python -m workcell run examples/workcell.yaml examples/pcr_batch.py --out out/pcr10.html`. Check that:
  - greedy deadlocks
  - greedy_hotel completes
  - both CP-SAT modes are optimal or feasible
  - each CP-SAT makespan is no worse than greedy_hotel's
- [ ] **Step 5: Commit** with `feat: CLI and PCR example`.

---

### Task 13: Scale scenario

**Files:**
- Create: `workcell/scenarios.py`, `tests/test_scenarios.py`
- Modify: `workcell/cli.py` to add the `scale` subcommand

**Interfaces:**
- Produces:
  - `scale_workcell(stampers: int, thermocyclers: int, scanners: int, samples: int, primers: int, empties: int) -> Workcell`. It has 1 arm, 1 storage with `slots = samples + primers + empties`, 1 hotel with 500 slots, and ids `stampN`, `tcN`, `scanN` starting at 1. Durations are `default: 1, check_status: 0`.
  - `scale_specs(workflows: int, primers: int) -> list[WorkflowSpec]`: PCR workflows, workflow `i` using sample `S{i+1:04d}` and primer `P{i % primers + 1:04d}`.
  - **CLI `scale`:** `python -m workcell scale --workflows N [--stampers 4] [--thermocyclers 16] [--scanners 2] [--primers 8] [--mode M]... [--time-limit S=60] [--out PATH=out/scale.html]`. It uses `samples=N`, `empties=N`, and the same modes and output as `run`. Batch source is shown as the generating command line.

- [ ] **Step 1: Write a failing test** in `tests/test_scenarios.py`:

```python
def test_scale_compiles_and_hotel_completes():
    wc = scale_workcell(stampers=2, thermocyclers=4, scanners=2, samples=20, primers=4, empties=20)
    b = compile_batch(wc, scale_specs(20, primers=4))
    assert b.workflows[0].variables.keys() >= {"?stamp", "?tc0"}
    p = make_plan(b, "greedy_hotel"); assert p.status == "complete" and validate_plan(p, b) == []
    assert execute(p, b).alarm is None
```

- [ ] **Step 2: Run** the test and confirm it fails.
- [ ] **Step 3: Implement** `scenarios.py` and the `scale` subcommand.
- [ ] **Step 4: Run** the test and confirm it passes. Then run the full suite with `.venv/bin/pytest -v`.
- [ ] **Step 5: Experiment.** Run `python -m workcell scale --workflows 100 --time-limit 60 --out out/scale100.html`. For each mode, record the status, makespan, and solve time in `README.md` under "Scale notes". This is observation, so whatever happens is a valid result (CP-SAT finding only a feasible plan, or no solution at all).
- [ ] **Step 6: Commit** with `feat: scale scenario and notes`.
