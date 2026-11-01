"""The 3D view's pure model (workcell/report/lab3d_model.js), run under node."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from conftest import EXAMPLES
from workcell.config import load_workcell
from workcell.dsl import load_batch
from workcell.live.session import run_live
from workcell.report import report_data
from workcell.run import run_session

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
MODEL = Path(__file__).resolve().parent.parent / "workcell" / "report" / "lab3d_model.js"


def js(expr, data, tmp_path):
    """Evaluate the JS expression `expr` with M = the model and D = `data`; returns its JSON."""
    f = tmp_path / "d.json"
    f.write_text(json.dumps(data))
    code = (f"const M = require({json.dumps(str(MODEL))});"
            f"const D = JSON.parse(require('fs').readFileSync({json.dumps(str(f))}, 'utf8'));"
            f"const busy = s => [...s.busy].sort();"
            f"console.log(JSON.stringify((() => {expr})()));")
    out = subprocess.run(["node", "-e", code], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


@pytest.fixture(scope="module")
def libprep():
    wc = load_workcell(EXAMPLES / "libprep_2arm.yaml")
    specs, _ = load_batch(EXAMPLES / "libprep_capture.py")
    session = run_live(wc, specs, seed=0).session
    return report_data(session), session.runs[0].trace


TL = "M.timeline(D.runs[0], D.instruments)"


def test_timeline_matches_trace(libprep, tmp_path):
    d, trace = libprep
    got = js(f"{TL}.instr.map(i => [i.t0, i.instrument, i.op])", d, tmp_path)
    want = [[e.time, e.instrument, e.op] for e in trace.events]
    assert sorted(map(tuple, got)) == sorted(map(tuple, want))
    assert js(f"{TL}.moves.length", d, tmp_path) == len(trace.moves)


def test_state_carried_plate(libprep, tmp_path):
    d, _ = libprep
    m, before, mid, after = js(f"""{{
        const tl = {TL}, m = tl.moves[0], L = D.runs[0].initial_locations;
        return [m, M.stateAt(tl, L, m.t0 - 1).plates[m.plate],
                M.stateAt(tl, L, (m.t0 + m.t1) / 2).plates[m.plate],
                M.stateAt(tl, L, m.t1).plates[m.plate]];
    }}""", d, tmp_path)
    if m["t0"] >= 1:
        assert before == {"at": m["src"]}
    assert mid["arm"] == m["arm"] and 0 < mid["u"] < 1
    assert after == {"at": m["dst"]}
    assert js(f"{{ const tl = {TL}, m = tl.moves[0]; return M.stateAt(tl, D.runs[0].initial_locations, m.t1).rest[m.arm] === m.dst; }}", d, tmp_path)


def test_state_busy(libprep, tmp_path):
    d, _ = libprep
    first, passive_busy, zero = js(f"""{{
        const tl = {TL}, L = D.runs[0].initial_locations;
        const bc = tl.instr.find(i => i.op === "bead_cleanup");
        const types = Object.fromEntries(D.instruments.map(i => [i.id, i.type]));
        const passive = new Set(), zero = new Set();
        for (const i of tl.instr) {{
            for (const x of busy(M.stateAt(tl, L, (i.t0 + i.t1) / 2)))
                if (["storage", "hotel", "handoff", "arm"].includes(types[x])) passive.add(x);
            if (i.t1 === i.t0 && busy(M.stateAt(tl, L, i.t0)).includes(i.instrument)
                && !tl.instr.some(j => j !== i && j.instrument === i.instrument && j.t1 > j.t0
                                   && j.t0 <= i.t0 && i.t0 < j.t1)) zero.add(i.instrument);
        }}
        return [busy(M.stateAt(tl, L, (bc.t0 + bc.t1) / 2)).includes(bc.instrument), [...passive], [...zero]];
    }}""", d, tmp_path)
    assert first and passive_busy == [] and zero == []


def test_state_nest_suffix(workcell_small, pcr_specs, tmp_path):
    d = report_data(run_session(workcell_small, pcr_specs(1), ["jit"], time_limit=5))
    at, owner = js(f"""{{
        const tl = {TL}, m = tl.moves.find(m => m.dst === "stamp1.dst");
        return [M.stateAt(tl, D.runs[0].initial_locations, m.t1).plates[m.plate],
                M.nestOwner(D.instruments)["stamp1.dst"]];
    }}""", d, tmp_path)
    assert at == {"at": "stamp1.dst"} and owner == "stamp1"


def test_state_alarm_truncates(tmp_path):
    run = {"alarm": {"time": 5}, "blocks": [{"id": "w/00", "start": 0, "instructions": [
        {"instrument": "tc1", "op": "run", "args": {}, "duration": 10},
        {"instrument": "tc1", "op": "open", "args": {}, "duration": 10}]}]}
    d = {"runs": [run], "instruments": [{"id": "tc1", "type": "thermocycler", "nests": []}]}
    assert js(f"{TL}.instr.map(i => [i.t0, i.t1, i.op])", d, tmp_path) == [[0, 5, "run"]]


def test_state_empty_run(tmp_path):
    d = {"runs": [{"alarm": None, "blocks": [], "initial_locations": {"P1": "store1"}}],
         "instruments": [{"id": "store1", "type": "storage", "nests": []}]}
    tl, st = js(f"[{TL}.T, M.stateAt({TL}, D.runs[0].initial_locations, 100)]", d, tmp_path)
    assert tl == 0 and st == {"plates": {"P1": {"at": "store1"}}, "arms": {}, "rest": {}, "busy": {}}


def test_state_is_pure(libprep, tmp_path):
    d, _ = libprep
    a, b = js(f"""{{
        const tl = {TL}, L = D.runs[0].initial_locations;
        const fresh = M.stateAt(tl, L, 500);
        M.stateAt(tl, L, 900);
        const again = M.stateAt(tl, L, 500);
        return [[fresh.plates, busy(fresh)], [again.plates, busy(again)]];
    }}""", d, tmp_path)
    assert a == b


def _layout_check(insts, tmp_path):
    return js("""{
        const L = M.layout(D);
        const reach = {};
        for (const a of D.filter(i => i.type === "arm"))
            reach[a.id] = a.reach || D.filter(i => i.type !== "arm").map(i => i.id);
        const far = [];
        for (const [a, ids] of Object.entries(reach)) for (const id of ids) {
            const [x, z] = L.pos[id], [ax, az] = L.arms[a].at;
            if (Math.hypot(x - ax, z - az) > 2 * L.arms[a].link + 1e-9) far.push([a, id]);
        }
        const ids = Object.keys(L.pos); let close = Infinity;
        for (let i = 0; i < ids.length; i++) for (let j = i + 1; j < ids.length; j++)
            close = Math.min(close, Math.hypot(L.pos[ids[i]][0] - L.pos[ids[j]][0], L.pos[ids[i]][1] - L.pos[ids[j]][1]));
        return { L, far, close };
    }""", insts, tmp_path)


def test_layout_two_arm(tmp_path):
    from workcell.report.build import instruments_data
    insts = instruments_data(load_workcell(EXAMPLES / "libprep_2arm.yaml"))
    r = _layout_check(insts, tmp_path)
    L = r["L"]
    assert set(L["pos"]) == {i["id"] for i in insts if i["type"] != "arm"}
    dist = lambda a: ((L["pos"]["ho1"][0] - L["arms"][a]["at"][0]) ** 2 + (L["pos"]["ho1"][1] - L["arms"][a]["at"][1]) ** 2) ** 0.5
    assert abs(dist("arm1") - dist("arm2")) < 0.01
    assert r["far"] == [] and r["close"] >= 0.9
    assert len(L["groups"]) == 1


def test_layout_single_arm(workcell_small, tmp_path):
    from workcell.report.build import instruments_data
    insts = instruments_data(workcell_small)
    r = _layout_check(insts, tmp_path)
    assert set(r["L"]["pos"]) == {i["id"] for i in insts if i["type"] != "arm"}
    assert r["far"] == [] and r["close"] >= 0.9


def test_layout_separate_lines(tmp_path):
    from workcell.report.build import instruments_data
    insts = instruments_data(load_workcell(EXAMPLES / "libprep_10k.yaml"))
    r = _layout_check(insts, tmp_path)
    L = r["L"]
    assert len(L["groups"]) == 4 and r["far"] == [] and r["close"] >= 0.9
    boxes = []
    for g in L["groups"]:
        pts = [L["pos"][i] for i in g if i in L["pos"]] + [L["arms"][i]["at"] for i in g if i in L["arms"]]
        boxes.append((min(p[0] for p in pts), max(p[0] for p in pts), min(p[1] for p in pts), max(p[1] for p in pts)))
    for i, a in enumerate(boxes):
        for b in boxes[i + 1:]:
            assert a[1] < b[0] or b[1] < a[0] or a[3] < b[2] or b[3] < a[2]
