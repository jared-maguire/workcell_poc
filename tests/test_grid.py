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


from workcell.live.executor import execute_live  # noqa: E402
from workcell.live.noise import NoiseParams  # noqa: E402

QUIET = NoiseParams(noise=0, delay_prob=0)


def test_window_plan_partial(timed_batch):
    b = timed_batch(3)
    p = plan_cpsat(b, time_limit=1, deterministic=True, hint="serial", window=3600, grid=10)
    total = {x.id for x in b.all_blocks()}
    assert p.finished and p.partial and set(p.start) < total
    assert validate_plan(p, b) == []


def test_window_open_stays_block_nest(timed_batch):
    """Plates still in a thermocycler at the window's end (their return is outside it) keep
    it: three workflows, two thermocyclers, a window that ends mid-run."""
    b = timed_batch(3)
    p = plan_cpsat(b, time_limit=1, deterministic=True, hint="serial", window=5000, grid=10)
    tc_runs = [k for k in p.start if k.endswith("/12")]
    assert len(tc_runs) <= 2 or validate_plan(p, b) == []
    assert validate_plan(p, b) == []


def test_window_live_completes(timed_batch):
    b = timed_batch(3)
    p = plan_cpsat(b, time_limit=1, deterministic=True, hint="serial", window=3600, grid=10)
    rp = lambda st: replan_cpsat(b, st, time_limit=0.5, grid=10, window=3600)
    r = execute_live(b, p, QUIET, rp, 300, window=3600)
    assert r.trace.alarm is None and r.trace.completed >= {x.id for x in b.all_blocks()}
    assert any(e.reason == "horizon" for e in r.replans)
    from workcell.live.session import as_run_plan
    assert validate_plan(as_run_plan(b, r), b, check_durations=False) == []


def test_window_more_workflows_than_thermocyclers(timed_batch):
    """4 PCRs, 2 thermocyclers, 1 h window: two plates sit in the thermocyclers past the
    window (open stays) and the other two can only be placed after that — still feasible."""
    b = timed_batch(4)
    p = plan_cpsat(b, time_limit=0.5, deterministic=True, hint="serial", grid=10, window=3600)
    assert p.finished and p.partial and validate_plan(p, b) == []


def test_grid_given_park_and_started_removal(two_arm):
    """A lab-tech park and its rewritten removal both started, the removal 3 s after the
    park ended inside one 10 s cell: the grid model must stay feasible."""
    from pathlib import Path
    from workcell.compiler import compile_batch
    from workcell.dsl import load_batch
    from workcell.parking import park_pair, parking_spots
    b = compile_batch(two_arm, load_batch(Path(__file__).resolve().parent.parent
                                          / "examples/pcr_live.py")[0][:2])
    p = plan_cpsat(b, time_limit=1, deterministic=True, hint="serial")
    d = "pcr_live-0/15"
    spot = parking_spots(two_arm, near=p.bindings["pcr_live-0"]["?tc0"])[0]
    park, moved = park_pair(b, p.blocks[d], spot)
    fixed = {k: (p.start[k], p.end(k)) for k in p.start if p.start[k] < p.start[d]}
    t = max(e for _, e in fixed.values()) + 1
    t += (1 - (t + park.duration)) % 10                         # park ends 1 s into a cell
    fixed[park.id] = (t, t + park.duration)
    fixed[d] = (t + park.duration + 3, t + park.duration + 3 + moved.duration)
    keep = {wf: dict(v) for wf, v in p.bindings.items()}
    st = ReplanState(now=fixed[d][1] + 1, fixed=fixed, bindings=keep, hint=dict(p.start),
                     hint_bindings=keep, blocks={d: moved, park.id: park})
    new = replan_cpsat(b, st, time_limit=1, grid=10, max_idle_hold=300)
    assert new.finished
