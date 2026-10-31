import pytest

from conftest import SMALL
from workcell.compiler import compile_batch
from workcell.config import parse_workcell
from workcell.library import pcr
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


def test_thermocycler_binding_spreads():
    # Rulings R3/R6(b): long thermocycling keeps tc1 busy when pcr-1 binds.
    b = compile_batch(
        parse_workcell({**SMALL, "durations": {"default": 1, "check_status": 0,
                                               "thermocycler.run_program": 30}}),
        [pcr(sample="S0001", primer="P0001"), pcr(sample="S0002", primer="P0002")])
    p = plan_greedy(b, park=True)
    assert {p.bindings["pcr-0"]["?tc0"], p.bindings["pcr-1"]["?tc0"]} == {"tc1", "tc2"}
    assert validate_plan(p, b) == []
