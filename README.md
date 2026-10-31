# Workcell scheduler playground

A simulation playground for scheduling plate-handling workflows (such as PCR) on a lab workcell with one robot arm, storage, a hotel, a scanner, a head stamper and thermocyclers. It compiles a batch of workflows, plans them with several strategies, simulates each plan, and writes a single self-contained HTML report comparing the results.

## Example report

[`docs/reports/pcr10.html`](docs/reports/pcr10.html) is a pre-generated report for 10 parallel PCR workflows on the example workcell, comparing all four default modes. Download it and open it in a browser (it is fully self-contained and works offline). In brief:

| mode | result | makespan |
|---|---|---|
| greedy | deadlock at t=7, 0/10 done | – |
| greedy_hotel | done | 257 min |
| cpsat:makespan | done (not proven best within the 30 s limit) | 204 min |
| cpsat:sum_completion | done (not proven best within the 30 s limit) | 209 min |

The report opens on the best run (shortest makespan among runs where every workflow finished).

## v2: live re-planning

`live` runs a batch the way a lab would: each step has a realistic time in seconds (set in the workflow with `@workflow(times=...)` and `seconds=`), actual times vary randomly (seeded), and the system re-plans with CP-SAT when a delay would push a later step back by more than the threshold (5 min). A re-plan keeps churn low: it holds the best finish time it can find while changing as little as possible, and it is adopted only if it finishes at least the threshold sooner than just letting everything slide. Same seed, same run.

```
.venv/bin/python -m workcell live examples/workcell.yaml examples/pcr_live.py --seed 0 --out out/live.html
```
```
seed: 0
planned makespan: 5:56:22
actual makespan:  7:50:30
re-plans: 11 (5 adopted, 6 not worth it, 0 failed)
idle hold: limit 300s, parks 18, violations 6 (worst 399s)
completed: 10/10
```

**Idle hold.** No instrument may sit idle holding a plate for more than 5 minutes (`--max-idle-hold SECONDS`, `0` disables). The planner sees long waits coming and parks the plate right away in the hotel (deck positions: only the arm move) or storage (a machine: 60 s to store or retrieve), then brings it back when its next stop is free; short waits stay put. During the run, like a lab tech, the executor moves a plate to the hotel when it has waited almost the limit and its next move still hasn't started (e.g. behind a thermocycler running long). Waits that still go over (mostly by seconds, from step-time noise) are counted in the report.

Example report (opens in the browser): https://jared-maguire.github.io/workcell_poc/docs/reports/pcr10_live.html. Re-plan markers on the timeline: solid = adopted, dashed = not worth it, dotted = failed. Options: `--seed`, `--max-idle-hold SECONDS`, `--replan-threshold SECONDS`, `--time-limit` (initial plan, default 30), `--replan-time-limit` (per re-plan phase, default 2), `--noise`, `--delay-prob`.


## Demo: library prep + hybrid capture, three batches

`examples/libprep_capture.py` takes a 96-sample gDNA plate through Illumina DNA Prep library prep, IDT xGen hybridization capture, and consolidation into a single well for the sequencer: 30 steps with real durations (tagmentation, bead cleanups, indexing PCR, pooling 8 libraries per capture, dry-down, 4 h hybridization, streptavidin capture, washes, post-capture PCR, QC reads, consolidation). `examples/libprep_workcell.yaml` adds a liquid handler with on-deck magnet (×2), heated shakers (×2), sealer, peeler, centrifuge, plate reader and vacuum concentrator to the arm, storage, hotel, scanner, stamper and three thermocyclers. Workflows use `w.step(instrument_type, op, plate[, dst], seconds=...)` for these instruments. Design: `docs/superpowers/specs/2026-10-31-libprep-capture-design.md`.

```
.venv/bin/python -m workcell live examples/libprep_workcell.yaml examples/libprep_capture.py --seed 0 --time-limit 60
```
```
planned makespan: 11:19:41
actual makespan:  12:10:24
re-plans: 15 (11 adopted, 4 not worth it, 0 failed)
idle hold: limit 300s, parks 17, violations 3 (worst 714s)
completed: 3/3
```

