import dataclasses
import json
import re

import pytest

from workcell.config import FailureSpec
from workcell.plan import Plan
from workcell.report import build_report, report_data, write_report
from workcell.run import RunResult, run_session
from workcell.sim.executor import execute
from workcell.stats import compute_stats


@pytest.fixture
def session(workcell_small, pcr_specs):
    return run_session(workcell_small, pcr_specs(2), ["greedy", "greedy_hotel", "cpsat"], time_limit=10,
                       workcell_source="instruments: []", batch_source="def batch(): ...")


def data_of(html):
    m = re.search(r'<script id="workcell-data" type="application/json">(.*?)</script>', html, re.S)
    return json.loads(m.group(1).replace("<\\/", "</"))


def test_report_data_contract(session):
    d = data_of(build_report(session))
    assert [r["mode"] for r in d["runs"]] == ["greedy:round_robin", "greedy_hotel:round_robin", "cpsat:makespan"]
    assert d["runs"][0]["deadlock"]["time"] == 7 and d["runs"][0]["deadlock"]["waits"]
    assert any(b["inserted"] for b in d["runs"][1]["blocks"])
    assert len(d["compiled"][0]["blocks"]) == 16
    assert "highlight" in d["sources"]["workflows"]


def test_self_contained(session):
    html = build_report(session)
    assert not re.search(r'(src|href)\s*=\s*["\']https?://', html)


def test_alarm_and_no_solution_render(workcell_small, pcr_specs):
    wc = dataclasses.replace(workcell_small, failures=[FailureSpec(op="scan", plate="S0002", kind="barcode_mismatch")])
    sess = run_session(wc, pcr_specs(2), ["greedy_hotel"], time_limit=5)
    empty = Plan("cpsat:makespan", {}, {}, {}, "no_solution"); tr = execute(empty, sess.batch)
    sess.runs.append(RunResult("cpsat:makespan", empty, tr, compute_stats(empty, tr, sess.batch), []))
    d = data_of(build_report(sess))
    assert d["runs"][0]["alarm"]["block"].startswith("pcr-1")
    assert d["runs"][1]["status"] == "no_solution" and d["runs"][1]["blocks"] == []


def test_run_blocks_follow_trace_spans(session):
    d = report_data(session)
    for run, res in zip(d["runs"], session.runs):
        assert {b["id"]: (b["start"], b["end"]) for b in run["blocks"]} == \
            {k: tuple(v) for k, v in res.trace.spans.items()}
    json.dumps(d)  # fully JSON-serializable (sets, tuples converted)


def test_data_escapes_script_close(session, monkeypatch):
    import workcell.report.build as build
    monkeypatch.setattr(build, "report_data", lambda s: {"x": "</script><b>"})
    html = build_report(session)
    assert html.count("</script>") == html.count("<script")
    assert data_of(html) == {"x": "</script><b>"}


def test_write_report(session, tmp_path):
    p = write_report(session, tmp_path / "sub" / "r.html")
    assert p.exists() and p.read_text().startswith("<!doctype html>")
