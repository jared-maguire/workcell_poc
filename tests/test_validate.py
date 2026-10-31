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
