"""Idle holds: time a plate sits in an instrument nest between two of its own blocks, while
that instrument does nothing for it (max-idle-hold spec)."""
from __future__ import annotations

from dataclasses import dataclass

from workcell.ir import Block, CompiledBatch

_PARKING = ("storage", "hotel")


@dataclass
class IdleGap:
    plate: str
    nest: str
    prev: str       # block before the gap (ends it holding the plate in `nest`)
    next: str       # block after the gap
    length: int


def _instrument_nest(batch: CompiledBatch, nest: str) -> bool:
    wc = batch.workcell
    return wc.instruments[wc.nest_owner(nest)].type not in _PARKING


def idle_gaps(blocks: dict[str, Block], start: dict[str, int], end: dict[str, int],
              batch: CompiledBatch) -> list[IdleGap]:
    """Idle-hold gaps of a schedule: for each plate, its scheduled blocks in (start, id)
    order; while it sits in an instrument nest, each consecutive pair of its blocks leaves a
    gap of start[next] - end[prev]. Sorted by (plate, start of prev, prev)."""
    initial = batch.initial_locations()
    by_plate: dict[str, list[str]] = {}
    for bid in start:
        for p in blocks[bid].plates:
            by_plate.setdefault(p, []).append(bid)
    gaps = []
    for plate in sorted(by_plate):
        loc, prev = initial.get(plate), None
        for bid in sorted(by_plate[plate], key=lambda b: (start[b], b)):
            if prev is not None and loc is not None and _instrument_nest(batch, loc):
                gaps.append(IdleGap(plate, loc, prev, bid, start[bid] - end[prev]))
            for m in blocks[bid].moves:
                if m.plate == plate:
                    loc = m.dst
            prev = bid
    return gaps


def violations(gaps: list[IdleGap], limit: int) -> list[IdleGap]:
    return [g for g in gaps if g.length > limit]
