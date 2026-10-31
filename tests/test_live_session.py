from unittest.mock import patch

import pytest
from conftest import EXAMPLES

from workcell.dsl import load_batch
from workcell.live.executor import execute_live
from workcell.live.noise import NoiseParams
from workcell.live.session import as_run_plan, run_live
from workcell.planners.cpsat import plan_cpsat, replan_cpsat
from workcell.validate import validate_plan

FAST = dict(time_limit=1, replan_time_limit=0.3)


def specs(n):
    return load_batch(EXAMPLES / "pcr_live.py")[0][:n]


@pytest.mark.parametrize("seed", [0, 1])
def test_live_runs_validate(workcell_small, seed):
    ls = run_live(workcell_small, specs(3), seed=seed, delay_prob=0.1, replan_threshold=120, **FAST)
    assert ls.errors == [] and ls.session.runs[0].stats.completed == 3
    assert ls.live.actual_makespan == ls.session.runs[0].stats.makespan


@pytest.mark.slow
def test_some_seed_replans_and_still_validates(workcell_small):
    runs = [run_live(workcell_small, specs(3), seed=s, delay_prob=0.1, replan_threshold=60, **FAST)
            for s in range(6)]
    assert any(r.live.replans > 0 for r in runs) and all(r.errors == [] for r in runs)
    assert all(r.live.replans == r.live.adopted_replans + r.live.not_worth_it + r.live.failed_replans
               for r in runs)


def test_validator_duration_flag(timed_batch):
    b = timed_batch(1)
    p = plan_cpsat(b, time_limit=0.3, deterministic=True, hint="serial")
    r = execute_live(b, p, NoiseParams(seed=2), lambda st: p, 10**9)
    asrun = as_run_plan(b, r)
    assert validate_plan(asrun, b, check_durations=False) == []
    assert asrun.status == "complete" and set(asrun.start) == {blk.id for blk in b.all_blocks()}


@pytest.mark.slow
def test_same_seed_same_session(workcell_small):
    a = run_live(workcell_small, specs(3), seed=5, delay_prob=0.1, replan_threshold=60, **FAST)
    c = run_live(workcell_small, specs(3), seed=5, delay_prob=0.1, replan_threshold=60, **FAST)
    assert a.result.trace.spans == c.result.trace.spans and a.live == c.live


def test_session_payload(workcell_small):
    ls = run_live(workcell_small, specs(2), seed=1, **FAST)
    assert ls.session.runs[0].mode == "live" and len(ls.session.runs) == 1
    assert ls.session.live["seed"] == 1 and isinstance(ls.session.live["replans"], list)
    assert ls.session.live["stats"]["actual_makespan"] == ls.live.actual_makespan
    assert set(ls.session.live["solve_seconds"]) == {"initial", "replans"}


def test_as_run_validates_after_forced_replan(timed_batch):
    """An adopted re-plan mid-run still yields a physically valid as-run schedule. Threshold 0
    adopts any solved re-plan (with PCR, re-plans after a finished delay rarely beat sliding)."""
    from test_live_executor import overrun
    b = timed_batch(3)
    p = plan_cpsat(b, time_limit=0.3, deterministic=True, hint="serial")
    with patch("workcell.live.executor.actual_block", overrun("pcr_live-0/06", 6000)):
        r = execute_live(b, p, NoiseParams(noise=0, delay_prob=0),
                         lambda st: replan_cpsat(b, st, time_limit=0.3), 0)
    assert any(e.adopted for e in r.replans) and len(r.plans) > 1
    asrun = as_run_plan(b, r)
    assert asrun.status == "complete" and validate_plan(asrun, b, check_durations=False) == []


def test_live_session_idle_stats(workcell_small):
    ls = run_live(workcell_small, specs(2), seed=1, max_idle_hold=300, **FAST)
    assert ls.errors == [] and ls.session.live["max_idle_hold"] == 300
    st = ls.live
    assert st.parks >= 0 and st.idle_replans >= 0 and st.idle_violations >= 0
    off = run_live(workcell_small, specs(2), seed=1, max_idle_hold=None, **FAST)
    assert off.live.parks == 0 and off.session.live["max_idle_hold"] is None


def test_live_session_planning_settings(workcell_small):
    ls = run_live(workcell_small, specs(2), seed=1, **FAST)
    assert (ls.session.live["grid"], ls.session.live["window"]) == (10, 4 * 3600)
    assert all(s % 10 == 0 for s in ls.result.plans[0].start.values())
    exact = run_live(workcell_small, specs(2), seed=1, grid=1, window=None, **FAST)
    assert (exact.session.live["grid"], exact.session.live["window"]) == (1, None)
    assert ls.errors == [] and exact.errors == []
