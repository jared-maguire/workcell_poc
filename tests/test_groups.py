"""Workcells of separate groups (arms connected through handoffs within a group, not across):
each workflow uses only the instruments of its plates' group."""
import sys
from pathlib import Path

import pytest
from conftest import TWO_ARM

from workcell.compiler import CompileError, compile_batch
from workcell.config import parse_workcell
from workcell.dsl import load_batch
from workcell.parking import parking_spots
from workcell.planners.dispatch import plan_dispatch
from workcell.sim.executor import execute
from workcell.validate import validate_plan

EX = Path(__file__).resolve().parent.parent / "examples"


def apart(**extra):
    """TWO_ARM with arm2 cut off from ho1: group 1 = arm1's zone, group 2 = arm2, tc2, hotel2."""
    cfg = {**TWO_ARM, "instruments": [dict(i) for i in TWO_ARM["instruments"]]}
    for inst in cfg["instruments"]:
        if inst["id"] == "arm2":
            inst["reach"] = ["tc2", "hotel2"] + extra.get("arm2", [])
    cfg["instruments"] += extra.get("instruments", [])
    return cfg


def specs(n):
    return load_batch(EX / "pcr_live.py")[0][:n]


def test_separate_groups_allowed():
    wc = parse_workcell(apart())
    assert wc.group("tc2") == {"arm2", "tc2", "hotel2"}
    assert "tc2" not in wc.group("tc1") and {"arm1", "store1", "ho1"} <= wc.group("tc1")


def test_candidates_limited_to_the_plates_group():
    b = compile_batch(parse_workcell(apart()), specs(2))
    used = {i for blk in b.all_blocks() for i in blk.instruments}
    assert "tc1" in used and not used & {"arm2", "tc2", "hotel2"}


def test_plates_in_two_groups_is_an_error():
    cfg = apart(arm2=["store2"], instruments=[{"id": "store2", "type": "storage", "slots": 10}])
    cfg["plates"] = [dict(p) for p in cfg["plates"]]
    cfg["plates"].append({"barcode": "X0001", "type": "primer", "location": "store2"})
    from workcell.dsl import Workflow, workflow

    @workflow
    def split(w: Workflow):
        w.stamp(src=w.own("S0001"), dst=w.own("X0001"))
    with pytest.raises(CompileError, match="groups"):
        compile_batch(parse_workcell(cfg), [split()])


def test_parking_spots_stay_in_the_group():
    wc = parse_workcell(apart())
    assert "hotel2" not in parking_spots(wc, near="tc1")
    assert parking_spots(wc, near="tc2") == ["hotel2"]


def test_lines_workcell_dispatches_per_line():
    sys.path.insert(0, str(EX))
    from libprep_capture import libprep_capture
    from make_libprep_lines import workcell
    wc = parse_workcell(workcell(2, 4))
    b = compile_batch(wc, [libprep_capture(batch=i) for i in range(1, 5)])
    p = plan_dispatch(b, max_idle_hold=300)
    assert p.status == "complete" and validate_plan(p, b) == []
    tr = execute(p, b)
    assert tr.alarm is None and tr.lock_waits == []
    for i, wf in enumerate(b.workflows):
        home = wc.group(f"store{i % 2 + 1}")
        for blk in wf.blocks:
            used = p.blocks[blk.id].instruments
            assert used <= home, (wf.id, used - home)
