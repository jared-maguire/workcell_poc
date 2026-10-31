import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _run(*args):
    return subprocess.run([sys.executable, "-m", "workcell", "run", *args],
                          capture_output=True, text=True, cwd=ROOT)


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
    text = re.sub(r"<[^>]+>", "", out.read_text())  # batch source is syntax-highlighted into spans
    assert "python -m workcell scale --workflows 3" in text
