# Workcell scheduler playground

A simulation playground for scheduling plate-handling workflows (such as PCR) on a lab workcell with one robot arm, storage, a hotel, a scanner, a head stamper and thermocyclers. It compiles a batch of workflows, plans them with several strategies, simulates each plan, and writes a single self-contained HTML report comparing the results.

## Quickstart

```
python -m venv .venv
.venv/bin/pip install -e .
.venv/bin/python -m workcell run examples/workcell.yaml examples/pcr_batch.py
```

Then open `out/report.html` in a browser. Options: `--mode M` (repeatable), `--time-limit S` (CP-SAT seconds, default 30), `--out PATH`.

## Modes

- `greedy` (`greedy:round_robin`, `greedy:fifo`): dispatches blocks as resources free up, with no parking. It is expected to deadlock on the PCR batch; that demonstrates why planning matters.
- `greedy_hotel` (`greedy_hotel:round_robin`, `greedy_hotel:fifo`): greedy that can park plates in the hotel to avoid deadlock.
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
