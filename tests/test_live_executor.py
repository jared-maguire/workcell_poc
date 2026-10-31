from unittest.mock import patch

from workcell.live.executor import execute_live
from workcell.live.noise import NoiseParams
from workcell.plan import Plan
from workcell.planners.cpsat import plan_cpsat, replan_cpsat


def p0(b):
    return plan_cpsat(b, time_limit=0.3, deterministic=True, hint="serial")


def cp(b):
    return lambda st: replan_cpsat(b, st, time_limit=0.3)


QUIET = NoiseParams(noise=0, delay_prob=0)


def overrun(block_id, extra):
    """actual_block replacement: planned durations, plus `extra` s on block_id's longest
    instruction."""
    def f(noise, blk):
        ds = [i.duration for i in blk.instructions]
        if blk.id == block_id:
            ds[ds.index(max(ds))] += extra
        return ds
    return f


def all_ids(b):
    return {blk.id for blk in b.all_blocks()}


def test_zero_noise_matches_plan(timed_batch):
    b = timed_batch(3)
    p = p0(b)
    r = execute_live(b, p, QUIET, cp(b), 300)
    assert r.replans == [] and r.trace.alarm is None
    assert {bid: s for bid, (s, e) in r.trace.spans.items()} == p.start
    assert all(e == p.end(bid) for bid, (s, e) in r.trace.spans.items())


def test_noisy_run_completes(timed_batch):
    b = timed_batch(3)
    r = execute_live(b, p0(b), NoiseParams(seed=1), cp(b), 300)
    assert r.trace.alarm is None and r.trace.completed == all_ids(b)


def test_long_delay_triggers_replan(timed_batch):
    b = timed_batch(3)
    with patch("workcell.live.executor.actual_block", overrun("pcr_live-0/12", 900)):
        r = execute_live(b, p0(b), QUIET, cp(b), 300)
    assert r.replans and r.replans[0].trigger == "pcr_live-0/12" and r.replans[0].push > 300
    assert r.trace.alarm is None and r.trace.completed == all_ids(b)
    assert len(r.plans) == 1 + sum(e.adopted for e in r.replans)
    assert all(e.status == "not_worth_it" for e in r.replans
               if not e.adopted and e.makespan_after is not None)


def test_huge_threshold_never_replans(timed_batch):
    b = timed_batch(3)
    r = execute_live(b, p0(b), NoiseParams(seed=1, delay_prob=0.2), cp(b), 10**9)
    assert r.replans == [] and r.trace.alarm is None and r.trace.completed == all_ids(b)


def test_failed_replan_keeps_plan(timed_batch):
    b = timed_batch(3)
    bad = lambda st: Plan("cpsat:makespan", {}, {}, {}, "no_solution", solver_status="UNKNOWN")
    with patch("workcell.live.executor.actual_block", overrun("pcr_live-0/12", 900)):
        r = execute_live(b, p0(b), QUIET, bad, 300)
    assert r.replans and all(e.status == "no_solution" and e.makespan_after is None
                             and not e.adopted for e in r.replans)
    assert r.trace.alarm is None and len(r.plans) == 1 and r.trace.completed == all_ids(b)


def test_same_delay_does_not_retrigger(timed_batch):
    """A delay that was judged not worth re-planning resets the baseline (keep sliding)."""
    b = timed_batch(3)
    bad = lambda st: Plan("cpsat:makespan", {}, {}, {}, "no_solution", solver_status="UNKNOWN")
    with patch("workcell.live.executor.actual_block", overrun("pcr_live-0/12", 900)):
        r = execute_live(b, p0(b), QUIET, bad, 300)
    assert len(r.replans) == 1


def test_same_seed_same_run(timed_batch):
    b = timed_batch(3)
    r1 = execute_live(b, p0(b), NoiseParams(seed=4, delay_prob=0.1), cp(b), 120)
    r2 = execute_live(b, p0(b), NoiseParams(seed=4, delay_prob=0.1), cp(b), 120)
    assert r1.trace.spans == r2.trace.spans
    assert [(e.time, e.trigger) for e in r1.replans] == [(e.time, e.trigger) for e in r2.replans]


def test_delay_on_last_block_no_replan(timed_batch):
    b = timed_batch(1)
    p = p0(b)
    last = max(p.start, key=lambda x: (p.end(x), x))
    with patch("workcell.live.executor.actual_block", overrun(last, 5000)):
        r = execute_live(b, p, QUIET, cp(b), 300)
    assert r.replans == [] and r.trace.alarm is None


def test_overrun_attributed_while_still_running(timed_batch):
    """Another block's finish can reveal a still-running overrun: the event must name the
    overrunning block, with its positive delay so far, and the as-run schedule stays valid."""
    from workcell.live.session import as_run_plan
    from workcell.validate import validate_plan
    b = timed_batch(4)
    p = plan_cpsat(b, time_limit=3, deterministic=True, hint="serial")   # 2/00 overlaps 0/12's end
    bad = lambda st: Plan("cpsat:makespan", {}, {}, {}, "no_solution", solver_status="UNKNOWN")
    for replanner in (bad, cp(b)):
        with patch("workcell.live.executor.actual_block", overrun("pcr_live-2/00", 6000)):
            r = execute_live(b, p, QUIET, replanner, 300)
        assert r.replans and r.replans[0].trigger == "pcr_live-2/00"
        assert all(e.delay > 0 for e in r.replans)
    assert validate_plan(as_run_plan(b, r), b, check_durations=False) == []
