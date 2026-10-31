"""Ruling R7: deterministic CP-SAT solves run in a child process, retried if it crashes."""
import sys

import pytest

from workcell.planners import cpsat
from workcell.planners.cpsat import plan_cpsat
from workcell.validate import validate_plan


def _in_process(monkeypatch):
    """Make `plan_cpsat` solve deterministic models in this process instead of a child."""
    real = cpsat._solve
    monkeypatch.setattr(cpsat, "_solve", lambda m, solver, isolate: real(m, solver, False))


def test_isolated_matches_in_process(timed_batch, monkeypatch):
    b = timed_batch(3)
    isolated = plan_cpsat(b, time_limit=0.3, deterministic=True, hint="serial")
    _in_process(monkeypatch)
    local = plan_cpsat(b, time_limit=0.3, deterministic=True, hint="serial")
    assert isolated.status in ("optimal", "feasible") and validate_plan(isolated, b) == []
    assert isolated.start == local.start and isolated.bindings == local.bindings
    assert isolated.solver_status == local.solver_status


# A child that fails (FAILURE code) on each of its first CRASH_TIMES attempts, counted as
# files in CRASH_DIR, then solves normally.
_FAILING_CHILD = """
import os, pathlib, resource, sys, time
crashes = pathlib.Path(os.environ["CRASH_DIR"])
done = len(list(crashes.iterdir()))
if done < int(os.environ["CRASH_TIMES"]):
    (crashes / str(done)).touch()
    {failure}
from workcell.planners.cpsat import _worker_main
_worker_main()
"""
_ABORT = "resource.setrlimit(resource.RLIMIT_CORE, (0, 0)); sys.stderr.write('heap!'); sys.stderr.flush(); os.abort()"
_EXIT_1 = "sys.stderr.write('ImportError: no module named nonsense'); sys.exit(1)"
_HANG = "time.sleep(60)"


@pytest.fixture(autouse=True)
def _fresh_worker():
    """Each test starts and ends without a solver process (a leftover one started with the
    same failing command would skip the injected crashes)."""
    cpsat._drop_worker()
    yield
    cpsat._drop_worker()


def _crash(monkeypatch, tmp_path, times, failure=_ABORT):
    monkeypatch.setenv("CRASH_DIR", str(tmp_path))
    monkeypatch.setenv("CRASH_TIMES", str(times))
    monkeypatch.setattr(cpsat, "_CHILD_CMD",
                        [sys.executable, "-c", _FAILING_CHILD.format(failure=failure)])


def test_child_crash_is_retried(timed_batch, monkeypatch, tmp_path):
    b = timed_batch(2)
    expected = plan_cpsat(b, time_limit=0.3, deterministic=True, hint="serial")
    _crash(monkeypatch, tmp_path, 2)
    p = plan_cpsat(b, time_limit=0.3, deterministic=True, hint="serial")
    assert len(list(tmp_path.iterdir())) == 2          # two aborted attempts, third solved
    assert p.start == expected.start and p.bindings == expected.bindings
    assert p.status == expected.status and validate_plan(p, b) == []


def test_all_attempts_crash(timed_batch, monkeypatch, tmp_path):
    b = timed_batch(2)
    _crash(monkeypatch, tmp_path, 99)
    with pytest.warns(RuntimeWarning, match="heap!"):
        p = plan_cpsat(b, time_limit=0.3, deterministic=True, hint="serial")
    assert len(list(tmp_path.iterdir())) == 3
    assert (p.status, p.solver_status) == ("no_solution", "CRASHED")
    assert p.blocks == {} and p.start == {} and p.bindings == {}


def test_child_error_raises_without_retry(timed_batch, monkeypatch, tmp_path):
    """A non-zero exit that is not a signal is a deterministic Python-level failure."""
    _crash(monkeypatch, tmp_path, 99, failure=_EXIT_1)
    with pytest.raises(RuntimeError, match="no module named nonsense"):
        plan_cpsat(timed_batch(2), time_limit=0.3, deterministic=True, hint="serial")
    assert len(list(tmp_path.iterdir())) == 1


@pytest.mark.slow
def test_child_timeout_is_retried(timed_batch, monkeypatch, tmp_path):
    b = timed_batch(2)
    expected = plan_cpsat(b, time_limit=0.3, deterministic=True, hint="serial")
    _crash(monkeypatch, tmp_path, 1, failure=_HANG)
    monkeypatch.setattr(cpsat, "_child_timeout", lambda time_limit: 15.0)   # roomy: slow tests run in parallel
    p = plan_cpsat(b, time_limit=0.3, deterministic=True, hint="serial")
    assert len(list(tmp_path.iterdir())) == 1          # first attempt hung, second solved
    assert p.start == expected.start and p.bindings == expected.bindings


def test_child_timeout_default():
    assert cpsat._child_timeout(2.0) == 300 and cpsat._child_timeout(30.0) == 1800


def test_corrupt_child_result_is_a_failed_attempt(timed_batch, monkeypatch):
    monkeypatch.setattr(cpsat, "_CHILD_CMD", [sys.executable, "-c", "print('not a pickle')"])
    with pytest.warns(RuntimeWarning):
        p = plan_cpsat(timed_batch(1), time_limit=0.3, deterministic=True, hint="serial")
    assert (p.status, p.solver_status) == ("no_solution", "CRASHED")


def test_model_invalid_raises_through_child(timed_batch, monkeypatch):
    real = cpsat._Model.objective

    def objective(self, objective):
        real(self, objective)
        self.m.Proto().merge_text_format('variables { name: "bad" domain: 5 domain: 1 }')
    monkeypatch.setattr(cpsat._Model, "objective", objective)
    with pytest.raises(RuntimeError, match="MODEL_INVALID"):
        plan_cpsat(timed_batch(1), time_limit=0.3, deterministic=True, hint=None)


def test_non_deterministic_stays_in_process(timed_batch, monkeypatch):
    def no_child(*a, **k):
        raise AssertionError("child process used")
    monkeypatch.setattr(cpsat, "_solve_isolated", no_child)
    b = timed_batch(2)
    p = plan_cpsat(b, time_limit=2, hint=False)
    assert p.status in ("optimal", "feasible") and validate_plan(p, b) == []


def test_all_attempts_timing_out_is_an_error(timed_batch, monkeypatch, tmp_path):
    """A solve that keeps timing out must not silently become "no_solution": that would make
    a live run's outcome depend on machine load (breaking same-seed reproducibility)."""
    _crash(monkeypatch, tmp_path, 99, failure=_HANG)
    monkeypatch.setattr(cpsat, "_child_timeout", lambda time_limit: 0.5)
    with pytest.raises(RuntimeError, match="timed out"):
        plan_cpsat(timed_batch(1), time_limit=0.3, deterministic=True, hint="serial")


def test_child_timeout_is_generous():
    assert cpsat._child_timeout(2.0) >= 300 and cpsat._child_timeout(30.0) >= 1800


def test_worker_is_reused(timed_batch):
    """One long-lived solver process serves consecutive solves (no per-solve startup)."""
    b = timed_batch(1)
    plan_cpsat(b, time_limit=0.1, deterministic=True, hint="serial")
    pid = cpsat._WORKER.proc.pid
    plan_cpsat(b, time_limit=0.1, deterministic=True, hint="serial")
    assert cpsat._WORKER.proc.pid == pid
