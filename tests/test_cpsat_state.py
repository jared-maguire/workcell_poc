import os
import subprocess
import sys
from pathlib import Path

from workcell.planners.cpsat import ReplanState, plan_cpsat
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
p = plan_cpsat(b, time_limit=5, deterministic=True, hint=False)
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
