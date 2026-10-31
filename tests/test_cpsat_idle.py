import copy
from pathlib import Path

from conftest import SMALL

from workcell.compiler import compile_batch
from workcell.config import parse_workcell
from workcell.dsl import load_batch
from workcell.parking import park_durations, park_pair, parking_spots, plan_parks
from workcell.planners.cpsat import ReplanState, plan_cpsat
from workcell.sim.executor import execute
from workcell.validate import validate_plan

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
ORDER = ["00", "01", "02", "03", "04", "05", "06", "13", "07", "08", "09", "10", "14", "11"]


def without(*ids):
    cfg = copy.deepcopy(SMALL)
    cfg["instruments"] = [i for i in cfg["instruments"] if i["id"] not in ids]
    return parse_workcell(cfg)


def batch_on(wc, n):
    return compile_batch(wc, load_batch(EXAMPLES / "pcr_live.py")[0][:n])


def busy_state(b):
    """Workflows 0 and 1 thermocycling on tc1 and tc2 (fixed, one block at a time before
    that); workflow 2 done through its last stamp and primer return. Both thermocyclers stay
    busy for ~50 more minutes, so 2/11 cannot go straight to a thermocycler."""
    t, fixed = 0, {}
    for wf in range(3):
        for s in ORDER if wf < 2 else ORDER[:-1]:
            bid = f"pcr_live-{wf}/{s}"
            d = b.block(bid).duration
            fixed[bid] = (t, t + d)
            t += d
        if wf < 2:
            fixed[f"pcr_live-{wf}/12"] = (t, t + b.block(f"pcr_live-{wf}/12").duration)
    return ReplanState(now=t, fixed=fixed,
                       bindings={"pcr_live-0": {"?tc0": "tc1"}, "pcr_live-1": {"?tc0": "tc2"}})


def solve(b, st):
    return plan_cpsat(b, time_limit=2, deterministic=True, hint="serial", state=st,
                      max_idle_hold=300)


def test_busy_thermocyclers_force_park(timed_batch):
    b = timed_batch(3)
    st = busy_state(b)
    p = solve(b, st)
    assert p.finished and validate_plan(p, b, max_idle_hold=300) == []
    parks = plan_parks(p)
    assert parks["pcr_live-2/11"] == "hotel1"
    k = "pcr_live-2/11.park"
    assert p.blocks[k].moves[0].src == "stamp1.dst"
    assert st.now <= p.start[k] <= st.now + 60                      # right away
    assert p.blocks["pcr_live-2/11"].moves[0].src == "hotel1"
    assert p.bindings["pcr_live-2"]["?tc0"] in ("tc1", "tc2")
    assert execute(p, b).alarm is None


def test_parks_to_storage_without_hotel():
    b = batch_on(without("hotel1"), 3)
    p = solve(b, busy_state(b))
    assert p.finished and validate_plan(p, b, max_idle_hold=300) == []
    assert plan_parks(p)["pcr_live-2/11"] == "store1"


def test_no_limit_unchanged(timed_batch):
    b = timed_batch(2)
    a = plan_cpsat(b, time_limit=0.5, deterministic=True, hint="serial")
    c = plan_cpsat(b, time_limit=0.5, deterministic=True, hint="serial", max_idle_hold=None)
    assert a.start == c.start and plan_parks(c) == {}


def test_limit_plan_valid(timed_batch):
    b = timed_batch(2)
    p = plan_cpsat(b, time_limit=1, deterministic=True, hint="serial", max_idle_hold=300)
    assert p.finished and validate_plan(p, b, max_idle_hold=300) == []
    assert execute(p, b).alarm is None


def test_parking_spots(timed_batch):
    assert parking_spots(timed_batch(1).workcell) == ["hotel1", "store1"]


def test_park_pair_shape(timed_batch):
    b = timed_batch(1)
    d = b.block("pcr_live-0/11").bind({"?tc0": "tc1"})
    park, moved = park_pair(b, d, "hotel1")
    assert (park.id, park.kind, park.inserted) == ("pcr_live-0/11.park", "park", True)
    assert [(m.src, m.dst) for m in park.moves] == [("stamp1.dst", "hotel1")]
    assert [(m.src, m.dst) for m in moved.moves] == [("hotel1", "tc1")]
    assert park.id in moved.deps and set(d.deps) <= set(moved.deps) and park.deps == d.deps
    assert park.duration == 10 and moved.duration == 20     # move 10; doors 5+5 + move 10


def test_park_durations_var_source(timed_batch):
    b = timed_batch(1)
    assert park_durations(b, b.block("pcr_live-0/15"), "hotel1") == (20, 70)   # doors+move; move+store
    assert park_durations(b, b.block("pcr_live-0/15"), "store1") == (80, 130)  # +store; retrieve+move+store
