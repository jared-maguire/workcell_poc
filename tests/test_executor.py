import dataclasses

from helpers import VALID, sequential

from workcell.compiler import compile_batch
from workcell.config import FailureSpec
from workcell.library import pcr
from workcell.plan import Plan
from workcell.sim.executor import execute


def test_valid_plan_runs_clean(compiled_small):
    b = compiled_small(1); p = sequential(b, VALID); t = execute(p, b)
    assert t.alarm is None and t.lock_waits == []
    assert t.end_time == p.makespan
    assert set(t.final_locations.values()) == {"store1"}
    assert t.spans["pcr-0/00"] == (0, 2)


def test_capacity_violation_raises_alarm(compiled_small):
    b = compiled_small(1)
    order = ["00", "01", "02", "03", "04", "05", "06", "07", "08", "09", "13", "10", "14", "11", "12", "15"]
    t = execute(sequential(b, order), b)
    assert t.alarm is not None and "stamp1.src" in t.alarm.message and t.alarm.block == "pcr-0/09"


def test_injected_mismatch_halts(compiled_small, workcell_small):
    wc = dataclasses.replace(workcell_small, failures=[FailureSpec(op="scan", plate="S0001", kind="barcode_mismatch")])
    b = compile_batch(wc, [pcr(sample="S0001", primer="P0001")])
    t = execute(sequential(b, VALID), b)
    assert t.alarm.block == "pcr-0/01" and all(e.time <= t.alarm.time for e in t.events)
    assert t.end_time == t.alarm.time
    assert t.spans["pcr-0/01"][1] == t.alarm.time
    assert "pcr-0/02" not in t.spans


def test_overlapping_plan_waits_for_locks(compiled_small):
    b = compiled_small(1); p = sequential(b, VALID); p.start["pcr-0/03"] = 0
    t = execute(p, b)
    assert any(blk == "pcr-0/03" and mins > 0 for blk, mins in t.lock_waits)


def test_partial_plan(compiled_small):
    b = compiled_small(1)
    t = execute(sequential(b, VALID[:5], status="deadlock"), b)
    assert t.alarm is None and len(t.spans) == 5


def test_empty_plan(compiled_small):
    b = compiled_small(1)
    p = Plan(mode="test", blocks={}, start={}, bindings={}, status="deadlock")
    t = execute(p, b)
    assert t.end_time == 0 and t.events == [] and t.alarm is None
    assert t.final_locations == t.initial_locations


def test_moves_recorded(compiled_small):
    b = compiled_small(1); t = execute(sequential(b, VALID), b)
    assert t.moves and all(m.src != m.dst for m in t.moves)
    assert [m.time for m in t.moves] == sorted(m.time for m in t.moves)
