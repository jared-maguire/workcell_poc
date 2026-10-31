import pytest
from conftest import SMALL

from workcell.compiler import CompileError, compile_batch
from workcell.config import parse_workcell
from workcell.dsl import workflow

LAB = parse_workcell({**SMALL, "instruments": SMALL["instruments"] + [
    {"id": "lh1", "type": "liquid_handler"}, {"id": "hs1", "type": "heated_shaker"},
    {"id": "seal1", "type": "plate_sealer"}]})
TIMES = {"arm.move": 10, "check_status": 0, "retrieve": 60, "store": 60, "scan": 4}


def compile_one(fn):
    return compile_batch(LAB, [fn()]).workflows[0]


def test_step_compiles_to_op_block():
    @workflow(times=TIMES)
    def f(w):
        p = w.own("S0001")
        w.step("heated_shaker", "shake", p, seconds=60)
    wf = compile_one(f)
    (op,) = [b for b in wf.blocks if b.kind == "op"]
    assert [(i.instrument, i.op, i.args, i.duration) for i in op.instructions] == [
        ("hs1", "check_status", {}, 0), ("hs1", "shake", {"plates": ["S0001"]}, 60)]
    assert op.label == "shake S0001"


def test_lh_two_plate_step_nests():
    @workflow(times=TIMES)
    def f(w):
        a, b = w.own("S0001"), w.own(w.new_plate("empty"))
        w.step("liquid_handler", "pool", a, b, seconds=900)
    wf = compile_one(f)
    dsts = {m.plate: m.dst for blk in wf.blocks for m in blk.moves if blk.kind == "move"}
    assert dsts == {"S0001": "lh1.src", "E0001": "lh1.dst"}


def test_consecutive_lh_steps_no_move():
    @workflow(times=TIMES)
    def f(w):
        a, b = w.own("S0001"), w.own(w.new_plate("empty"))
        x = w.step("liquid_handler", "pool", a, b, seconds=900)
        w.step("liquid_handler", "wash", b, seconds=300, after=x)
    wf = compile_one(f)
    kinds = [blk.kind for blk in wf.blocks]
    i = [blk.label for blk in wf.blocks].index("pool S0001 E0001")
    assert kinds[i + 1] == "op" and wf.blocks[i + 1].label == "wash E0001"


def test_step_two_plates_needs_two_positions():
    @workflow(times=TIMES)
    def f(w):
        a, b = w.own("S0001"), w.own("S0002")
        w.step("heated_shaker", "x", a, b, seconds=1)
    with pytest.raises(ValueError, match="two plates"):
        f()


def test_step_unknown_type():
    @workflow(times=TIMES)
    def f(w):
        w.step("sequencer", "run", w.own("S0001"), seconds=1)
    with pytest.raises(CompileError, match="sequencer"):
        compile_one(f)


def test_step_requires_seconds():
    @workflow(times=TIMES)
    def f(w):
        w.step("heated_shaker", "shake", w.own("S0001"))
    with pytest.raises(TypeError):
        f()


def test_consecutive_same_type_steps_stay_put():
    """Two shaker steps in a row on one plate use the same shaker: no move between them."""
    wc = parse_workcell({**SMALL, "instruments": SMALL["instruments"] + [
        {"id": "hs1", "type": "heated_shaker"}, {"id": "hs2", "type": "heated_shaker"}]})

    @workflow(times=TIMES)
    def f(w):
        p = w.own("S0001")
        w.step("heated_shaker", "capture", p, seconds=2700)
        w.step("heated_shaker", "heated_wash", p, seconds=600)
    wf = compile_batch(wc, [f()]).workflows[0]
    labels = [blk.label for blk in wf.blocks]
    i = labels.index("capture S0001")
    assert labels[i + 1] == "heated_wash S0001"
    assert wf.blocks[i].instructions[1].instrument == wf.blocks[i + 1].instructions[1].instrument
