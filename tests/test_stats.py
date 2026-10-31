from workcell.plan import Plan
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
    assert s.makespan == 0 and s.completed == 0 and s.utilization["arm1"] == 0


def test_run_session(workcell_small, pcr_specs):
    sess = run_session(workcell_small, pcr_specs(2), ["greedy", "greedy_hotel", "cpsat"], time_limit=10)
    assert [r.mode for r in sess.runs] == ["greedy:round_robin", "greedy_hotel:round_robin", "cpsat:makespan"]
    assert all(r.errors == [] for r in sess.runs)
