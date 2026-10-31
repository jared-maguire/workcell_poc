import pytest

from workcell.compiler import AmbiguousRoute, route, transport_instructions
from workcell.sim.world import World

T = {"arm.move": 10, "open_door": 5, "close_door": 5, "retrieve": 60, "store": 60,
     "check_status": 0}


def ops(instrs):
    return [(i.instrument, i.op, i.args.get("src"), i.args.get("dst")) for i in instrs]


def test_route_same_arm(two_arm):
    assert route(two_arm, "store1", "scan1") == [("arm1", None, None)]


def test_route_relay(two_arm):
    assert route(two_arm, "store1", "tc2") == [("arm1", None, "ho1"), ("arm2", "ho1", None)]


def test_same_arm_move_unchanged(two_arm):
    got = ops(transport_instructions(two_arm, "S0001", "store1", "scan1", times=T))
    assert got == [("store1", "check_status", None, None), ("arm1", "check_status", None, None),
                   ("scan1", "check_status", None, None), ("store1", "retrieve", None, None),
                   ("arm1", "move", "store1", "scan1")]


def test_relay_instructions(two_arm):
    got = ops(transport_instructions(two_arm, "S0001", "store1", "tc2", times=T))
    assert got == [
        ("store1", "check_status", None, None), ("arm1", "check_status", None, None),
        ("ho1", "check_status", None, None), ("arm2", "check_status", None, None),
        ("tc2", "check_status", None, None),
        ("store1", "retrieve", None, None),
        ("arm1", "move", "store1", "ho1"),
        ("tc2", "open_door", None, None),
        ("arm2", "move", "ho1", "tc2"),
        ("tc2", "close_door", None, None)]
    back = ops(transport_instructions(two_arm, "S0001", "tc2", "hotel1", times=T))
    assert back[-4:] == [("tc2", "open_door", None, None), ("arm2", "move", "tc2", "ho1"),
                         ("tc2", "close_door", None, None), ("arm1", "move", "ho1", "hotel1")]


def test_relay_runs_in_simulator(two_arm):
    w = World(two_arm, {"S0001": "store1"})
    for i in transport_instructions(two_arm, "S0001", "store1", "tc2", times=T):
        w.apply(i)
    for i in transport_instructions(two_arm, "S0001", "tc2", "hotel1", times=T):
        w.apply(i)
    assert w.location("S0001") == "hotel1"


def test_var_spanning_arms_is_ambiguous(two_arm):
    with pytest.raises(AmbiguousRoute):
        transport_instructions(two_arm, "E0001", "stamp1.dst", "?tc0",
                               var_types={"?tc0": "thermocycler"},
                               candidates={"?tc0": ["tc1", "tc2"]}, times=T)
