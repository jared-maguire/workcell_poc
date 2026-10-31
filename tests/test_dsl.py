import pytest

from workcell.dsl import load_batch, workflow
from workcell.library import pcr


def test_pcr_spec_shape():
    spec = pcr(sample="S0001", primer="P0001")
    assert spec.name == "pcr" and spec.args == {"sample": "S0001", "primer": "P0001"}
    assert [(d.mode, d.barcode, d.pool) for d in spec.plates.values()] == [
        ("own", "S0001", None), ("borrow", "P0001", None), ("own", None, "empty")]
    assert [o.kind for o in spec.ops] == ["stamp", "stamp", "thermocycle", "store", "store", "store"]
    assert spec.ops[1].after == ("op0",)


def test_borrowing_a_new_plate_rejected():
    @workflow
    def bad(w):
        w.borrow(w.new_plate("empty"))
    with pytest.raises(ValueError, match="own"):
        bad()


def test_use_after_store_rejected():
    @workflow
    def bad(w):
        p = w.own("S0001"); w.store(p); w.thermocycle(p, program=[])
    with pytest.raises(ValueError, match="after it was stored"):
        bad()


def test_stamp_same_plate_rejected():
    @workflow
    def bad(w):
        p = w.own("S0001"); w.stamp(src=p, dst=p)
    with pytest.raises(ValueError):
        bad()


def test_unknown_after_rejected():
    @workflow
    def bad(w):
        p = w.own("S0001"); w.store(p, after="op9")
    with pytest.raises(ValueError, match="op9"):
        bad()


def test_load_batch(tmp_path):
    f = tmp_path / "b.py"
    f.write_text("from workcell.library import pcr\n"
                 "def batch():\n    return [pcr(sample='S0001', primer='P0001')]\n")
    specs, src = load_batch(f)
    assert len(specs) == 1 and "def batch" in src
