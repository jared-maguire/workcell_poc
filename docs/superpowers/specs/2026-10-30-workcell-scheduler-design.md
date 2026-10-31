# Workcell Playground — Design

Date: 2026-10-30
Source: `task_description.md` plus design conversation with Jared.

Items marked **(builder's call)** or **(Claude's call)** were not decided by Jared; everything else was.

## 1. Intent

A workcell is a set of lab instruments (thermocyclers, plate storage, head stampers, barcode scanners, hotels) served by a robotic arm, running many molecular-biology workflows at once over shared instruments and shared plates. This project is a **simulation playground** for exploring how its control software could work:

- a language for writing workflows,
- a compiler that turns workflows into instrument instructions grouped into synchronized blocks,
- planners/schedulers that order those blocks across many parallel workflows without conflicts, while minimizing time,
- a simulator that executes the plan against stateful fake instruments,
- a rich HTML report that makes the result visible.

The planner and scheduler are the part Jared most wants to explore. Eventually (far future) the real thing runs on real hardware in a real lab, so the concepts should map onto reality, but nothing here talks to hardware.

**Success:** run 10 PCR workflows in parallel, in more than one scheduling mode (greedy + hotel and CP-SAT must complete; pure greedy is expected to deadlock and show it), and open a report showing Gantt charts, an animation, stats, the workflow source, and the compiled output.

## 2. Goals, non-goals, anti-goals

