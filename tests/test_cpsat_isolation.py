"""Ruling R7: deterministic CP-SAT solves run in a child process, retried if it crashes."""
import sys

from workcell.planners import cpsat
from workcell.planners.cpsat import plan_cpsat
from workcell.validate import validate_plan


def _in_process(monkeypatch):
    """Make `plan_cpsat` solve deterministic models in this process instead of a child."""
    real = cpsat._solve
    monkeypatch.setattr(cpsat, "_solve", lambda m, solver, isolate: real(m, solver, False))


def test_isolated_matches_in_process(timed_batch, monkeypatch):
    b = timed_batch(3)
    isolated = plan_cpsat(b, time_limit=5, deterministic=True, hint="serial")
    _in_process(monkeypatch)
    local = plan_cpsat(b, time_limit=5, deterministic=True, hint="serial")
    assert isolated.status in ("optimal", "feasible") and validate_plan(isolated, b) == []
    assert isolated.start == local.start and isolated.bindings == local.bindings
    assert isolated.solver_status == local.solver_status


# child that aborts until it has done so CRASH_TIMES times (counted as files in CRASH_DIR)
_ABORTING_CHILD = """
import os, pathlib
crashes = pathlib.Path(os.environ["CRASH_DIR"])
done = len(list(crashes.iterdir()))
if done < int(os.environ["CRASH_TIMES"]):
    (crashes / str(done)).touch()
    os.abort()
from workcell.planners.cpsat import _child_main
_child_main()
"""


def _crash(monkeypatch, tmp_path, times):
    monkeypatch.setenv("CRASH_DIR", str(tmp_path))
    monkeypatch.setenv("CRASH_TIMES", str(times))
    monkeypatch.setattr(cpsat, "_CHILD_CMD", [sys.executable, "-c", _ABORTING_CHILD])


def test_child_crash_is_retried(timed_batch, monkeypatch, tmp_path):
    b = timed_batch(2)
    expected = plan_cpsat(b, time_limit=5, deterministic=True, hint="serial")
    _crash(monkeypatch, tmp_path, 2)
    p = plan_cpsat(b, time_limit=5, deterministic=True, hint="serial")
    assert len(list(tmp_path.iterdir())) == 2          # two aborted attempts, third solved
    assert p.start == expected.start and p.bindings == expected.bindings
    assert p.status == expected.status and validate_plan(p, b) == []


def test_all_attempts_crash(timed_batch, monkeypatch, tmp_path):
    b = timed_batch(2)
    _crash(monkeypatch, tmp_path, 99)
    p = plan_cpsat(b, time_limit=5, deterministic=True, hint="serial")
    assert len(list(tmp_path.iterdir())) == 3
    assert (p.status, p.solver_status) == ("no_solution", "CRASHED")
    assert p.blocks == {} and p.start == {} and p.bindings == {}


def test_non_deterministic_stays_in_process(timed_batch, monkeypatch):
    def no_child(*a, **k):
        raise AssertionError("child process used")
    monkeypatch.setattr(cpsat, "_solve_isolated", no_child)
    b = timed_batch(2)
    p = plan_cpsat(b, time_limit=2, hint=False)
    assert p.status in ("optimal", "feasible") and validate_plan(p, b) == []
