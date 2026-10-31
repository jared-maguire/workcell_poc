import pytest
from helpers import VALID, sequential

from workcell.plan import plate_stays
from workcell.validate import validate_plan


def test_valid_sequential(compiled_small):
    b = compiled_small(1)
    assert validate_plan(sequential(b, VALID), b) == []


def test_dependency_violation(compiled_small):
    b = compiled_small(1)
    p = sequential(b, VALID)
    p.start["pcr-0/07"] = 0
    assert any("dependency" in e for e in validate_plan(p, b))


def test_instrument_overlap(compiled_small):
    b = compiled_small(1)
    p = sequential(b, VALID)
    p.start["pcr-0/13"] = p.start["pcr-0/06"]
    assert any("stamp1" in e for e in validate_plan(p, b))


def test_nest_capacity(compiled_small):
    b = compiled_small(1)
    order = ["00", "01", "02", "03", "04", "05", "06", "07", "08", "09", "13", "10", "14", "11", "12", "15"]
    assert any("stamp1.src" in e for e in validate_plan(sequential(b, order), b))


def test_unbound_var(compiled_small):
    b = compiled_small(1)
    with pytest.raises(KeyError):
        sequential(b, VALID, bindings={})


def test_missing_blocks_when_finished(compiled_small):
    b = compiled_small(1)
    assert validate_plan(sequential(b, VALID[:-1]), b)
    assert validate_plan(sequential(b, VALID[:-1], status="deadlock"), b) == []


def test_plate_stays(compiled_small):
    b = compiled_small(1)
    p = sequential(b, VALID)
    stays = plate_stays(p, b.initial_locations())
    assert ("S0001", "store1", 0, 0) in stays
    assert any(s[0] == "S0001" and s[1] == "scan1" for s in stays)


# --- negative tests for individual checks ---------------------------------

def _errors(p, b):
    return validate_plan(p, b)


def test_unresolved_name(compiled_small):
    b = compiled_small(1)
    p = sequential(b, VALID)
    p.blocks["pcr-0/11"] = b.block("pcr-0/11")  # still holds ?tc0
    assert any("unresolved" in e and "?tc0" in e for e in _errors(p, b))


def test_plate_overlap(compiled_small):
    b = compiled_small(1)
    p = sequential(b, VALID)
    p.start["pcr-0/13"] = p.start["pcr-0/06"]  # both touch S0001
    assert any("plate S0001" in e for e in _errors(p, b))


def test_broken_move_chain(compiled_small):
    b = compiled_small(1)
    p = sequential(b, VALID)
    p.blocks["pcr-0/02"].moves[0].src = "tc1"
    errs = _errors(p, b)
    assert any("but it is at" in e for e in errs)


def test_borrow_span_overlap(workcell_small):
    from workcell.compiler import compile_batch
    from workcell.library import pcr
    from workcell.plan import Plan, bind_batch

    b = compile_batch(workcell_small, [pcr(sample="S0001", primer="P0001"),
                                       pcr(sample="S0002", primer="P0001")])
    bindings = {"pcr-0": {"?tc0": "tc1"}, "pcr-1": {"?tc0": "tc2"}}
    blocks = bind_batch(b, bindings)

    def plan(offset):
        start, t = {}, 0
        for wf, off in (("pcr-0", 0), ("pcr-1", offset)):
            t = off
            for s in VALID:
                start[f"{wf}/{s}"] = t
                t += blocks[f"{wf}/{s}"].duration
        return Plan(mode="test", blocks=blocks, start=start, bindings=bindings, status="complete")

    assert any("borrow" in e for e in _errors(plan(3), b))
    assert not any("borrow" in e for e in _errors(plan(1000), b))


def test_block_differs_from_compiled(compiled_small):
    b = compiled_small(1)
    p = sequential(b, VALID)
    p.blocks["pcr-0/06"].instructions = p.blocks["pcr-0/06"].instructions[:1]
    assert any("instructions differ" in e for e in _errors(p, b))
    p = sequential(b, VALID)
    p.blocks["pcr-0/07"].deps = []
    assert any("lost compiled dependencies" in e for e in _errors(p, b))
    p = sequential(b, VALID)
    p.blocks["pcr-0/02"].moves[0].dst = "tc2"
    assert any("moves differ" in e for e in _errors(p, b))


def test_park_insertion_allowed(compiled_small):
    import dataclasses

    b = compiled_small(1)
    p = sequential(b, VALID)
    park = dataclasses.replace(b.block("pcr-0/13"), id="pcr-0/park0", kind="park",
                               inserted=True, deps=[], moves=[])
    p.blocks[park.id] = park
    p.start[park.id] = 1000
    move = p.blocks["pcr-0/13"]
    move.deps = [*move.deps, park.id]
    move.moves[0].src = "hotel1"
    errs = _errors(p, b)
    assert not any("differ" in e for e in errs)
    # an uncompiled, non-park block is rejected
    park.inserted = False
    assert any("not compiled" in e for e in _errors(p, b))


def test_unknown_scheduled_id_and_negative_start(compiled_small):
    b = compiled_small(1)
    p = sequential(b, VALID)
    p.start["ghost"] = 5
    assert any("ghost" in e for e in _errors(p, b))
    p = sequential(b, VALID)
    p.start["pcr-0/00"] = -1
    assert any("negative" in e for e in _errors(p, b))


def test_unknown_nest(compiled_small):
    b = compiled_small(1)
    p = sequential(b, VALID)
    p.blocks["pcr-0/15"].moves[0].dst = "nowhere"
    assert any("nowhere" in e for e in _errors(p, b))


def test_bad_bindings(compiled_small):
    b = compiled_small(1)
    p = sequential(b, VALID)
    p.bindings = {"pcr-0": {"?tc0": "scan1"}}
    assert any("not one of" in e for e in _errors(p, b))
    p.bindings = {"pcr-0": {"?tc0": "tc1", "?zz": "tc1"}}
    assert any("?zz" in e for e in _errors(p, b))
    p.bindings = {"pcr-9": {}}
    assert any("pcr-9" in e for e in _errors(p, b))
