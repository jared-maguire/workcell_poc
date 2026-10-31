"""Generated scenarios for scale experiments."""
from __future__ import annotations

from workcell.config import Workcell, parse_workcell
from workcell.library import pcr


def scale_workcell_dict(stampers: int, thermocyclers: int, scanners: int,
                        samples: int, primers: int, empties: int) -> dict:
    instruments = [
        {"id": "arm1", "type": "arm"},
        {"id": "store1", "type": "storage", "slots": samples + primers + empties},
        {"id": "hotel1", "type": "hotel", "slots": 500},
    ]
    for prefix, itype, n in (("stamp", "head_stamper", stampers),
                             ("tc", "thermocycler", thermocyclers),
                             ("scan", "barcode_scanner", scanners)):
        instruments += [{"id": f"{prefix}{i}", "type": itype} for i in range(1, n + 1)]
    return {
        "instruments": instruments,
        "durations": {"default": 1, "check_status": 0},
        "plates": [
            {"generate": "sample", "prefix": "S", "count": samples, "location": "store1"},
            {"generate": "primer", "prefix": "P", "count": primers, "location": "store1"},
            {"generate": "empty", "prefix": "E", "count": empties, "location": "store1"},
        ],
    }


def scale_workcell(stampers: int, thermocyclers: int, scanners: int,
                   samples: int, primers: int, empties: int) -> Workcell:
    return parse_workcell(scale_workcell_dict(stampers, thermocyclers, scanners,
                                              samples, primers, empties))


def scale_specs(workflows: int, primers: int) -> list:
    return [pcr(sample=f"S{i + 1:04d}", primer=f"P{i % primers + 1:04d}") for i in range(workflows)]
