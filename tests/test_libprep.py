from pathlib import Path

from workcell.compiler import compile_batch
from workcell.config import load_workcell
from workcell.dsl import load_batch
from workcell.plan import Plan, bind_batch
from workcell.planners.cpsat import serial_hint
from workcell.sim.executor import execute
from workcell.validate import validate_plan

EX = Path(__file__).resolve().parent.parent / "examples"
# instrument-step and thermocycler times, in workflow order (spec table; steps 7-8 merged)
STEPS = [60, 60, 60, 10, 900, 10, 60, 360, 720, 60, 60, 60, 30, 10, 2175, 10, 30, 1800, 300,
         900, 2700, 600, 10, 30, 14430, 10, 600, 2700, 600, 900, 180, 10, 1005, 10, 1200, 300,
         600, 10]


def batch(n):
    return compile_batch(load_workcell(EX / "libprep_workcell.yaml"),
                         load_batch(EX / "libprep_capture.py")[0][:n])


def test_libprep_compiles():
    b = batch(1)
    ops = [blk for blk in b.workflows[0].blocks if blk.kind == "op"]
    assert [o.duration for o in ops] == STEPS
    tc = [o.duration for o in ops if o.instructions[1].op == "run_program"]
    assert tc == [900, 2175, 14430, 1005]


def test_libprep_serial_plan_runs():
    b = batch(1)
    starts, binds = serial_hint(b)
    plan = Plan("serial", bind_batch(b, binds), starts, binds, "feasible")
    assert validate_plan(plan, b) == []
    trace = execute(plan, b)
    assert trace.alarm is None and trace.completed == {blk.id for blk in b.all_blocks()}


def test_libprep_three_batches_compile():
    b = batch(3)
    codes = [set(wf.barcodes.values()) for wf in b.workflows]
    assert all(len(c) == 9 for c in codes) and not (codes[0] & codes[1] or codes[1] & codes[2])
