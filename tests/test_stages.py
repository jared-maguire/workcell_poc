"""Stages: in a workcell whose instruments carry `stage:` labels, a workflow op marked with
`w.stage(name)` binds only to instruments of that stage, and `store` goes to that stage's
storage. Workcells without stages ignore the marks."""
import pytest

from workcell.compiler import CompileError, compile_batch
from workcell.config import ConfigError, parse_workcell
from workcell.dsl import workflow
from workcell.planners.dispatch import plan_dispatch
from workcell.sim.executor import execute
from workcell.validate import validate_plan

TIMES = {"arm.move": 10, "retrieve": 60, "store": 60, "scan": 4, "check_status": 0}


def staged(stages=True):
    """Two stations in a row: a (storage, scanner, two liquid handlers) and b (storage,
    scanner, one liquid handler), joined by a handoff."""
    def s(name):
        return {"stage": name} if stages else {}
    inst = [
        {"id": "arm_a", "type": "arm", "reach": ["store_a", "scan_a", "lh_a1", "lh_a2", "ho"]},
        {"id": "arm_b", "type": "arm", "reach": ["store_b", "scan_b", "lh_b", "ho"]},
        {"id": "ho", "type": "handoff", "slots": 2},
        {"id": "store_a", "type": "storage", "slots": 50, **s("a")},
        {"id": "scan_a", "type": "barcode_scanner", **s("a")},
        {"id": "lh_a1", "type": "liquid_handler", **s("a")},
        {"id": "lh_a2", "type": "liquid_handler", **s("a")},
        {"id": "store_b", "type": "storage", "slots": 50, **s("b")},
        {"id": "scan_b", "type": "barcode_scanner", **s("b")},
        {"id": "lh_b", "type": "liquid_handler", **s("b")},
    ]
    plates = [{"barcode": f"P{i}", "type": "sample", "location": "store_a"} for i in range(3)]
    return parse_workcell({"instruments": inst, "plates": plates,
                           "durations": {"default": 1, "check_status": 0}})


@workflow(times=TIMES)
def two_stations(w, i):
    p = w.own(f"P{i}")
    w.stage("a")
    w.step("liquid_handler", "mix", p, seconds=100)
    w.stage("b")
    w.step("liquid_handler", "wash", p, seconds=100)
    w.store(p)


def bound(b):
    p = plan_dispatch(b)
    assert p.finished and validate_plan(p, b) == []
    tr = execute(p, b)
    assert tr.alarm is None
    return p


def test_stage_parsed():
    wc = staged()
    assert wc.instruments["lh_b"].stage == "b" and wc.instruments["arm_a"].stage is None


def test_stage_must_be_a_string():
    with pytest.raises(ConfigError):
        parse_workcell({"instruments": [{"id": "a", "type": "arm"},
                                        {"id": "s", "type": "storage", "slots": 1, "stage": 3}]})


def test_dsl_records_stage():
    spec = two_stations(0)
    assert [op.stage for op in spec.ops] == ["a", "b", "b"]


def test_steps_bind_within_their_stage():
    b = compile_batch(staged(), [two_stations(i) for i in range(3)])
    p = bound(b)
    for wf in b.workflows:
        assert wf.variables == {"?lh0": ["lh_a1", "lh_a2"]}   # stage b's one is substituted
    assert {binds["?lh0"] for binds in p.bindings.values()} == {"lh_a1", "lh_a2"}


def test_store_goes_to_the_stage_storage():
    b = compile_batch(staged(), [two_stations(0)])
    last = b.workflows[0].blocks[-1]
    assert last.moves[-1].dst == "store_b"


def test_same_type_in_next_stage_moves():
    """A plate on stage a's liquid handler does not stay there for stage b's step."""
    b = compile_batch(staged(), [two_stations(0)])
    ops = [blk for blk in b.workflows[0].blocks if blk.kind == "op"]
    assert len(ops) == 2 and ops[1].instructions[-1].instrument == "lh_b"


def test_no_stages_in_workcell_ignores_marks():
    b = compile_batch(staged(stages=False), [two_stations(0)])
    wf = b.workflows[0]
    # unstaged: the second step stays on the first liquid handler; store returns home
    assert len([blk for blk in wf.blocks if blk.kind == "op"]) == 2
    assert wf.blocks[-1].moves[-1].dst == "store_a"
    assert set(wf.variables["?lh0"]) == {"lh_a1", "lh_a2", "lh_b"}


def test_stage_without_that_instrument_type():
    @workflow(times=TIMES)
    def bad(w):
        p = w.own("P0")
        w.stage("c")
        w.step("liquid_handler", "mix", p, seconds=10)

    with pytest.raises(CompileError, match="stage"):
        compile_batch(staged(), [bad()])


def test_flow_line_example_moves_forward_only():
    """The flow-line example (examples/make_libprep_flow.py): three library preps run clean, and
    every plate move stays at its station or goes to the next one."""
    import sys
    from pathlib import Path

    from workcell.live.session import run_live

    ex = Path(__file__).resolve().parent.parent / "examples"
    sys.path.insert(0, str(ex))
    import make_libprep_flow as flow
    from libprep_capture import libprep_capture

    wc = parse_workcell(flow.workcell(3))
    order = [s for s, _ in flow.STATIONS]
    session = run_live(wc, [libprep_capture(batch=b) for b in (1, 2, 3)], seed=0)
    assert session.errors == [] and len(session.trace.completed) > 0
    hops = {}                     # block -> (first src, last dst) of its arm moves (relays too)
    for e in session.trace.events:
        if e.op == "move":
            hops[e.block] = (hops.get(e.block, (e.args["src"],))[0], e.args["dst"])
    assert any(len({wc.instruments[wc.nest_owner(n)].stage for n in h}) == 2 for h in hops.values())
    for block, (src, dst) in hops.items():
        if "hotel" in {wc.instruments[wc.nest_owner(n)].type for n in (src, dst)}:
            continue              # parking: the zone's own hotel, same arm
        a, b = (order.index(wc.instruments[wc.nest_owner(n)].stage) for n in (src, dst))
        assert 0 <= b - a <= 1, (block, src, dst)
