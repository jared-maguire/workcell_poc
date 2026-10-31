from pathlib import Path

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


EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


@pytest.fixture
def timed_batch(workcell_small):
    """Compile the first n timed PCR workflows of examples/pcr_live.py (seconds-scale)."""
    from workcell.compiler import compile_batch
    from workcell.dsl import load_batch

    def make(n):
        return compile_batch(workcell_small, load_batch(EXAMPLES / "pcr_live.py")[0][:n])
    return make


# Two arms: arm1 reaches storage, hotel1, scanner, stamper, tc1; arm2 reaches tc2 and hotel2;
# they share only the handoff spot ho1.
TWO_ARM = {
    **SMALL,
    "instruments": [
        {"id": "arm1", "type": "arm", "reach": ["store1", "hotel1", "scan1", "stamp1", "tc1", "ho1"]},
        {"id": "arm2", "type": "arm", "reach": ["tc2", "hotel2", "ho1"]},
        {"id": "store1", "type": "storage", "slots": 2000},
        {"id": "hotel1", "type": "hotel", "slots": 50},
        {"id": "hotel2", "type": "hotel", "slots": 50},
        {"id": "scan1", "type": "barcode_scanner"},
        {"id": "stamp1", "type": "head_stamper"},
        {"id": "tc1", "type": "thermocycler"},
        {"id": "tc2", "type": "thermocycler"},
        {"id": "ho1", "type": "handoff", "slots": 2},
    ],
}


@pytest.fixture
def two_arm():
    return parse_workcell(TWO_ARM)
