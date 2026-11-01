"""Park blocks: move a plate out of an instrument nest to a hotel or storage, and start its
removing move from there instead (the greedy_hotel park shape)."""
from __future__ import annotations

import dataclasses

from workcell.compiler import route, transport_instructions
from workcell.config import Workcell
from workcell.ir import Block, CompiledBatch, PlateMove


def parking_spots(wc: Workcell, near: str | None = None) -> list[str]:
    """Nest ids a plate may be parked in: hotels first, then storage, sorted within each;
    with `near` (a concrete nest), only spots in its group, the shortest route first."""
    nests = wc.nests()
    spots = [n for t in ("hotel", "storage")
             for inst in sorted(wc.of_type(t))
             for n in sorted(nests) if nests[n].instrument == inst]
    if near is None:
        return spots
    spots = [sp for sp in spots if wc.nest_owner(sp) in wc.group(wc.nest_owner(near))]
    hops = {sp: len(route(wc, wc.nest_owner(near), wc.nest_owner(sp))) for sp in spots}
    return sorted(spots, key=lambda sp: hops[sp])        # stable: hotels before storage


def _times(batch: CompiledBatch, wf_id: str) -> dict[str, int] | None:
    i = next(i for i, wf in enumerate(batch.workflows) if wf.id == wf_id)
    return batch.specs[i].times


def park_pair(batch: CompiledBatch, removal: Block, spot: str) -> tuple[Block, Block]:
    """(park block, rewritten removal) for a bound single-move `removal` parked at `spot`."""
    wc, (mv,) = batch.workcell, removal.moves
    times = _times(batch, removal.workflow)
    park = Block(id=f"{removal.id}.park", workflow=removal.workflow, kind="park",
                 label=f"park {mv.plate} → {spot}",
                 instructions=transport_instructions(wc, mv.plate, mv.src, spot, times=times),
                 moves=[PlateMove(mv.plate, mv.src, spot)], plates={mv.plate},
                 deps=list(removal.deps), inserted=True)
    moved = dataclasses.replace(
        removal, label=f"unpark {mv.plate} {spot} → {mv.dst}",
        instructions=transport_instructions(wc, mv.plate, spot, mv.dst, times=times),
        moves=[PlateMove(mv.plate, spot, mv.dst)], deps=[*removal.deps, park.id])
    return park, moved
