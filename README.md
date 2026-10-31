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
