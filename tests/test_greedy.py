import pytest

from workcell.planners import make_plan
from workcell.planners.greedy import plan_greedy
from workcell.sim.executor import execute
from workcell.validate import validate_plan


def test_fifo_one_workflow_complete_and_valid(compiled_small):
    b = compiled_small(1); p = plan_greedy(b, priority="fifo")
    assert p.status == "complete" and p.mode == "greedy:fifo"
    assert validate_plan(p, b) == []
    t = execute(p, b); assert t.alarm is None and t.lock_waits == []


def test_fifo_two_workflows_deadlocks(compiled_small):
    # Ruling R6: pure greedy deadlocks on 2 PCRs under fifo too.
    b = compiled_small(2); p = plan_greedy(b, priority="fifo")
    assert p.status == "deadlock" and p.deadlock.time == 16
    assert validate_plan(p, b) == []


def test_round_robin_deadlocks_on_two_pcr(compiled_small):
    b = compiled_small(2); p = plan_greedy(b)          # default round_robin
    assert p.status == "deadlock" and p.deadlock.time == 7
    blocked = {w.block: w.reason for w in p.deadlock.waits}
    assert "stamp1.src" in blocked["pcr-1/02"] and "scan1" in blocked["pcr-0/03"]
    assert validate_plan(p, b) == []
    assert len(execute(p, b).spans) == 5


def test_make_plan_modes(compiled_small):
    b = compiled_small(2)
    assert make_plan(b, "greedy").mode == "greedy:round_robin"
    assert make_plan(b, "greedy:fifo").mode == "greedy:fifo"
    with pytest.raises(ValueError, match="greedy_hotel"):
        make_plan(b, "bogus")
