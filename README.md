# Workcell scheduler playground

A simulation playground for scheduling plate-handling workflows (such as PCR) on a lab workcell with one robot arm, storage, a hotel, a scanner, a head stamper and thermocyclers. It compiles a batch of workflows, plans them with several strategies (including the just-in-time dispatcher that runs live mode), simulates each plan, and writes a single self-contained HTML report comparing the results.

## Example report

[`docs/reports/pcr10.html`](docs/reports/pcr10.html) is a pre-generated report for 10 parallel PCR workflows on the example workcell, comparing the default modes. Download it and open it in a browser (it is fully self-contained and works offline). In brief:

| mode | result | makespan |
|---|---|---|
| greedy | deadlock at t=7, 0/10 done | – |
| greedy_hotel | done | 257 min |
| jit | done (0.1 s) | 236 min |
| cpsat:makespan | done (not proven best within the 30 s limit) | 204 min |
| cpsat:sum_completion | done (not proven best within the 30 s limit) | 209 min |

With these unit step times the arm is the bottleneck and CP-SAT's lookahead pays off; with realistic times (live mode below) the dispatcher matches or beats it.

The report opens on the best run (shortest makespan among runs where every workflow finished).

## Live mode: just-in-time dispatch

`live` runs a batch the way a lab would. Each step has a realistic time in seconds (set in the workflow with `@workflow(times=...)` and `seconds=`), and actual times vary randomly (seeded; same seed, same run).

A just-in-time dispatcher reacts to steps actually finishing:
- It starts whatever can start: workflows already under way first, then the one with the most work left.
- It sends a plate to the nearest parking spot when its next stop is full.
- It never looks at how long a running step will really take.

The forecast is the same dispatcher run on the planned times. Dispatching a whole run takes well under a second.

```
.venv/bin/python -m workcell live examples/workcell.yaml examples/pcr_live.py --seed 0 --out out/live.html
```
```
seed: 0
forecast makespan: 6:02:24
actual makespan:   7:09:05
idle hold: limit 300s, parks 27, violations 2 (worst 465s)
completed: 10/10
dispatch: 0.30 s
```

**Idle hold.** No instrument may sit idle holding a plate for more than 5 minutes (`--max-idle-hold SECONDS`, `0` disables). The dispatcher does what a lab tech would: when a plate has waited 4 minutes on an instrument and its next move hasn't started, it goes to the nearest hotel (deck positions: only the arm move) or, if the hotels are full, to storage (a machine: 60 s to store or retrieve). Waits that still go over are counted in the report. The rule doesn't cover a plate waiting between two uses of the same instrument.

Example report (opens in the browser): https://jared-maguire.github.io/workcell_poc/docs/reports/pcr10_live.html. Options: `--seed`, `--max-idle-hold SECONDS`, `--noise`, `--delay-prob`.

**Why not CP-SAT?** Until 2026-10-31 live mode planned with CP-SAT and re-planned on delays. That needed a 10 s grid, a 4 h rolling window and a persistent solver process to run in about a minute. On the same seeds the dispatcher did as well or better:

| | seed 0 | seed 1 | seed 2 | per run |
|---|---|---|---|---|
| 10 PCRs, CP-SAT re-planning | 7:57:50 | 7:18:50 | 7:21:24 | 32–54 s |
| 10 PCRs, dispatch | 7:09:05 | 6:04:40 | 5:53:18 | 0.3 s |
| 3 library preps, CP-SAT re-planning | 11:55:18 | 12:01:20 | 12:03:40 | 82–144 s |
| 3 library preps, dispatch | 12:08:58 | 11:15:07 | 11:21:10 | 0.2 s |

Without noise, dispatch is within 1.4% of CP-SAT's proven optimum on the library prep batch (40,418 s). Design: `docs/superpowers/specs/2026-10-31-jit-dispatch-design.md`.


## Demo: library prep + hybrid capture, three batches