One batch needs about 10.5 h of instrument time; three in parallel finish in about 12 h. Report: https://jared-maguire.github.io/workcell_poc/docs/reports/libprep3_live.html.

### Two arms

Arms can each reach their own instruments and pass plates through handoff spots (`reach:` lists and `type: handoff` in the workcell file; only handoffs may be shared). A move between zones is relayed through a handoff in one step; the planner may use any instrument and sees each choice's route. `examples/libprep_2arm.yaml` splits the demo into a library-prep zone (arm1) and a capture zone (arm2) with one handoff spot; sealer, peeler and centrifuge exist once (plates cross over), plate readers once per zone. Design: `docs/superpowers/specs/2026-10-31-multi-arm-design.md`.

```
.venv/bin/python -m workcell live examples/libprep_2arm.yaml examples/libprep_capture.py --seed 0 --time-limit 120
```
```
planned makespan: 11:21:14
actual makespan:  12:13:40
re-plans: 21 (18 adopted, 3 not worth it, 0 failed)
idle hold: limit 300s, parks 16, violations 6 (worst 324s)
completed: 3/3
```

At three batches the thermocyclers (4 h hybridization) set the pace, so two arms finish about as fast as one; the second arm is for higher volume. The two-arm model is harder for CP-SAT, so it gets a longer budget (`--time-limit 120`, ~7 min). Report: `docs/reports/libprep3_2arm_live.html`.

## Quickstart

```
python -m venv .venv
.venv/bin/pip install -e .
.venv/bin/python -m workcell run examples/workcell.yaml examples/pcr_batch.py
```

Then open `out/report.html` in a browser. Options: `--mode M` (repeatable), `--time-limit S` (CP-SAT seconds, default 30), `--out PATH`.

## Modes

- `greedy` (`greedy:round_robin`, `greedy:fifo`): dispatches blocks as resources free up, with no parking. It is expected to deadlock on the PCR batch; that demonstrates why planning matters.
- `greedy_hotel` (`greedy_hotel:round_robin`, `greedy_hotel:fifo`): greedy that can park plates in the hotel to avoid deadlock. It can still deadlock if hotel capacity is exhausted (the deadlock waits then say "hotel full").
- `cpsat` / `cpsat:makespan`: CP-SAT plan minimizing total makespan.
- `cpsat:sum_completion`: CP-SAT plan minimizing the sum of workflow completion times.

## Project layout

- `workcell/config.py`: workcell YAML loading
- `workcell/dsl.py`, `workcell/library.py`: workflow DSL and PCR library
- `workcell/compiler.py`, `workcell/ir.py`: batch compilation to blocks
- `workcell/planners/`: greedy and CP-SAT planners
- `workcell/sim/`: executor and world model
- `workcell/validate.py`, `workcell/stats.py`: plan validation and statistics
- `workcell/report/`: HTML report
- `workcell/run.py`, `workcell/cli.py`: orchestration and command line
- `examples/`: example workcell and PCR batch
- `tests/`: test suite

## Scale notes

Command: `.venv/bin/python -m workcell scale --workflows 100 --time-limit 60 --out out/scale100.html`

Workcell: 1 arm, 1 storage (208 slots), 1 hotel (500 slots), 4 head stampers, 16 thermocyclers, 2 barcode scanners; 100 samples, 8 primers, 100 empties; 100 PCR workflows (sample `S{i+1:04d}`, primer `P{i % 8 + 1:04d}`); durations default 1, check_status 0.

| mode | status | makespan | completed/total | solve s |
|---|---|---|---|---|
| greedy:round_robin | deadlock | 17 | 0/100 | 0.00 |
| greedy_hotel:round_robin | complete | 2288 | 100/100 | 0.00 |
| cpsat:makespan | feasible | 2002 | 100/100 | 65.91 |
| cpsat:sum_completion | feasible | 2003 | 100/100 | 66.13 |

Pure greedy deadlocked almost immediately (t=17, nothing finished). greedy_hotel completed all 100 workflows but its makespan (2288) is about 14% worse than CP-SAT's. CP-SAT found only feasible (not proven optimal) plans within the 60 s limit, with makespan 2002 and 2003; the two objectives ended up nearly identical, and each solve ran about 6 s past the limit.
