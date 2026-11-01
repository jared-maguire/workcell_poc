"""Just-in-time dispatcher (spec docs/superpowers/specs/2026-10-31-jit-dispatch-design.md):
start whatever can start — workflows already under way first, then the most remaining work;
park a plate when its next stop is full, or (idle rule) when it has sat on an instrument too
long."""
from __future__ import annotations

import dataclasses
from collections import defaultdict
from typing import Callable

from workcell.ir import Block, CompiledBatch
from workcell.parking import park_pair, parking_spots
from workcell.plan import Deadlock, Plan, Wait
from workcell.planners.greedy import _Greedy

PASSIVE = ("storage", "hotel", "handoff")
PARK_MARGIN = 60    # the idle rule parks a plate this many seconds before the limit


def remaining_work(batch: CompiledBatch) -> dict[str, int]:
    """Block id -> its planned duration plus the longest chain of planned durations after it
    to the end of its workflow."""
    out: dict[str, int] = {}
    for wf in batch.workflows:
        succ = defaultdict(list)
        for b in wf.blocks:
            for d in b.deps:
                succ[d].append(b.id)
        for b in reversed(wf.blocks):           # compile order is topological
            out[b.id] = b.duration + max((out[s] for s in succ[b.id]), default=0)
    return out


class _Dispatch(_Greedy):
    def __init__(self, batch: CompiledBatch, max_idle_hold: int | None,
                 actual: Callable[[Block], list[int]] | None):
        super().__init__(batch, "fifo", park=True)
        self.rank = remaining_work(batch)
        self.order = {b.id: i for i, b in enumerate(batch.all_blocks())}
        self.park_after = None if not max_idle_hold else max(0, max_idle_hold - PARK_MARGIN)
        self.actual = actual
        self.spot: dict[str, str] = {}          # parked removal id -> parking spot
        self.idle_since: dict[str, int] = {}    # plate -> end of its last block
        self.under_way: set[str] = set()        # workflows with a started block

    # -- ordering and binding -------------------------------------------------
    def _current(self, blk: Block) -> Block:
        return blk                      # parked moves are rewritten once bound (_try_bind)

    def _ready(self) -> list[Block]:
        """Workflows under way first (a new one would take shared plates and instruments
        they wait for), then the most remaining work, then batch order."""
        return sorted(super()._ready(), key=lambda b: (b.workflow not in self.under_way,
                                                       -self.rank[b.id], self.order[b.id]))

    def _bound(self, blk: Block, new: dict[str, str]) -> Block:
        concrete = blk.bind({**self.bindings[blk.workflow], **new})
        if blk.id in self.spot:
            concrete = park_pair(self.batch, concrete, self.spot[blk.id])[1]
        return concrete

    def _try_bind(self, blk: Block):
        """Like greedy, but binding choices go shortest route (block duration) first."""
        options = sorted(((self._bound(blk, new), new) for new in self._choices(blk)),
                         key=lambda o: o[0].duration)
        first_reasons = None
        for concrete, new in options:
            reasons = self._blockers(concrete)
            if not reasons:
                return concrete, new, []
            if first_reasons is None:
                first_reasons = reasons
        return None, {}, first_reasons or []

    def _passive(self, nest: str) -> bool:
        wc = self.batch.workcell
        return wc.instruments[wc.nest_owner(nest)].type in PASSIVE

    # -- transitions ------------------------------------------------------------
    def _start(self, blk: Block, new_bindings: dict[str, str], t: int) -> None:
        if self.actual is not None:
            blk = dataclasses.replace(blk, instructions=[
                dataclasses.replace(i, duration=d)
                for i, d in zip(blk.instructions, self.actual(blk))])
        for p in blk.plates:
            self.idle_since.pop(p, None)
        self.under_way.add(blk.workflow)
        super()._start(blk, new_bindings, t)

    def _finish(self, bid: str) -> None:
        end = self.running[bid]
        super()._finish(bid)
        for p in self.blocks[bid].plates:
            if not self._passive(self.location[p]):
                self.idle_since[p] = end

    # -- parking ------------------------------------------------------------------
    def _park(self, blk: Block, t: int) -> bool:
        """Park the plate of the single-move block `blk` (not started, not parked yet) in the
        nearest parking spot with room, if the park can start now; `blk` will then start
        from there."""
        (move,) = blk.moves
        cur = self.location[move.plate]
        spot = next((s for s in parking_spots(self.batch.workcell, near=cur)
                     if len(self._occupants(s)) < self.capacity[s]), None)
        if spot is None:
            self.hotel_full[blk.id] = f"hotel full: cannot park {move.plate}"
            return False
        anyway = next(iter(self._choices(blk)))           # the park leg is binding-free
        concrete = blk.bind({**self.bindings[blk.workflow], **anyway})
        park, _ = park_pair(self.batch, concrete, spot)
        park = dataclasses.replace(park, deps=[])
        if self._blockers(park):
            return False
        self.spot[blk.id] = spot
        self._start(park, {}, t)
        return True

    def _park_hook(self, blk: Block, t: int) -> bool:
        """greedy_hotel's rule: park when `blk` is blocked only by its destination nest, under
        every binding choice, and its plate sits on an instrument."""
        if blk.kind != "move" or blk.id in self.spot or len(blk.moves) != 1:
            return False
        for new in self._choices(blk):
            concrete = self._bound(blk, new)
            dst = concrete.moves[0].dst
            reasons = self._blockers(concrete)
            if not reasons or not all(r.startswith(f"nest {dst} ") for r in reasons):
                return False
        if self._passive(self.location[blk.moves[0].plate]):
            return False
        return self._park(blk, t)

    def _next_block(self, plate: str) -> Block | None:
        wf = self.workflows[self.wf_index[self.placed_by[plate]]]
        return next((b for b in wf.blocks if plate in b.plates and b.id not in self.start), None)

    def _idle_parks(self, t: int) -> bool:
        """The idle rule: park a plate that has sat idle on an instrument for `park_after`
        seconds when its next block is a single move out that hasn't started."""
        if self.park_after is None:
            return False
        locked = self._locked_plates()
        for plate, since in sorted(self.idle_since.items()):
            if t - since < self.park_after or plate in locked or plate not in self.placed_by:
                continue
            nxt = self._next_block(plate)
            if (nxt is None or nxt.kind not in ("move", "return") or len(nxt.moves) != 1
                    or nxt.id in self.spot):
                continue
            if self._park(nxt, t):
                return True
        return False

    def _deadlines(self, t: int) -> list[int]:
        if self.park_after is None:
            return []
        return [s + self.park_after for s in self.idle_since.values() if s + self.park_after > t]

    def run(self, mode: str) -> Plan:
        t = 0
        compiled = [b.id for b in self.batch.all_blocks()]
        while True:
            for bid in sorted(b for b, end in self.running.items() if end == t):
                self._finish(bid)
            while self._start_one(t) or self._idle_parks(t):
                pass
            if all(bid in self.done for bid in compiled):
                return self._plan(mode, "complete")
            wakes = [*self.running.values(), *self._deadlines(t)]
            if not wakes:
                ready = self._ready()
                waits = [Wait(b.id, "; ".join([*self._try_bind(b)[2], *self._hotel_reason(b)]))
                         for b in ready]
                return self._plan(mode, "deadlock",
                                  Deadlock(time=t, waits=waits, cycle=self._wait_cycle(ready)))
            t = min(wakes)


def plan_dispatch(batch: CompiledBatch, max_idle_hold: int | None = None,
                  actual: Callable[[Block], list[int]] | None = None,
                  mode: str = "jit") -> Plan:
    """Dispatch the batch just in time; returns a "complete" plan or a "deadlock" plan of the
    blocks started before the stall. With `max_idle_hold` (s), the idle rule parks plates;
    with `actual` (bound block -> instruction durations) blocks run that long, and only
    finished blocks' end times inform decisions (a live run)."""
    return _Dispatch(batch, max_idle_hold, actual).run(mode)
