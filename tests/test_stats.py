from workcell.plan import Plan
import dataclasses

import yaml

from workcell.compiler import compile_batch
from workcell.config import FailureSpec
from workcell.library import pcr
from workcell.run import run_files
from conftest import SMALL
from workcell.planners.greedy import plan_greedy
from workcell.run import run_session
from workcell.sim.executor import execute
from workcell.stats import compute_stats


def test_stats_complete(compiled_small):
    b = compiled_small(2); p = plan_greedy(b, priority="fifo", park=True)
    s = compute_stats(p, execute(p, b), b)
    assert s.completed == s.total == 2 and s.makespan == p.makespan
    assert all(v is not None and v > 0 for v in s.latency.values())
    assert 0 < s.utilization["arm1"] <= 1


def test_stats_deadlock_partial(compiled_small):
    b = compiled_small(2); p = plan_greedy(b); s = compute_stats(p, execute(p, b), b)
    assert s.status == "deadlock" and s.completed == 0 and s.latency["pcr-0"] is None and s.max_latency is None


def test_stats_no_solution(compiled_small):
    b = compiled_small(1)
    p = Plan(mode="cpsat:makespan", blocks={}, start={}, bindings={}, status="no_solution")
    s = compute_stats(p, execute(p, b), b)
    assert s.status == "no_solution" and s.makespan == 0 and s.completed == 0 and s.utilization["arm1"] == 0


def test_run_session(workcell_small, pcr_specs):
    sess = run_session(workcell_small, pcr_specs(2), ["greedy", "greedy_hotel", "cpsat"], time_limit=10)
    assert [r.mode for r in sess.runs] == ["greedy:round_robin", "greedy_hotel:round_robin", "cpsat:makespan"]
    assert all(r.errors == [] for r in sess.runs)


def test_stats_alarm(workcell_small):
    wc = dataclasses.replace(workcell_small, failures=[FailureSpec(op="stamp", kind="fault")])
    b = compile_batch(wc, [pcr(sample="S0001", primer="P0001")])
    p = plan_greedy(b, priority="fifo", park=True)
    s = compute_stats(p, execute(p, b), b)
    assert s.status == "alarm" and s.alarm
    assert s.latency["pcr-0"] is None and s.completed < s.total


def test_run_files(tmp_path):
    wc_path, batch_path = tmp_path / "wc.yaml", tmp_path / "batch.py"
    wc_path.write_text(yaml.safe_dump(SMALL))
    batch_path.write_text("from workcell.library import pcr\n"
                          "def batch():\n    return [pcr(sample='S0001', primer='P0001')]\n")
    sess = run_files(wc_path, batch_path, ["greedy:fifo"], time_limit=5)
    assert sess.runs[0].stats.completed == 1 and "def batch" in sess.batch_source
    assert "instruments" in sess.workcell_source


def test_run_session_rejects_unknown_mode_first(workcell_small, pcr_specs, monkeypatch):
    import pytest
    import workcell.run as run
    monkeypatch.setattr(run, "compile_batch", lambda *a: pytest.fail("compiled before mode check"))
    with pytest.raises(ValueError, match="bogus.*greedy_hotel"):
        run_session(workcell_small, pcr_specs(1), ["greedy", "bogus"], time_limit=1)


def test_blocked_time_hand_checked(compiled_small):
    # One PCR under greedy fifo (see trace in final-fix-report): stamp1 holds a plate but is idle
    # at t=4-6 (S0001 waits for the empty plate) and t=9-10 (E0001 waits for the primer): 5 min;
    # scan1 holds P0001 at t=12 while the arm returns S0001: 1 min; tc1 holds the finished
    # PCR plate at t=19 while the arm returns P0001: 1 min.
    b = compiled_small(1); p = plan_greedy(b, priority="fifo"); s = compute_stats(p, execute(p, b), b)
    assert s.blocked == {"scan1": 1, "stamp1": 5, "tc1": 1, "tc2": 0}
    assert s.mean_completion == 24 and s.total_waiting == s.waiting["pcr-0"] == s.mean_waiting


def test_waiting_and_completion_summaries(compiled_small):
    b = compiled_small(3); p = plan_greedy(b, priority="fifo", park=True); s = compute_stats(p, execute(p, b), b)
    ends = [max(p.end(x.id) for x in wf.blocks) for wf in b.workflows]
    assert s.mean_completion == sum(ends) / 3
    assert s.total_waiting == sum(s.waiting.values()) and s.mean_waiting == s.total_waiting / 3


def test_waiting_summaries_none_when_nothing_completes(compiled_small):
    b = compiled_small(2); p = plan_greedy(b); s = compute_stats(p, execute(p, b), b)
    assert s.total_waiting is None and s.mean_waiting is None and s.mean_completion is None


def test_stats_incomplete_when_scheduled_blocks_never_finish(compiled_small):
    from helpers import VALID, sequential
    b = compiled_small(1)
    p = sequential(b, VALID[1:])       # pcr-0/00 missing: everything after it waits forever
    t = execute(p, b)
    assert t.alarm is None and set(p.start) - t.completed
    assert compute_stats(p, t, b).status == "incomplete"
