import pytest

from workcell.config import parse_workcell

SMALL = {
    "instruments": [
        {"id": "arm1", "type": "arm"},
        {"id": "store1", "type": "storage", "slots": 2000},
        {"id": "hotel1", "type": "hotel", "slots": 50},
        {"id": "scan1", "type": "barcode_scanner"},
        {"id": "stamp1", "type": "head_stamper"},
        {"id": "tc1", "type": "thermocycler"},
        {"id": "tc2", "type": "thermocycler"},
    ],
    "durations": {"default": 1, "check_status": 0},
    "plates": [
        {"generate": "sample", "prefix": "S", "count": 10, "location": "store1"},
        {"generate": "primer", "prefix": "P", "count": 2, "location": "store1"},
        {"generate": "empty", "prefix": "E", "count": 20, "location": "store1"},
    ],
}


@pytest.fixture
def workcell_small():
    return parse_workcell(SMALL)


@pytest.fixture
def pcr_specs():
    from workcell.library import pcr

    def make(n):
        return [pcr(sample=f"S{i+1:04d}", primer=f"P{i % 2 + 1:04d}") for i in range(n)]
    return make


@pytest.fixture
def compiled_small(workcell_small, pcr_specs):
    from workcell.compiler import compile_batch

    def make(n):
        return compile_batch(workcell_small, pcr_specs(n))
    return make
