from pathlib import Path

from workcell.compiler import compile_batch
from workcell.dsl import load_batch
from workcell.live.session import run_live
from workcell.parking import parking_spots
from workcell.planners.cpsat import plan_cpsat
from workcell.sim.executor import execute
from workcell.validate import validate_plan

EX = Path(__file__).resolve().parent.parent / "examples"


def specs(n):
    return load_batch(EX / "pcr_live.py")[0][:n]


def plan(b, **kw):
    return plan_cpsat(b, time_limit=1, deterministic=True, hint="serial", **kw)


def test_two_arm_plan_valid(two_arm):
    b = compile_batch(two_arm, specs(2))
    p = plan(b)
    assert p.finished and validate_plan(p, b) == []
    tr = execute(p, b)
    assert tr.alarm is None and tr.completed == {blk.id for blk in b.all_blocks()}
    # whichever thermocyclers were chosen, moves to tc2 are relayed through ho1 by both arms
    for bid, blk in p.blocks.items():
        if any(m.dst == "tc2" or m.src == "tc2" for m in blk.moves):
            assert {"arm1", "arm2", "ho1"} <= blk.instruments


def test_two_arm_plan_uses_far_thermocycler(two_arm):
    b = compile_batch(two_arm, specs(2))
    p = plan(b)
    assert {p.bindings[wf]["?tc0"] for wf in p.bindings} == {"tc1", "tc2"}


def test_two_arm_live_runs(two_arm):
    ls = run_live(two_arm, specs(2), seed=1)
    assert ls.errors == [] and ls.trace.alarm is None and ls.live.actual_makespan > 0


def test_parking_spots_nearest_first(two_arm):
    assert parking_spots(two_arm, near="tc2") == ["hotel2", "hotel1", "store1"]
    assert parking_spots(two_arm, near="stamp1.dst") == ["hotel1", "store1", "hotel2"]


def test_serial_hint_picks_near_candidates(two_arm):
    """Multi-arm: the serial warm start binds each choice to the candidate nearest the plate
    (fewest arm hops), so the solver doesn't start from relays everywhere; it stays valid."""
    from conftest import TWO_ARM
    from workcell.config import parse_workcell
    from workcell.plan import Plan, bind_batch
    from workcell.planners.cpsat import serial_hint
    insts = sorted(TWO_ARM["instruments"], key=lambda i: i["id"] != "tc2")   # tc2 first
    b = compile_batch(parse_workcell({**TWO_ARM, "instruments": insts}), specs(2))
    assert b.workflows[0].variables["?tc0"][0] == "tc2"
    starts, binds = serial_hint(b)
    assert all(v["?tc0"] == "tc1" for v in binds.values())       # stamper is in arm1's zone
    p = Plan("serial", bind_batch(b, binds), starts, binds, "feasible")
    assert validate_plan(p, b) == []


_MODEL_SCRIPT = """
import hashlib, sys
sys.path.insert(0, 'tests')
from conftest import TWO_ARM
from workcell.compiler import compile_batch
from workcell.config import parse_workcell
from workcell.dsl import load_batch
from workcell.planners.cpsat import _Model
b = compile_batch(parse_workcell(TWO_ARM), load_batch('examples/pcr_live.py')[0][:2])
m = _Model(b)
m.objective('makespan')
print(hashlib.sha256(str(m.m.Proto()).encode()).hexdigest())
"""


def test_two_arm_model_same_across_processes():
    """The multi-arm CP-SAT model is built in the same
    order under any string-hash seed, so deterministic solves give the same plan."""
    import os
    import subprocess
    import sys
    root = Path(__file__).resolve().parent.parent
    outs = {subprocess.run([sys.executable, "-c", _MODEL_SCRIPT], cwd=root, check=True,
                           capture_output=True, text=True,
                           env={**os.environ, "PYTHONHASHSEED": seed}).stdout
            for seed in ("1", "2", "3", "4")}
    assert len(outs) == 1 and next(iter(outs))


def test_cpsat_default_hint_on_two_arm_libprep():
    """The default warm start must handle two arms (greedy_hotel's parks can't route a move
    whose destination is still a cross-zone variable)."""
    from workcell.config import load_workcell
    b = compile_batch(load_workcell(EX / "libprep_2arm.yaml"),
                      load_batch(EX / "libprep_capture.py")[0])
    p = plan_cpsat(b, time_limit=1)
    assert p.status in ("optimal", "feasible", "no_solution")
