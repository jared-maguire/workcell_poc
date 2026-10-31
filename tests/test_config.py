import pytest

from conftest import SMALL
from workcell.config import ConfigError, FailureSpec, parse_workcell


def test_nests(workcell_small):
    n = workcell_small.nests()
    assert n["stamp1.src"].capacity == 1 and n["stamp1.dst"].instrument == "stamp1"
    assert n["tc1"].capacity == 1 and n["scan1"].capacity == 1
    assert n["store1"].capacity == 2000 and n["hotel1"].capacity == 50
    assert "arm1" not in n
    assert workcell_small.nest_owner("stamp1.src") == "stamp1"


def test_generated_plates(workcell_small):
    p = workcell_small.plates
    assert p["S0001"].type == "sample" and p["S0010"].location == "store1"
    assert "S0011" not in p and p["E0020"].type == "empty"


def test_duration_lookup():
    wc = parse_workcell({**SMALL, "durations": {"default": 1, "check_status": 0, "thermocycler.run_program": 10}})
    assert wc.duration("thermocycler", "run_program") == 10
    assert wc.duration("barcode_scanner", "check_status") == 0
    assert wc.duration("arm", "move") == 1


def test_of_type(workcell_small):
    assert workcell_small.of_type("thermocycler") == ["tc1", "tc2"]


def test_failures_parsed():
    wc = parse_workcell({**SMALL, "failures": [{"op": "scan", "plate": "S0003", "kind": "barcode_mismatch"}]})
    assert wc.failures == [FailureSpec(op="scan", plate="S0003", kind="barcode_mismatch")]


@pytest.mark.parametrize("bad, msg", [
    ({"instruments": [{"id": "x", "type": "toaster"}]}, "toaster"),
    ({"instruments": SMALL["instruments"] + [{"id": "tc1", "type": "thermocycler"}]}, "duplicate"),
    ({"plates": [{"barcode": "S1", "type": "sample", "location": "tc1"}]}, "storage"),
])
def test_config_errors(bad, msg):
    with pytest.raises(ConfigError, match=msg):
        parse_workcell({**SMALL, **bad})
