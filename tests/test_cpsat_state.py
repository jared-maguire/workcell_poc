import os
import subprocess
import sys
from pathlib import Path

import pytest
from ortools.sat.python import cp_model

from workcell.plan import Plan, bind_batch
from workcell.planners.cpsat import ReplanState, plan_cpsat, serial_hint
from workcell.validate import validate_plan


def test_deterministic_repeatable(timed_batch):
    b = timed_batch(3)
    p1 = plan_cpsat(b, time_limit=5, hint=False, deterministic=True)
    p2 = plan_cpsat(b, time_limit=5, hint=False, deterministic=True)
    assert p1.start == p2.start and p1.bindings == p2.bindings and validate_plan(p1, b) == []


def test_replan_respects_fixed_and_now(timed_batch):
    b = timed_batch(3); p0 = plan_cpsat(b, time_limit=5, deterministic=True)
    first = sorted(p0.start, key=p0.start.get)[:4]
    # they each ran 7 s long; each overrun pushes the later ones back (as execution would),
    # so the fixed past stays lock-consistent
    fixed = {bid: (p0.start[bid] + 7 * i, p0.end(bid) + 7 * (i + 1)) for i, bid in enumerate(first)}
    now = max(e for _, e in fixed.values())
    bind = {wf: dict(v) for wf, v in p0.bindings.items()}
    p = plan_cpsat(b, time_limit=5, deterministic=True,
                   state=ReplanState(now=now, fixed=fixed, bindings=bind))
    assert p.status in ("optimal", "feasible")
    assert all(p.start[bid] == fixed[bid][0] for bid in fixed)
    assert all(p.start[bid] >= now for bid in p.start if bid not in fixed)
    assert p.bindings == bind


def test_running_block_overran(timed_batch):
    b = timed_batch(3); p0 = plan_cpsat(b, time_limit=5, deterministic=True)
    run = next(bid for bid in p0.start if b.block(bid).duration == 4020)
    s = p0.start[run]; now = s + 5000                                        # past its planned end
    done = {bid: (p0.start[bid], p0.end(bid)) for bid in p0.start if p0.end(bid) <= s and bid != run}
    state = ReplanState(now=now, fixed={**done, run: (s, max(now, s + 4020))},
                        bindings={wf: dict(v) for wf, v in p0.bindings.items()})
    p = plan_cpsat(b, time_limit=5, deterministic=True, state=state)
    assert p.status in ("optimal", "feasible")


def test_state_hint_replaces_greedy_hint(timed_batch, monkeypatch):
    b = timed_batch(2); p0 = plan_cpsat(b, time_limit=5, hint=False, deterministic=True)
    def no_greedy(*a, **k):
        raise AssertionError("greedy hint used")
    monkeypatch.setattr("workcell.planners.cpsat.plan_greedy", no_greedy)
    state = ReplanState(now=0, fixed={}, bindings={}, hint=dict(p0.start))
    p = plan_cpsat(b, time_limit=5, deterministic=True, state=state)
    assert p.status in ("optimal", "feasible") and validate_plan(p, b) == []


_SCRIPT = """
import json, sys
sys.path.insert(0, "tests")
from conftest import EXAMPLES, SMALL
from workcell.compiler import compile_batch
from workcell.config import parse_workcell
from workcell.dsl import load_batch
from workcell.planners.cpsat import plan_cpsat
b = compile_batch(parse_workcell(SMALL), load_batch(EXAMPLES / "pcr_live.py")[0][:3])
p = plan_cpsat(b, time_limit=5, deterministic=True, hint="serial")
print(json.dumps(sorted(p.start.items())))
print(json.dumps(p.bindings, sort_keys=True))
"""


def test_deterministic_across_processes():
    """Same inputs give the same plan even under different string-hash seeds (ruling R2)."""
    root = Path(__file__).resolve().parent.parent
    outs = [subprocess.run([sys.executable, "-c", _SCRIPT], cwd=root, check=True,
                           capture_output=True, text=True,
                           env={**os.environ, "PYTHONHASHSEED": seed}).stdout
            for seed in ("1", "2")]
    assert outs[0] and outs[0] == outs[1]


def _serial_plan(b):
    starts, bindings = serial_hint(b)
    return Plan("serial", bind_batch(b, bindings), starts, bindings, "complete")


@pytest.mark.parametrize("n", [1, 10])
def test_serial_hint_is_a_valid_plan(timed_batch, n):
    b = timed_batch(n); p = _serial_plan(b)
    assert set(p.start) == {x.id for x in b.all_blocks()}
    assert validate_plan(p, b) == []
    assert p.bindings == {w.id: {v: c[0] for v, c in w.variables.items()} for w in b.workflows}


@pytest.mark.slow
def test_serial_hint_warm_start_improves_ten_workflows(timed_batch):
    """Ruling R5: 8 workers / 30 det-s from the serial hint beats the serial plan (~30 s)."""
    b = timed_batch(10)
    p = plan_cpsat(b, time_limit=30, deterministic=True, hint="serial")
    assert p.status in ("optimal", "feasible") and validate_plan(p, b) == []
    assert p.makespan < _serial_plan(b).makespan


def _capture_hints(monkeypatch):
    """Record the solution hint of every model solved: {variable name: hinted value}."""
    seen = []
    real = cp_model.CpSolver.Solve

    def solve(self, model, *a, **k):
        proto = model.Proto()
        h = proto.solution_hint
        seen.append({proto.variables[v].name: x for v, x in zip(h.vars, h.values)})
        return real(self, model, *a, **k)
    monkeypatch.setattr(cp_model.CpSolver, "Solve", solve)
    return seen


def _other_binding(b, bindings):
    """bindings with one var of the first multi-candidate var switched to another candidate."""
    for wf in b.workflows:
        for var, cands in wf.variables.items():
            if len(cands) > 1:
                alt = next(c for c in cands if c != bindings[wf.id][var])
                return wf.id, var, alt
    raise AssertionError("no multi-candidate var")


def test_state_hint_bindings_applied(timed_batch, monkeypatch):
    b = timed_batch(2); p0 = plan_cpsat(b, time_limit=5, hint="serial", deterministic=True)
    wf, var, alt = _other_binding(b, p0.bindings)
    hb = {w: dict(v) for w, v in p0.bindings.items()}; hb[wf][var] = alt   # differs from p0
    seen = _capture_hints(monkeypatch)
    state = ReplanState(now=0, fixed={}, bindings={}, hint=dict(p0.start), hint_bindings=hb)
    p = plan_cpsat(b, time_limit=5, deterministic=True, state=state)
    assert p.status in ("optimal", "feasible") and validate_plan(p, b) == []
    cands = next(w for w in b.workflows if w.id == wf).variables[var]
    assert {c: seen[0][f"x[{wf},{var},{c}]"] for c in cands} == {c: int(c == alt) for c in cands}


def test_hint_modes(timed_batch, monkeypatch):
    b = timed_batch(2); starts, bindings = serial_hint(b)
    seen = _capture_hints(monkeypatch)
    for h in ("serial", None, False):
        plan_cpsat(b, time_limit=2, hint=h, deterministic=True)
    serial, none, false = seen
    assert {k: v for k, v in serial.items() if k.startswith("s[")} == \
        {f"s[{bid}]": s for bid, s in starts.items()}
    assert all(serial[f"x[{w},{v},{c}]"] == int(bindings[w][v] == c)
               for w, vs in bindings.items() for v in vs
               for c in next(x for x in b.workflows if x.id == w).variables[v])
    assert none == {} and false == {}
