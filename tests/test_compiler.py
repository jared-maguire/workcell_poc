import pytest
from conftest import SMALL

from workcell.compiler import CompileError, compile_batch
from workcell.config import parse_workcell
from workcell.dsl import workflow
from workcell.ir import PlateMove, resolve
from workcell.library import pcr


def test_pcr_block_kinds(compiled_small):
    wf = compiled_small(1).workflows[0]
    assert wf.id == "pcr-0" and wf.barcodes["p2"] == "E0001"
    assert [b.kind for b in wf.blocks] == (
        ["fetch", "scan", "move"] * 2 + ["op"] + ["fetch", "scan", "move", "op"]
        + ["move", "op"] + ["return"] * 3)
    assert wf.variables == {"?tc0": ["tc1", "tc2"]}
    assert wf.owned == {"S0001", "E0001"} and wf.borrowed == {"P0001"}


def test_fetch_instructions(compiled_small):
    b = compiled_small(1).block("pcr-0/00")
    assert [(i.instrument, i.op) for i in b.instructions] == [
        ("store1", "check_status"), ("arm1", "check_status"), ("scan1", "check_status"),
        ("store1", "retrieve"), ("arm1", "move")]
    assert b.instructions[-1].args == {"plate": "S0001", "src": "store1", "dst": "scan1"}
    assert b.moves == [PlateMove("S0001", "store1", "scan1")] and b.duration == 2


def test_tc_blocks_use_var_and_doors(compiled_small):
    batch = compiled_small(1)
    load, run, ret_e = batch.block("pcr-0/11"), batch.block("pcr-0/12"), batch.block("pcr-0/15")
    assert [(i.instrument, i.op) for i in load.instructions][3:] == [
        ("?tc0", "open_door"), ("arm1", "move"), ("?tc0", "close_door")]
    assert load.moves == [PlateMove("E0001", "stamp1.dst", "?tc0")]
    assert run.instructions[-1].op == "run_program" and run.instruments == {"?tc0"}
    assert ret_e.duration == 4 and ret_e.instructions[-1].op == "store"


def test_dependencies(compiled_small):
    blk = compiled_small(1).block
    assert set(blk("pcr-0/06").deps) == {"pcr-0/02", "pcr-0/05"}
    assert "pcr-0/06" in blk("pcr-0/07").deps          # primer fetch waits for stamp a
    assert "pcr-0/06" in blk("pcr-0/13").deps          # return S after stamp a
    assert "pcr-0/12" in blk("pcr-0/15").deps          # return E after cycling


def test_bind_resolves_vars(compiled_small):
    b = compiled_small(1).block("pcr-0/11").bind({"?tc0": "tc2"})
    assert "tc2" in b.instruments and b.moves[0].dst == "tc2"
    with pytest.raises(KeyError):
        resolve("?tc0", {})


def test_shared_primer_and_empty_allocation(compiled_small):
    batch = compiled_small(3)
    assert [w.barcodes["p2"] for w in batch.workflows] == ["E0001", "E0002", "E0003"]
    assert batch.workflows[0].borrowed == batch.workflows[2].borrowed == {"P0001"}
    assert batch.workflows[0].borrow_span("P0001") == ("pcr-0/07", "pcr-0/14")


@pytest.mark.parametrize("specs, msg", [
    (lambda: [pcr(sample="S9999", primer="P0001")], "unknown plate barcode 'S9999'"),
    (lambda: [pcr(sample="S0001", primer="S0001")], "S0001"),
    (lambda: [pcr(sample="S0001", primer="P0001"), pcr(sample="S0001", primer="P0001")], "owned by pcr-0"),
])
def test_compile_errors(workcell_small, specs, msg):
    with pytest.raises(CompileError, match=msg):
        compile_batch(workcell_small, specs())


def test_empty_pool_exhausted(workcell_small):
    wc = parse_workcell({**SMALL, "plates": SMALL["plates"][:2] + [
        {"generate": "empty", "prefix": "E", "count": 2, "location": "store1"}]})
    with pytest.raises(CompileError, match="no unused plates of type 'empty' left \\(needed by pcr-2\\)"):
        compile_batch(wc, [pcr(sample=f"S{i+1:04d}", primer="P0001") for i in range(3)])


def test_cycle_rejected(workcell_small):
    @workflow
    def cyc(w):
        p = w.own("S0001"); q = w.own("S0002")
        a = w.thermocycle(p, program=[]); w.thermocycle(q, program=[], after=a)
    spec = cyc(); spec.ops[0].after = (spec.ops[1].id,)   # force a cycle
    with pytest.raises(CompileError, match="cyc-0"):
        compile_batch(workcell_small, [spec])
