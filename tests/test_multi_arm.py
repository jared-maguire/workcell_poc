from pathlib import Path

from workcell.compiler import compile_batch
from workcell.dsl import load_batch
from workcell.live.session import run_live
from workcell.parking import parking_spots
from workcell.planners.cpsat import ReplanState, plan_cpsat, replan_cpsat
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


def test_two_arm_replan_valid(two_arm):
    b = compile_batch(two_arm, specs(2))
    p = plan(b)
    order = sorted(p.start, key=lambda k: (p.start[k], k))[:12]
    now = max(p.end(k) for k in order)
    keep = {wf: dict(v) for wf, v in p.bindings.items()}
    st = ReplanState(now=now, fixed={k: (p.start[k], p.end(k)) for k in order},
                     bindings=keep, hint=dict(p.start), hint_bindings=keep)
    new = replan_cpsat(b, st, time_limit=1, max_idle_hold=300)
    assert new.finished and validate_plan(new, b) == []


def test_two_arm_idle_hold_plan(two_arm):
    from workcell.planners.cpsat import plan_idle_hold
    b = compile_batch(two_arm, specs(2))
    p = plan_idle_hold(b, time_limit=2, max_idle_hold=300)
    assert p.finished and validate_plan(p, b) == []


def test_two_arm_live_runs(two_arm):
    ls = run_live(two_arm, specs(2), seed=1, time_limit=2, replan_time_limit=0.5)
    assert ls.errors == [] and ls.result.trace.alarm is None


def test_parking_spots_nearest_first(two_arm):
    assert parking_spots(two_arm, near="tc2") == ["hotel2", "hotel1", "store1"]
    assert parking_spots(two_arm, near="stamp1.dst") == ["hotel1", "store1", "hotel2"]


def test_replan_after_tech_park_of_variant_removal(two_arm):
    """The executor parked a plate whose removal's route depends on a binding (no CP-SAT park
    option): the re-plan takes the running park and rewritten removal as given."""
    from workcell.parking import park_pair, plan_parks
    b = compile_batch(two_arm, specs(2))
    p = plan(b)
    d = "pcr_live-0/15"
    assert b.block(d).variants
    tc = p.bindings["pcr_live-0"]["?tc0"]
    spot = parking_spots(two_arm, near=tc)[0]
    park, moved = park_pair(b, p.blocks[d], spot)
    fixed = {k: (p.start[k], p.end(k)) for k in p.start if p.start[k] < p.start[d]}
    t = max(e for _, e in fixed.values())
    fixed[park.id] = (t, t + park.duration)
    keep = {wf: dict(v) for wf, v in p.bindings.items()}
    st = ReplanState(now=t + park.duration, fixed=fixed, bindings=keep, hint=dict(p.start),
                     hint_bindings=keep, parks={d: spot}, blocks={d: moved, park.id: park})
    new = replan_cpsat(b, st, time_limit=1, max_idle_hold=300)
    assert new.finished and validate_plan(new, b) == []
    assert plan_parks(new) == {d: spot} and new.blocks[d].moves[0].src == spot
    assert new.start[park.id] == t


def test_replan_with_unmodelled_hint_park(two_arm):
    """The current plan holds a lab-tech park (not started yet) the model has no option for:
    the churn phase must not choke on it."""
    b = compile_batch(two_arm, specs(2))
    p = plan(b)
    order = sorted(p.start, key=lambda k: (p.start[k], k))[:12]
    keep = {wf: dict(v) for wf, v in p.bindings.items()}
    st = ReplanState(now=max(p.end(k) for k in order),
                     fixed={k: (p.start[k], p.end(k)) for k in order}, bindings=keep,
                     hint=dict(p.start), hint_bindings=keep,
                     hint_parks={"pcr_live-0/15": "hotel1", "pcr_live-1/15": "hotel2"})
    new = replan_cpsat(b, st, time_limit=1, max_idle_hold=300)
    assert new.finished and validate_plan(new, b) == []


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