**Goals**
- Workcell definition file: instruments, their capacities, plate inventory with barcodes.
- Python-embedded workflow language; workflows are DAGs.
- Compiler: workflow → DAG of synchronized blocks, each listing its instructions and required resources. Compiler inserts arm moves, door open/close, status checks, barcode scans, plate retrieval/storage.
- Scheduling modes behind one interface: **greedy** (baseline, = "grab semaphores as you go"; may deadlock, and that's a valid result), **greedy + hotel** (parks plates in the hotel to avoid deadlock), and **optimized** (OR-Tools CP-SAT) with selectable objective.
- Discrete-event simulator with stateful instruments that reject illegal instructions; executor enforces semaphores at runtime.
- Failure model: a failure pauses the system and raises an alarm (in simulation: logged, run halts, report shows where).
- Self-contained HTML report (section 9).
- Scales as an experiment toward ~100 instruments, thousands of containers, 10–100 concurrent workflows. Finding where each planner breaks down is part of the point.

**Non-goals (for now)**
- Real hardware, drivers, operator UI, persistence, networking.
- Realistic durations: every instruction takes 1 minute for now. Durations live in a table so real timing can be folded in later.
- Duration uncertainty / replanning (design should not preclude it).
- Multiple arms / arm reach zones (config format should allow adding later).
- Hotel capacity limits ("we have hotel space, don't worry about it").
- Hotel handoffs between workflows (passing a shared plate between workflows via the hotel without storing/retrieving) — noted as a future optimization.
- Volume tracking / plate depletion.
- Automatic recovery from failures.

**Anti-goals** (would be failures even if it "works")
- A simulator so idealized it hides bugs: simulated instruments must track state (door open, plates held, busy) and reject illegal instructions; the executor must actually acquire/release locks and refuse conflicting blocks.
- Planner coupled to executor or report: swapping planners must not touch the simulator or report.
- Semaphore calls baked into the compiled instruction stream (that would take ordering away from the planner).

## 3. Concepts and vocabulary

- **Instrument**: has an id, a type, a set of operations, and a number of **nests** (positions that can hold a plate). Thermocycler: 1 nest, has a door. Barcode scanner: 1 nest. Head stamper: 2 nests (source, destination). Storage: many slots. Hotel: many slots (short-term parking). Arm: holds at most 1 plate, moves plates between nests.
- **Container / plate**: identified by barcode. Has a type (e.g. `sample`, `primer`, `empty`), free-text contents, and an initial location (usually a storage slot).
- **Instruction**: one operation on one instrument, e.g. `tc1.open_door()`, `arm.move(storage1 → scanner1)`, `scanner1.scan(expect="S0001")`.
- **Synchronized block**: a short sequence of instructions that must run without another workflow interleaving, plus its **resource set** (instruments, nests, plates). Executed atomically with respect to locks: all locks acquired at once (all-or-nothing), released at block end.
- **Block DAG**: a compiled workflow — blocks plus dependency edges.
- **Plan**: start time for every block, plus a binding of every abstract resource (e.g. "any thermocycler") to a concrete instrument.
- **Trace**: what the executor actually did — timestamped instruction events, lock events, instrument state changes, failures.

### Who does what with semaphores (decided)
- The **compiler** decides block boundaries and annotates each block with its resource set. It does not emit acquire/release instructions.
- The **planner** uses those resource sets to decide order and timing.
- The **executor** acquires a block's locks all-or-nothing before running it and releases them after. Locks are a runtime safety net, not the scheduling mechanism (except in greedy mode, where "start whatever can acquire its locks" *is* the policy).

### Plate holds (decided)
Two kinds:
- **Owned** plates are exclusive to one workflow for its lifetime (e.g. the sample plate and the PCR plate). No other workflow may touch them, even while they sit idle.
- **Borrowed/shared** plates (e.g. a primer plate used by many PCR workflows) are held only while a workflow is using them. *(Claude's call, refining "short hold":)* since a borrowed plate has to be fetched, used, and returned, the hold spans from its fetch block to its return block within the borrowing workflow, not just the stamp block. Between workflows it is free and goes back to storage.

The batch is invalid if two workflows own the same plate.

## 4. Workcell definition (YAML) — Claude's call on format

```yaml
instruments:
  - {id: arm1,     type: arm}
  - {id: store1,   type: storage, slots: 2000}
  - {id: hotel1,   type: hotel,   slots: 50}
  - {id: scan1,    type: barcode_scanner}
  - {id: stamp1,   type: head_stamper}
  - {id: tc1,      type: thermocycler}
  - {id: tc2,      type: thermocycler}

durations:            # minutes; default applies to every instruction not listed
  default: 1
  # thermocycler.run_program: 1   # override later

plates:
  - {barcode: S0001, type: sample, contents: "96 human DNA samples", location: store1}
  - {barcode: P0001, type: primer, contents: "PCR primers in master mix", location: store1}
  - {barcode: E0001, type: empty, location: store1}
  # ... or a generator form for bulk inventory:
  - {generate: empty, prefix: E, count: 500, location: store1}

failures:             # optional, simulation only
  - {instruction: scan, plate: S0003, kind: barcode_mismatch}
```

Single arm reaches everything in v1. Format should leave room for `reach:` lists per arm later.

## 5. Workflow language — Python-embedded (Claude's call; Jared is happy with Python)

Workflows are plain Python functions that build a DAG through a small API. Retrieval, scanning, arm moves, doors and storage returns are implicit — the compiler adds them.

```python
from workcell.dsl import workflow, Workflow

PCR_PROGRAM = [(95, 2.0), *[(95, 0.5), (58, 0.5), (72, 1.0)] * 30, (72, 5.0)]  # (°C, min)

@workflow
def pcr(w: Workflow, sample: str, primer: str):
    s   = w.own(sample)                 # specific plate, exclusive to this workflow
    p   = w.borrow(primer)              # shared plate
    out = w.own(w.new_plate("empty"))   # any empty plate from the pool; becomes the PCR plate

    a = w.stamp(src=s, dst=out)
    b = w.stamp(src=p, dst=out, after=a)
    c = w.thermocycle(out, program=PCR_PROGRAM, after=b)
    w.store(s, after=a)
    w.store(p, after=b)
    w.store(out, after=c)
```

A batch file lists workflow instances:

```python
def batch():
    return [pcr(sample=f"S{i:04d}", primer=f"P{i % 2 + 1:04d}") for i in range(10)]
```

Dependency rules *(Claude's call)*: explicit `after=` edges, plus an implicit edge between consecutive operations on the same plate within one workflow, in program order. (The `after=` arguments above are then partly redundant, which is fine.) The DAG must be acyclic; the compiler rejects cycles.

`new_plate("empty")` is resolved to a concrete barcode at batch/compile time from inventory, so plans and reports are fully concrete *(Claude's call)*.

## 6. Compiler

Input: workcell definition + batch. Output: one block DAG per workflow instance, with concrete barcodes and **abstract or concrete** instrument resources (e.g. `thermocycler:*` to be bound by the planner; `stamp1` if only one exists — the compiler may still emit abstract types and let the planner bind).

**Block-boundary rule** *(Claude's call)*: a block starts and ends with every plate it touches resting in a nest. Plates in transit (on the arm, presented on a storage tray) never cross a block boundary. Splitting at resting points maximizes parallelism (e.g. the scanner is free while the arm does something else).

**Status checks**: each block begins with `check_status` on every instrument it uses, verifying the state is legal for what follows (Jared's intent for "check status").

**Barcode scans** *(Claude's call)*: once after every retrieval from storage. A mismatch is a failure. Making "scan before each stamp" a config switch is a nice-to-have.

**Plate locations between operations** *(Claude's call)*: the compiler routes plates directly from one operation's instrument to the next. While a plate waits there, it occupies that nest (and can block the instrument — e.g. a finished PCR plate sitting in the stamper because both thermocyclers are busy). That blocking is real and should be visible in the report.

**Planner-inserted hotel parking (decided)**: a planner may split a direct transport into *park* (move plate → hotel slot) and *unpark* (hotel → destination) blocks. The IR must support planner-inserted blocks; a Plan therefore carries its own final block list (compiled blocks plus any inserted ones), not just start times. Inserted blocks follow the same rules (resource sets, lock all-or-nothing, status checks) and are shown in the report like any other block, marked as planner-inserted.

Sketch of compiled PCR (instruction-level, before planning):

```
B1 fetch S0001 → scan1     [store1.check, arm1.check, scan1.check, store1.retrieve(S0001), arm1.move(store1→scan1)]
                           needs: arm1, store1, scan1.nest, plate S0001
B2 scan S0001              [scan1.check, scan1.scan(expect=S0001)]                    needs: scan1, S0001
B3 move S0001 → stamp.src  [arm1.check, stamp1.check, arm1.move(scan1→stamp1.src)]   needs: arm1, scan1, stamp1.src, S0001
... same for E00xx → stamp.dst, then:
B7 stamp S0001 → E00xx     [stamp1.check, stamp1.stamp(src, dst)]                     needs: stamp1, S0001, E00xx
B8 return S0001            [arm1.move(stamp1.src→store1), store1.store(S0001)]       needs: arm1, stamp1.src, store1, S0001
... fetch/scan/move P0001 into stamp.src, stamp, return P0001 ...
Bn move E00xx → tc         [tc.check, tc.open_door, arm1.move(stamp1.dst→tc), tc.close_door]  needs: arm1, stamp1.dst, tc:*, E00xx
Bn+1 run program           [tc.check, tc.run_program(PCR_PROGRAM)]                    needs: tc (same binding), E00xx
Bn+2 return E00xx          [tc.open_door, arm1.move(tc→store1), tc.close_door, store1.store(E00xx)]
```

Bindings must be consistent: the thermocycler a plate is loaded into is the one that runs it and unloads it.

**Occupancy**: in addition to per-block locks, each plate resting in a nest occupies that nest from the end of the block that put it there to the start of the block that removes it. Planners must respect nest capacities; the executor checks them.

## 7. Planners

One interface: `plan(compiled_batch, workcell, options) -> Plan`. A Plan is pure data (block start times + resource bindings + metadata such as mode, objective, solve time, optimality status), so the report and executor don't care which planner made it.

**Greedy** (baseline; = semaphores-only from the original doc)
- Event-driven list scheduling: at each time step, start any ready block whose dependencies are done and whose resources (locks, destination nest capacity, plate holds) are all free.
- Tie-break priority is a parameter *(builder's call on the set; at least FIFO by workflow index)*.
- Must detect deadlock (work remains, nothing running, nothing startable) and report it rather than hang.
- **Known and accepted**: pure greedy deadlocks on the 10-PCR example (A's sample in the stamper source nest; B's sample on the scanner waiting for that nest; A's empty plate waiting for the scanner). **A deadlock is a valid, first-class result** (decided): the plan/trace up to the stall is kept, and the report shows the stalled run in its mode tab and stats (e.g. "deadlocked at t=7, 2/10 complete") with the deadlocked wait cycle highlighted.

**Greedy + hotel** (decided: ship both greedy variants)
- Same as greedy, but when a plate's next nest is unavailable, park it in the hotel (planner-inserted park block, see §6) so the nest it's sitting in frees up; unpark when the destination frees. Hotel capacity is effectively unlimited, so this mode should always finish. Exact parking trigger/policy is the builder's call, as long as the 10-PCR example completes.
- Still detects and reports deadlock, in case some scenario defeats it.

**Optimized** (OR-Tools CP-SAT)
- Interval variable per block; precedence from the DAGs; NoOverlap per unary instrument (arm, scanner, stamper, each thermocycler); capacity constraints for nests/occupancy; optional intervals for choosing among equivalent instruments; plate holds as intervals that must not overlap per plate.
- Objectives (selectable): **makespan** (finish the batch soonest) and **sum of workflow completion times** (finish each workflow soonest). Both were wanted by Jared ("both have a place").
- Time limit parameter; report optimal vs. best-found. Warm-start from the greedy plan is a nice-to-have.
- v1 CP-SAT uses direct transports only (it avoids deadlock by ordering). Letting CP-SAT choose optional hotel parking is a future mode *(Claude's call)*.
- Expected: optimal at 10 workflows, time-limited good-enough at 100. Measuring that is part of the experiment.

New modes should be easy to add (e.g. hotel parking, priority-based heuristics).

## 8. Simulator and executor

- **Discrete-event** simulated clock — runs as fast as the CPU allows, no real-time waiting *(Claude's call)*.
- **Simulated instruments**: each a small state machine (door open/closed, plates in which nests, busy/idle, storage contents). They validate every instruction and fail on illegal ones (e.g. moving a plate into a closed thermocycler, retrieving a plate that isn't there, scanning the wrong barcode). `check_status` returns state and fails if it's illegal for the block.
- **Executor**: runs the Plan. For each block at its planned start: acquire all locks all-or-nothing (if not available, wait — and record that, since with fixed 1-minute durations it should never happen and signals a planner bug), execute instructions in order against simulated instruments, release locks.
- **Failures** (injected via config, or from illegal instructions): pause the system, raise an alarm (log entry with block/instruction/instrument), stop the run. The trace and report still render up to that point and highlight the failure.
- Future: random durations; the executor then keeps the planned order and shifts start times *(Claude's guess, not needed now)*.

Output: a **Trace** (JSON-serializable) of instruction events, lock acquire/release, plate movements, instrument states, and any alarm.

## 9. Report

A single self-contained HTML file per run (opens offline, no CDN), generated from workcell + batch source + compiled IR + plan(s) + trace(s). Jared approved all four mockup renditions plus additions:

1. **Summary stats**: makespan, per-workflow latency (mean/worst), instrument utilization bars, time plates/instruments spent blocked/waiting, solver status and time. When several modes are run, shown side by side for comparison.
2. **Gantt by instrument**: one row per instrument (arm, storage, scanner, stamper, each thermocycler, hotel), bars colored by workflow, tooltips with block/instruction detail.
3. **Gantt by workflow**: one row per workflow, bars colored by instrument type, gray spans for waiting.
4. **Animated floor plan**: instruments as boxes, plates (labeled by barcode, colored by owning workflow; shared plates distinct) moving between them; busy instruments highlighted; play/pause/scrub.
5. **Shared timeline**: a vertical cursor line advances across both Gantt charts in sync with the animation; clicking/dragging on a Gantt or scrubber seeks everything.
6. **Workflow source**: the user's workflow code, syntax highlighted.
7. **Compiled output**: per workflow, the block DAG with each block's instructions and resource set. Cross-linking *(Claude's guess, approved in playback)*: clicking a block highlights its bars; clicking a bar jumps to its block.
8. Failure or deadlock (if any) marked on the timeline and in the stats; for deadlock, the wait cycle (who holds what, who waits for what) is shown.
9. Planner-inserted blocks (hotel park/unpark) visually distinguished.

Multiple modes in one report: a mode switcher (tabs) for the Gantt/animation views, plus the side-by-side stats.

## 10. Interface

CLI *(Claude's call)*:

```
python -m workcell run examples/workcell.yaml examples/pcr_batch.py \
    --mode greedy --mode greedy_hotel --mode cpsat:makespan --mode cpsat:sum_completion \
    --time-limit 30 --out out/pcr10.html
```

Also usable as a library (compile / plan / simulate / report as separate functions) so experiments can be scripted.

## 11. Example scenarios to ship

- `examples/workcell.yaml`: 1 arm, 1 storage, 1 hotel, 1 scanner, 1 head stamper, 2 thermocyclers, inventory for the batch.
- `examples/pcr_batch.py`: 10 PCR workflows sharing 2 primer plates (to exercise shared-plate contention).
- A scale scenario generator: N workflows (up to 100) on a larger workcell (more stampers/thermocyclers, thousands of plates) to stress the planners. With one arm this will be arm-bound; that's acceptable and informative.

## 12. Constraints

- Python (local Python is 3.14; `uv` is not installed). Dependencies expected: `ortools`, `pyyaml`, `pygments` (for source highlighting), `pytest`. **Risk:** confirm OR-Tools ships wheels for 3.14; if not, use a 3.12/3.13 venv.
- Report is offline/self-contained; vanilla JS + SVG, no external CDNs.
- Tests with pytest. Key invariants to test: no resource double-booking in any plan; DAG precedence respected; nest capacity respected; consistent instrument bindings; simulator rejects illegal instructions; executor of a valid plan produces no lock waits and no failures; injected failure halts the run.

## 13. Left to the builder

- Internal module layout and class design (a reasonable split: `config`, `dsl`, `ir`, `compiler`, `planners/`, `sim/`, `report/`, `cli`).
- Exact IR/JSON schemas for blocks, plans, traces.
- Greedy priority rules beyond FIFO.
- CP-SAT modeling details (time unit = integer minutes is fine for now).
- Report visual design details (the brainstorm mockups are the reference: `.superpowers/brainstorm/*/content/renditions.html`).
