import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

from helpers import data_of

ROOT = Path(__file__).resolve().parent.parent


def _run(*args):
    return subprocess.run([sys.executable, "-m", "workcell", "run", *args],
                          capture_output=True, text=True, cwd=ROOT)


@pytest.mark.slow
def test_cli_end_to_end(tmp_path):
    out = tmp_path / "r.html"
    r = _run("examples/workcell.yaml", "examples/pcr_batch.py",
             "--mode", "greedy", "--mode", "greedy_hotel", "--mode", "cpsat", "--time-limit", "20",
             "--out", str(out))
    assert r.returncode == 0, r.stderr
    assert "deadlock" in r.stdout and "complete" in r.stdout and out.exists()
    assert f"report: {out}" in r.stdout


def test_cli_compile_error(tmp_path):
    bad = tmp_path / "b.py"
    bad.write_text("from workcell.library import pcr\ndef batch():\n    return [pcr(sample='S9999', primer='P0001')]\n")
    r = _run("examples/workcell.yaml", str(bad))
    assert r.returncode == 2 and "S9999" in r.stderr


def test_cli_scale(tmp_path):
    out = tmp_path / "s.html"
    r = subprocess.run([sys.executable, "-m", "workcell", "scale", "--workflows", "3", "--thermocyclers", "4",
                        "--mode", "greedy_hotel", "--out", str(out)],
                       capture_output=True, text=True, cwd=ROOT)
    assert r.returncode == 0, r.stderr
    assert "complete" in r.stdout and "3/3" in r.stdout and out.exists()
    # data JSON escapes `<`, so decode it; the batch source is syntax-highlighted into spans
    text = re.sub(r"<[^>]+>", "", data_of(out.read_text())["sources"]["batch"])
    assert "python -m workcell scale --workflows 3" in text


def test_cli_unknown_mode_rejected_before_planning():
    t0 = time.perf_counter()
    r = _run("examples/workcell.yaml", "examples/pcr_batch.py", "--mode", "cpsat", "--mode", "bogus")
    assert r.returncode == 2 and "bogus" in r.stderr and "greedy_hotel" in r.stderr
    assert "Traceback" not in r.stderr and r.stdout == ""
    assert time.perf_counter() - t0 < 10     # no CP-SAT solve ran first


@pytest.mark.parametrize("make", [
    lambda d: (str(d / "missing.yaml"), "examples/pcr_batch.py"),
    lambda d: ("examples/workcell.yaml", str(d / "missing.py")),
    lambda d: (_write(d / "bad.yaml", "instruments: [unclosed"), "examples/pcr_batch.py"),
    lambda d: ("examples/workcell.yaml", _write(d / "nobatch.py", "x = 1\n")),
])
def test_cli_bad_input_files_exit_2(tmp_path, make):
    r = _run(*make(tmp_path), "--mode", "greedy", "--out", str(tmp_path / "r.html"))
    assert r.returncode == 2 and r.stderr.startswith("error: ") and "Traceback" not in r.stderr


def _write(path, text):
    path.write_text(text)
    return str(path)


@pytest.mark.slow
def test_success_criterion_pcr10():
    from workcell.run import run_files
    sess = run_files(ROOT / "examples/workcell.yaml", ROOT / "examples/pcr_batch.py",
                     ["greedy", "greedy_hotel", "cpsat"], time_limit=20)
    greedy, hotel, cpsat = sess.runs
    assert greedy.stats.status == "deadlock" and greedy.plan.deadlock.cycle
    assert hotel.stats.status == "complete" and hotel.stats.completed == 10 and hotel.errors == []
    assert cpsat.stats.status in ("optimal", "feasible")
    assert cpsat.stats.completed == cpsat.stats.total == 10 and cpsat.errors == []


@pytest.mark.slow
def test_cli_live(tmp_path):
    out = tmp_path / "l.html"
    r = subprocess.run([sys.executable, "-m", "workcell", "live", "examples/workcell.yaml",
                        "examples/pcr_live.py", "--seed", "1", "--time-limit", "3",
                        "--replan-time-limit", "0.5", "--out", str(out)],
                       capture_output=True, text=True, cwd=ROOT)
    assert r.returncode == 0, r.stderr
    assert "re-plans:" in r.stdout and "completed: 10/10" in r.stdout and out.exists()
    assert "idle hold: limit 300s" in r.stdout


def test_cli_live_bad_file():
    r = subprocess.run([sys.executable, "-m", "workcell", "live", "examples/workcell.yaml", "nope.py"],
                       capture_output=True, text=True, cwd=ROOT)
    assert r.returncode == 2
