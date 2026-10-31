"""Idle holds: time a plate sits in an instrument nest between two of its own blocks, while
that instrument does nothing for it (max-idle-hold spec)."""
from __future__ import annotations

from dataclasses import dataclass

from workcell.ir import Block, CompiledBatch, resolve

_PARKING = ("storage", "hotel")


@dataclass
class IdleGap:
    plate: str
    nest: str
    prev: str       # block before the gap (ends it holding the plate in `nest`)
    next: str       # block after the gap
    length: int


@dataclass
class HoldChain:
    """A plate's stay in an instrument nest, at compile level: `blocks[0]` places it,
    `blocks[-1]` (a single-move block) removes it, the ones between use it there."""
    workflow: str
    plate: str
    nest: str       # may be a `?var` nest
    blocks: list[str]


def _instrument_nest(batch: CompiledBatch, nest: str) -> bool:
    wc = batch.workcell
    return wc.instruments[wc.nest_owner(nest)].type not in _PARKING


def hold_chains(batch: CompiledBatch) -> list[HoldChain]:
    """Every stay of a plate in a nest that is an instrument nest under every binding of its
    var, per workflow, from its compiled blocks in compiled order. Stays never left (no
    removing block) are dropped. ValueError if a removing block moves more than one plate."""
    out = []
    for wf in batch.workflows:
        for plate in sorted({p for b in wf.blocks for p in b.plates}):
            chain: HoldChain | None = None
            for b in wf.blocks:
                if plate not in b.plates:
                    continue
                mv = next((m for m in b.moves if m.plate == plate), None)
                if mv is None:
                    if chain is not None:
                        chain.blocks.append(b.id)
                    continue
                if chain is not None:
                    if len(b.moves) != 1:
                        raise ValueError(f"block {b.id} removes {plate} and moves other plates")
                    chain.blocks.append(b.id)
                    out.append(chain)
                    chain = None
                var = mv.dst.partition(".")[0] if mv.dst.startswith("?") else None
                nests = [resolve(mv.dst, {var: c}) for c in wf.variables[var]] if var else [mv.dst]
                if all(_instrument_nest(batch, n) for n in nests):
                    chain = HoldChain(wf.id, plate, mv.dst, [b.id])
    return out


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
