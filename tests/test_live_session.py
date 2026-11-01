import pytest
from conftest import EXAMPLES

from workcell.dsl import load_batch
from workcell.live.session import run_live
from workcell.validate import validate_plan


def specs(n):
    return load_batch(EXAMPLES / "pcr_live.py")[0][:n]


@pytest.mark.parametrize("seed", [0, 1])
def test_live_runs_validate(workcell_small, seed):
    ls = run_live(workcell_small, specs(3), seed=seed, delay_prob=0.1)
    assert ls.errors == [] and ls.session.runs[0].stats.completed == 3
    assert ls.live.actual_makespan == ls.session.runs[0].stats.makespan
    assert ls.trace.alarm is None and ls.trace.lock_waits == []


def test_forecast_is_noise_free_dispatch(workcell_small):
    ls = run_live(workcell_small, specs(3), seed=1)
    calm = run_live(workcell_small, specs(3), seed=1, noise=0, delay_prob=0)
    assert ls.forecast.status == "complete" and ls.live.planned_makespan == ls.forecast.makespan
    assert calm.live.actual_makespan == calm.live.planned_makespan and calm.live.total_delay == 0
    assert validate_plan(ls.forecast, ls.session.batch) == []


def test_same_seed_same_session(workcell_small):
    a = run_live(workcell_small, specs(3), seed=5, delay_prob=0.1)
    c = run_live(workcell_small, specs(3), seed=5, delay_prob=0.1)
    assert a.trace.spans == c.trace.spans
    assert {**a.live.__dict__, "dispatch_seconds": 0} == {**c.live.__dict__, "dispatch_seconds": 0}


def test_session_payload(workcell_small):
    ls = run_live(workcell_small, specs(2), seed=1)
    assert ls.session.runs[0].mode == "live" and len(ls.session.runs) == 1
    assert ls.session.live["seed"] == 1
    assert ls.session.live["stats"]["actual_makespan"] == ls.live.actual_makespan


def test_live_session_idle_stats(workcell_small):
    ls = run_live(workcell_small, specs(4), seed=1, max_idle_hold=300)
    assert ls.errors == [] and ls.session.live["max_idle_hold"] == 300
    assert ls.live.parks > 0 and ls.live.idle_violations >= 0
    off = run_live(workcell_small, specs(4), seed=1, max_idle_hold=None)
    assert off.session.live["max_idle_hold"] is None
    assert off.live.idle_violations == 0 and off.errors == []
