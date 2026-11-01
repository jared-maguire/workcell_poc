from workcell.idle import idle_gaps, violations
from workcell.plan import Plan, bind_batch
from workcell.planners.cpsat import serial_hint
from workcell.validate import validate_plan

P = "pcr_live-0/"


def serial_plan(b):
    starts, binds = serial_hint(b)
    return Plan("x", bind_batch(b, binds), starts, binds, "feasible")


def gaps_of(plan, b):
    return idle_gaps(plan.blocks, plan.start, {k: plan.end(k) for k in plan.start}, b)


def test_serial_plan_has_no_violations(timed_batch):
    b = timed_batch(2)
    plan = serial_plan(b)
    gaps = gaps_of(plan, b)
    assert gaps and violations(gaps, 300) == []
    assert validate_plan(plan, b, max_idle_hold=300) == []


def test_idle_gaps_follow_the_plate(timed_batch):
    b = timed_batch(1)
    plan = serial_plan(b)
    g = {(x.plate, x.prev, x.next): x for x in gaps_of(plan, b)}
    x = g[("E0001", P + "10", P + "11")]
    assert x.nest == "stamp1.dst" and x.length == plan.start[P + "11"] - plan.end(P + "10")
    assert ("E0001", P + "15", P + "00") not in g          # nothing after the return to storage
    assert all(x.nest not in ("store1", "hotel1") for x in g.values())


def test_validator_flags_long_wait(timed_batch):
    b = timed_batch(1)
    plan = serial_plan(b)
    t11 = plan.start[P + "11"]
    plan.start = {k: s + 1000 if s >= t11 else s for k, s in plan.start.items()}
    errs = validate_plan(plan, b, max_idle_hold=300)
    assert any("E0001" in e and "stamp1.dst" in e and P + "10" in e and P + "11" in e
               and "limit 300s" in e for e in errs)
    assert validate_plan(plan, b) == []                    # no limit → no idle errors
