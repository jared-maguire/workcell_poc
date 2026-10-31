import pytest

from workcell.live.order import plan_preds, project
from workcell.planners import cpsat
from workcell.planners.cpsat import ReplanState, plan_cpsat, replan_cpsat


def plan(b):
    return plan_cpsat(b, time_limit=0.3, deterministic=True, hint="serial")


def used_vars(blk):
    """`?vars` a compiled block refers to (instruments and move nests)."""
    names = list(blk.instruments) + [n for mv in blk.moves for n in (mv.src, mv.dst)]
    return {cpsat._var(n) for n in names} - {None}


def state_after_overrun(b, p0, extra, during=False):
    """The earliest-ending thermocycle run overruns by `extra` s. Project the rest of p0 under
    that delay (as the live executor does), then re-plan at the moment the run finishes —
    or, with `during`, one second after it starts, its late end already known: blocks
    projected to have started by then are fixed, the rest are hinted at their projected
    starts."""
    run = min((bid for bid in p0.start if b.block(bid).duration == 4020),
              key=lambda x: (p0.end(x), x))
    s = p0.start[run]
    end = s + 4020 + extra
    now = s + 1 if during else end
    done = {bid for bid in p0.start if p0.end(bid) <= s}
    starts = {bid: p0.start[bid] for bid in done | {run}}
    ends = {bid: p0.end(bid) for bid in done} | {run: end}
    proj = project(p0, plan_preds(p0, b), starts, ends, s)
    when = {**proj, **starts}
    fixed = {bid: (when[bid], ends.get(bid, when[bid] + b.block(bid).duration))
             for bid in p0.start if when[bid] < now or bid in ends}
    bindings = {}
    for bid in fixed:
        blk = b.block(bid)
        for var in used_vars(blk):
            bindings.setdefault(blk.workflow, {})[var] = p0.bindings[blk.workflow][var]
    return run, ReplanState(
        now=now, fixed=fixed, bindings=bindings,
        hint={bid: when[bid] for bid in p0.start if bid not in fixed},
        hint_bindings={wf: dict(bd) for wf, bd in p0.bindings.items()})


def true_makespan(p, st):
    """Makespan with fixed blocks at their actual ends (Plan.end uses planned durations)."""
    return max(st.fixed[bid][1] if bid in st.fixed else p.end(bid) for bid in p.start)


def tc_var(b, wf):
    return next(v for v, c in b.workflows[[w.id for w in b.workflows].index(wf)].variables.items()
                if c[0].startswith("tc"))


def test_no_delay_no_churn(timed_batch):
    b = timed_batch(3)
    p0 = plan(b)
    _, st = state_after_overrun(b, p0, 0)
    r = replan_cpsat(b, st, time_limit=0.3)
    assert r.status in ("optimal", "feasible")
    assert r.bindings == p0.bindings
    assert all(r.start[bid] == st.hint[bid] for bid in st.hint)


def test_idle_thermocycler_used(timed_batch):
    b = timed_batch(3)
    p0 = plan(b)
    run, st = state_after_overrun(b, p0, 3000, during=True)
    busy_wf = b.block(run).workflow
    busy_tc = p0.bindings[busy_wf][tc_var(b, busy_wf)]
    movable = [w.id for w in b.workflows if w.id != busy_wf
               and tc_var(b, w.id) not in st.bindings.get(w.id, {})
               and p0.bindings[w.id][tc_var(b, w.id)] == busy_tc]
    assert movable, "fixture should leave an unstarted run queued behind the overrunning one"
    phase1 = plan_cpsat(b, time_limit=0.3, deterministic=True, state=st)
    r = replan_cpsat(b, st, time_limit=0.3)
    assert all(r.bindings[w][tc_var(b, w)] != busy_tc for w in movable)
    assert true_makespan(r, st) <= true_makespan(phase1, st)


def test_phase2_failure_falls_back_to_phase1(timed_batch, monkeypatch):
    b = timed_batch(2)
    p0 = plan(b)
    _, st = state_after_overrun(b, p0, 1000)
    phase1 = plan_cpsat(b, time_limit=0.3, deterministic=True, state=st)
    real = cpsat._solve
    calls = []

    def failing_second(m, solver, isolate):
        calls.append(1)
        if len(calls) == 2:
            return cpsat._Solution("UNKNOWN", None, [], 0.0)
        return real(m, solver, isolate)

    monkeypatch.setattr(cpsat, "_solve", failing_second)
    r = replan_cpsat(b, st, time_limit=0.3)
    assert len(calls) == 2
    assert r.start == phase1.start and r.bindings == phase1.bindings


def test_deterministic(timed_batch):
    b = timed_batch(2)
    p0 = plan(b)
    _, st = state_after_overrun(b, p0, 1000)
    r1 = replan_cpsat(b, st, time_limit=0.3)
    r2 = replan_cpsat(b, st, time_limit=0.3)
    assert r1.start == r2.start and r1.bindings == r2.bindings
    assert "/" in r1.solver_status