`examples/libprep_capture.py` takes a 96-sample gDNA plate through Illumina DNA Prep library prep, IDT xGen hybridization capture, and consolidation into a single well for the sequencer: 30 steps with real durations (tagmentation, bead cleanups, indexing PCR, pooling 8 libraries per capture, dry-down, 4 h hybridization, streptavidin capture, washes, post-capture PCR, QC reads, consolidation). `examples/libprep_workcell.yaml` adds a liquid handler with on-deck magnet (×2), heated shakers (×2), sealer, peeler, centrifuge, plate reader and vacuum concentrator to the arm, storage, hotel, scanner, stamper and three thermocyclers. Workflows use `w.step(instrument_type, op, plate[, dst], seconds=...)` for these instruments. Design: `docs/superpowers/specs/2026-10-31-libprep-capture-design.md`.

```
.venv/bin/python -m workcell live examples/libprep_workcell.yaml examples/libprep_capture.py --seed 0
```
```
forecast makespan: 11:22:56
actual makespan:   12:08:58
idle hold: limit 300s, parks 11, violations 0 (worst 0s)
completed: 3/3
dispatch: 0.16 s
```

One batch needs about 10.5 h of instrument time; three in parallel finish in about 12 h. Report: https://jared-maguire.github.io/workcell_poc/docs/reports/libprep3_live.html.

### Two arms

Arms can each reach their own instruments and pass plates through handoff spots (`reach:` lists and `type: handoff` in the workcell file; only handoffs may be shared). A move between zones is relayed through a handoff in one step. The dispatcher may use any instrument and, given a choice, takes the free one with the shortest route; parked plates go to their own zone's hotel. greedy and greedy_hotel support one arm only. `examples/libprep_2arm.yaml` splits the demo into a library-prep zone (arm1) and a capture zone (arm2) with one handoff spot; sealer, peeler and centrifuge exist once (plates cross over), plate readers once per zone. Design: `docs/superpowers/specs/2026-10-31-multi-arm-design.md`.

```
.venv/bin/python -m workcell live examples/libprep_2arm.yaml examples/libprep_capture.py --seed 0
```
```
forecast makespan: 11:25:01
actual makespan:   12:12:01
idle hold: limit 300s, parks 12, violations 0 (worst 0s)
completed: 3/3
dispatch: 0.17 s
```

At three batches the thermocyclers (4 h hybridization) set the pace, so two arms finish about as fast as one; the second arm is for higher volume. Each demo run takes about 2 seconds. Report: `docs/reports/libprep3_2arm_live.html`.

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
- `jit`: the just-in-time dispatcher live mode uses (planned times, no idle limit). Workflows under way first, then most remaining work; parks a plate when its next stop is full. Any number of arms.
- `cpsat` / `cpsat:makespan`: CP-SAT plan minimizing total makespan.
- `cpsat:sum_completion`: CP-SAT plan minimizing the sum of workflow completion times.

## Project layout

- `workcell/config.py`: workcell YAML loading
- `workcell/dsl.py`, `workcell/library.py`: workflow DSL and PCR library
- `workcell/compiler.py`, `workcell/ir.py`: batch compilation to blocks
- `workcell/planners/`: greedy, just-in-time dispatch and CP-SAT planners
- `workcell/live/`: live mode (noisy step times, forecast, as-run validation)
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
| jit | complete | 2100 | 100/100 | 6.43 |
| cpsat:makespan | feasible | 2002 | 100/100 | 65.91 |
| cpsat:sum_completion | feasible | 2003 | 100/100 | 66.13 |

Pure greedy deadlocked almost immediately (t=17, nothing finished). greedy_hotel completed all 100 workflows but its makespan (2288) is about 14% worse than CP-SAT's; most of that is round-robin order (greedy_hotel:fifo gets 2003). jit gets 2100, 5% worse than CP-SAT, in 6 s (measured 2026-10-31). CP-SAT found only feasible (not proven optimal) plans within the 60 s limit, with makespan 2002 and 2003; the two objectives ended up nearly identical, and each solve ran about 6 s past the limit.
