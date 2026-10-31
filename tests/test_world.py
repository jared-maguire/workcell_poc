import dataclasses

import pytest

from workcell.config import FailureSpec
from workcell.ir import Instruction
from workcell.sim.world import InstrumentError, World


def I(inst, op, **args):
    return Instruction(inst, op, args, 1)


def world(wc, **locs):
    return World(wc, {"S0001": "store1", "E0001": "store1", **locs})


def test_fetch_scan_sequence(workcell_small):
    w = world(workcell_small)
    for i in [I("store1", "retrieve", plate="S0001"),
              I("arm1", "move", plate="S0001", src="store1", dst="scan1"),
              I("scan1", "scan", expect="S0001")]:
        w.apply(i)
    assert w.location("S0001") == "scan1" and w.presented("store1") is None


@pytest.mark.parametrize("instrs, msg", [
    ([I("arm1", "move", plate="S0001", src="store1", dst="scan1")], "tray"),
    ([I("store1", "retrieve", plate="S0001"), I("store1", "retrieve", plate="E0001")], "tray"),
    ([I("store1", "retrieve", plate="S0001"),
      I("arm1", "move", plate="S0001", src="store1", dst="tc1")], "door"),
    ([I("tc1", "open_door"), I("tc1", "open_door")], "already open"),
    ([I("tc1", "close_door")], "already closed"),
    ([I("stamp1", "stamp", src="S0001", dst="E0001")], "stamp1.src"),
    ([I("tc1", "run_program", program=[])], "no plate"),
])
def test_illegal(workcell_small, instrs, msg):
    w = world(workcell_small)
    with pytest.raises(InstrumentError, match=msg):
        for i in instrs:
            w.apply(i)


def test_full_nest(workcell_small):
    w = world(workcell_small, S0002="scan1")
    w.apply(I("store1", "retrieve", plate="S0001"))
    with pytest.raises(InstrumentError, match="scan1"):
        w.apply(I("arm1", "move", plate="S0001", src="store1", dst="scan1"))


def test_barcode_mismatch_injection(workcell_small):
    wc = dataclasses.replace(
        workcell_small, failures=[FailureSpec(op="scan", plate="S0001", kind="barcode_mismatch")])
    w = world(wc, S0001="scan1")
    with pytest.raises(InstrumentError, match="barcode"):
        w.apply(I("scan1", "scan", expect="S0001"))


def test_fault_injection_on_check_status(workcell_small):
    wc = dataclasses.replace(
        workcell_small, failures=[FailureSpec(op="check_status", instrument="tc1")])
    w = world(wc)
    w.apply(I("tc2", "check_status"))
    with pytest.raises(InstrumentError, match="faulted") as e:
        w.apply(I("tc1", "check_status"))
    assert e.value.instrument == "tc1" and e.value.op == "check_status"


def test_store_and_tray_roundtrip_and_tc_run(workcell_small):
    w = world(workcell_small)
    w.apply(I("store1", "retrieve", plate="S0001"))
    w.apply(I("tc1", "open_door"))
    w.apply(I("arm1", "move", plate="S0001", src="store1", dst="tc1"))
    w.apply(I("tc1", "close_door"))
    w.apply(I("tc1", "run_program", program=[]))
    w.apply(I("tc1", "open_door"))
    w.apply(I("arm1", "move", plate="S0001", src="tc1", dst="store1"))
    assert w.presented("store1") == "S0001"
    w.apply(I("store1", "store", plate="S0001"))
    assert w.presented("store1") is None and w.location("S0001") == "store1"
    assert w.snapshot()["doors"]["tc1"] is True


@pytest.fixture
def lab():
    from conftest import SMALL
    from workcell.config import parse_workcell
    return parse_workcell({**SMALL, "instruments": SMALL["instruments"] + [
        {"id": "lh1", "type": "liquid_handler"}, {"id": "hs1", "type": "heated_shaker"}]})


def test_step_plate_missing(lab):
    w = world(lab)
    with pytest.raises(InstrumentError, match="plate S0001 not in hs1"):
        w.apply(I("hs1", "shake", plates=["S0001"]))


def test_step_ok(lab):
    w = world(lab, S0001="hs1", E0001="lh1.dst")
    w.apply(I("hs1", "shake", plates=["S0001"]))
    w.apply(I("lh1", "magnet_wash", plates=["E0001"]))
    with pytest.raises(InstrumentError, match="not in lh1"):
        w.apply(I("lh1", "pool", plates=["S0001", "E0001"]))


def test_move_out_of_reach(two_arm):
    w = World(two_arm, {"S0001": "store1"})
    w.apply(I("store1", "retrieve", plate="S0001"))
    with pytest.raises(InstrumentError, match="tc2 out of reach of arm1"):
        w.apply(I("arm1", "move", plate="S0001", src="store1", dst="tc2"))


def test_relay_through_handoff(two_arm):
    w = World(two_arm, {"S0001": "store1"})
    for i in [I("store1", "retrieve", plate="S0001"),
              I("arm1", "move", plate="S0001", src="store1", dst="ho1"),
              I("tc2", "open_door"),
              I("arm2", "move", plate="S0001", src="ho1", dst="tc2")]:
        w.apply(i)
    assert w.location("S0001") == "tc2"
