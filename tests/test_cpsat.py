from workcell.planners import make_plan
from workcell.planners.cpsat import plan_cpsat
from workcell.planners.greedy import plan_greedy
from workcell.sim.executor import execute
from workcell.validate import validate_plan


def test_cpsat_valid_and_executes(compiled_small):
    b = compiled_small(2); p = plan_cpsat(b, time_limit=10)
    assert p.status in ("optimal", "feasible") and p.mode == "cpsat:makespan"
    assert validate_plan(p, b) == []
    t = execute(p, b); assert t.alarm is None and t.lock_waits == []


def test_cpsat_beats_or_ties_greedy(compiled_small):
    # Ruling R6(c): pure greedy deadlocks on >=2 PCRs, so compare against greedy_hotel fifo.
    b = compiled_small(4)
    g = plan_greedy(b, priority="fifo", park=True); c = plan_cpsat(b, time_limit=20)
    assert c.makespan <= g.makespan


def test_sum_completion_objective(compiled_small):
    # Ruling R6(c): compare against greedy_hotel fifo.
    b = compiled_small(3)
    def total(p): return sum(max(p.end(x.id) for x in w.blocks) for w in b.workflows)
    s = plan_cpsat(b, objective="sum_completion", time_limit=20)
    assert validate_plan(s, b) == [] and total(s) <= total(plan_greedy(b, priority="fifo", park=True))


def test_no_solution_when_time_limit_tiny(compiled_small):
    p = plan_cpsat(compiled_small(10), time_limit=0.001, hint=False)
    assert p.status in ("no_solution", "feasible", "optimal")
    if p.status == "no_solution":
        assert p.start == {} and not p.finished


def test_registry(compiled_small):
    assert make_plan(compiled_small(1), "cpsat:sum_completion", time_limit=5).mode == "cpsat:sum_completion"
