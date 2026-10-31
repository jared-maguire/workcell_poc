from pathlib import Path

from workcell.compiler import compile_batch
from workcell.dsl import load_batch, workflow
from workcell.library import pcr

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


def test_timed_pcr_block_durations(workcell_small):
    specs, _ = load_batch(EXAMPLES / "pcr_live.py")
    b = compile_batch(workcell_small, specs[:1])
    d = {blk.id: blk.duration for blk in b.workflows[0].blocks}
    assert d["pcr_live-0/00"] == 30                                      # fetch: retrieve 20 + move 10
    assert [d[f"pcr_live-0/{i:02d}"] for i in (1, 2, 6, 11, 12, 15, 13)] == [4, 10, 60, 20, 4020, 40, 30]


def test_untimed_workflow_unchanged(compiled_small):
    assert compiled_small(1).block("pcr-0/12").duration == 1            # v1 rules only


def test_mixed_batch(workcell_small):
    specs, _ = load_batch(EXAMPLES / "pcr_live.py")
    b = compile_batch(workcell_small, [specs[0], pcr(sample="S0002", primer="P0002")])
    assert b.block("pcr_live-0/12").duration == 4020 and b.block("pcr-1/12").duration == 1


def test_times_table_and_seconds_override(workcell_small):
    @workflow(times={"arm.move": 7, "scan": 3})
    def t(w):
        p = w.own("S0001"); q = w.own("S0002"); w.stamp(src=p, dst=q, seconds=11)
    b = compile_batch(workcell_small, [t()])
    blk = b.workflows[0].blocks
    assert blk[0].duration == 1 + 7          # retrieve falls back to workcell default 1; move 7
    assert blk[1].duration == 3
    assert next(x for x in blk if x.kind == "op").duration == 11


def test_bare_decorator_still_works():
    @workflow
    def u(w):
        w.own("S0001")
    assert u().times is None


def test_type_op_key_beats_op_key(workcell_small):
    @workflow(times={"arm.move": 7, "move": 99, "retrieve": 5, "storage.retrieve": 8})
    def t(w):
        p = w.own("S0001"); q = w.own("S0002"); w.stamp(src=p, dst=q)
    blk = compile_batch(workcell_small, [t()]).workflows[0].blocks[0]
    assert blk.duration == 8 + 7

