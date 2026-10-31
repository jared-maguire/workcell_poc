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
