"""Park blocks for the idle-hold rule: move a plate out of an instrument nest to a hotel or
storage, and start its removing move from there instead (the greedy_hotel park shape)."""
from __future__ import annotations

import dataclasses

from workcell.compiler import route, transport_instructions
from workcell.config import Workcell
from workcell.ir import Block, CompiledBatch, PlateMove
from workcell.plan import Plan


def parking_spots(wc: Workcell, near: str | None = None) -> list[str]:
    """Nest ids a plate may be parked in: hotels first, then storage, sorted within each;
    with `near` (a concrete nest), spots with the shortest route from it come first."""
    nests = wc.nests()
    spots = [n for t in ("hotel", "storage")
             for inst in sorted(wc.of_type(t))
             for n in sorted(nests) if nests[n].instrument == inst]
    if near is None:
        return spots
    hops = {sp: len(route(wc, wc.nest_owner(near), wc.nest_owner(sp))) for sp in spots}
    return sorted(spots, key=lambda sp: hops[sp])        # stable: hotels before storage


def _times(batch: CompiledBatch, wf_id: str) -> dict[str, int] | None:
    i = next(i for i, wf in enumerate(batch.workflows) if wf.id == wf_id)
    return batch.specs[i].times


def _var_types(batch: CompiledBatch, wf_id: str) -> dict[str, str]:
    wc = batch.workcell
    wf = next(w for w in batch.workflows if w.id == wf_id)
    return {v: wc.instruments[c[0]].type for v, c in wf.variables.items()}


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


def park_pair_instructions(batch: CompiledBatch, removal: Block,
                           spot: str) -> tuple[list, list]:
    """(park, rewritten removal) instructions for an unbound single-move `removal`
    (AmbiguousRoute if either route depends on a binding)."""
    wc, (mv,) = batch.workcell, removal.moves
    wf = next(w for w in batch.workflows if w.id == removal.workflow)
    kw = dict(var_types=_var_types(batch, removal.workflow),
              times=_times(batch, removal.workflow), candidates=wf.variables)
    return (transport_instructions(wc, mv.plate, mv.src, spot, **kw),
            transport_instructions(wc, mv.plate, spot, mv.dst, **kw))


def park_durations(batch: CompiledBatch, removal: Block, spot: str) -> tuple[int, int]:
    """(park, rewritten removal) durations for an unbound single-move `removal`."""
    there, back = park_pair_instructions(batch, removal, spot)
    return sum(i.duration for i in there), sum(i.duration for i in back)


def plan_parks(plan: Plan) -> dict[str, str]:
    """Removal block id -> parking spot, for every park block in `plan`."""
    return {bid[:-len(".park")]: blk.moves[0].dst for bid, blk in sorted(plan.blocks.items())
            if blk.inserted and blk.kind == "park" and bid.endswith(".park")}
