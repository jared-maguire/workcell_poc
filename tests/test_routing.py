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


from conftest import TWO_ARM  # noqa: E402

from workcell.compiler import compile_batch  # noqa: E402
from workcell.config import parse_workcell  # noqa: E402
from workcell.dsl import workflow  # noqa: E402

LAB2 = parse_workcell({**TWO_ARM, "instruments": [
    {**i, "reach": i["reach"] + (["lh1"] if i["id"] == "arm1" else ["lh2"] if i["id"] == "arm2" else [])}
    if i["type"] == "arm" else i for i in TWO_ARM["instruments"]]
    + [{"id": "lh1", "type": "liquid_handler"}, {"id": "lh2", "type": "liquid_handler"}]})


@workflow(times=T)
def to_tc(w):
    p = w.own("S0001")
    w.thermocycle(p, program=[(95, 1)])


def test_variants_one_var(two_arm):
    wf = compile_batch(two_arm, [to_tc()]).workflows[0]
    (mv,) = [b for b in wf.blocks if b.moves and b.moves[0].dst == "?tc0"]
    assert mv.variants is not None and set(mv.variants) == {(("?tc0", "tc1"),), (("?tc0", "tc2"),)}
    near, far = mv.bind({"?tc0": "tc1"}), mv.bind({"?tc0": "tc2"})
    assert "arm2" not in near.instruments and {"arm1", "arm2", "ho1"} <= far.instruments
    assert far.duration > near.duration and far.moves[0].dst == "tc2"
    assert mv.duration == near.duration                    # unbound = first candidates


def test_variants_two_vars():
    @workflow(times=T)
    def f(w):
        p = w.own("S0001")
        w.step("liquid_handler", "wash", p, seconds=60)
        w.thermocycle(p, program=[(95, 1)])
    wf = compile_batch(LAB2, [f()]).workflows[0]
    (mv,) = [b for b in wf.blocks if b.moves and b.moves[0].dst == "?tc0"]
    assert len(mv.variants) == 4
    assert {"arm1", "arm2"} <= mv.bind({"?lh0": "lh1", "?tc0": "tc2"}).instruments
    assert mv.bind({"?lh0": "lh2", "?tc0": "tc2"}).instruments >= {"arm2", "lh2", "tc2"}
    assert "arm1" not in mv.bind({"?lh0": "lh2", "?tc0": "tc2"}).instruments


def test_lh_numbered_stay_put():
    @workflow(times=T)
    def f(w):
        a, b = w.own("S0001"), w.own(w.new_plate("empty"))
        w.step("liquid_handler", "wash", a, seconds=60)
        w.step("liquid_handler", "dry", a, seconds=60)          # stays on the same LH
        w.step("liquid_handler", "pool", a, b, seconds=60)      # a new LH choice
    wf = compile_batch(LAB2, [f()]).workflows[0]
    labels = [blk.label for blk in wf.blocks]
    i = labels.index("wash S0001")
    assert labels[i + 1] == "dry S0001"
    assert set(wf.variables) >= {"?lh0", "?lh1"}
