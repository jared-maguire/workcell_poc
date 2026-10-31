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


def test_failure_instruction_alias():
    # spec §4 example writes failures with `instruction:`
    wc = parse_workcell({**SMALL, "failures": [{"instruction": "scan", "plate": "S0003", "kind": "barcode_mismatch"}]})
    assert wc.failures == [FailureSpec(op="scan", plate="S0003", kind="barcode_mismatch")]


@pytest.mark.parametrize("bad, msg", [
    ({"instruments": [{"type": "arm"}]}, "instrument entry 0.*'id'"),
    ({"instruments": [{"id": "a"}]}, "instrument entry 0.*'type'"),
    ({"plates": [{"generate": "sample", "prefix": "S", "location": "store1"}]}, "plate entry 0.*'count'"),
    ({"plates": [{"generate": "sample", "count": 2, "location": "store1"}]}, "plate entry 0.*'prefix'"),
    ({"plates": [{"generate": "sample", "count": 2, "prefix": "S"}]}, "plate entry 0.*'location'"),
    ({"plates": [{"type": "sample", "location": "store1"}]}, "plate entry 0.*'barcode'"),
    ({"plates": [{"barcode": "X1", "location": "store1"}]}, "plate entry 0.*'type'"),
    ({"failures": [{"op": "scan", "colour": "red"}]}, "failure entry 0.*colour"),
    ({"failures": [{"plate": "S0001"}]}, "failure entry 0.*'op'"),
    ({"failures": [{"op": "scan", "instruction": "scan"}]}, "failure entry 0.*both"),
    ({"instruments": [{"id": "s", "type": "storage"}]}, "storage 's'.*slots"),
])
def test_malformed_entries(bad, msg):
    with pytest.raises(ConfigError, match=msg):
        parse_workcell({**SMALL, **bad})


def test_hotel_without_slots_unlimited():
    wc = parse_workcell({**SMALL, "instruments": [*SMALL["instruments"][:2], {"id": "h", "type": "hotel"}]})
    assert wc.nests()["h"].capacity == 10**9


def test_load_workcell_malformed_yaml(tmp_path):
    from workcell.config import load_workcell
    (tmp_path / "w.yaml").write_text("instruments: [unclosed")
    with pytest.raises(ConfigError, match="w.yaml"):
        load_workcell(tmp_path / "w.yaml")
