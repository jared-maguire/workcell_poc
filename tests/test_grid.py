from workcell.planners.cpsat import ReplanState, plan_cpsat, plan_idle_hold, replan_cpsat
from workcell.sim.executor import execute
from workcell.validate import validate_plan


def test_grid_plan_valid(timed_batch):
    b = timed_batch(2)
    p = plan_cpsat(b, time_limit=1, deterministic=True, hint="serial", grid=10)
    assert p.finished and validate_plan(p, b) == []
    assert all(s % 10 == 0 for s in p.start.values())
    assert execute(p, b).alarm is None


def test_grid_replan_fixed_offgrid(timed_batch):
    """Fixed blocks at off-grid times, back to back on the arm: the model must stay feasible
    and the re-plan valid."""
    b = timed_batch(2)
    p = plan_cpsat(b, time_limit=1, deterministic=True, hint="serial")
    order = sorted(p.start, key=lambda k: (p.start[k], k))[:10]
    fixed, t = {}, 3
    for k in order:                          # one at a time from t=3: every edge off-grid
        fixed[k] = (t, t + p.blocks[k].duration)
        t += p.blocks[k].duration
    keep = {wf: dict(v) for wf, v in p.bindings.items()}
    st = ReplanState(now=t, fixed=fixed, bindings=keep, hint=dict(p.start), hint_bindings=keep)
    new = replan_cpsat(b, st, time_limit=1, grid=10)
    assert new.finished
    assert all(new.start[k] >= t for k in new.start if k not in fixed)
    assert all(new.start[k] == fixed[k][0] for k in fixed)


def test_grid_idle_hold_valid(timed_batch):
    b = timed_batch(3)
    p = plan_idle_hold(b, time_limit=2, max_idle_hold=300, grid=10)
    assert p.finished and validate_plan(p, b, max_idle_hold=300) == []
