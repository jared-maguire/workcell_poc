"""Greedy planner: event-driven list scheduling that starts whatever can acquire its locks."""
from __future__ import annotations

import itertools
from collections import Counter
from typing import Literal

from workcell.ir import Block, CompiledBatch, CompiledWorkflow
from workcell.plan import Deadlock, Plan, Wait

Priority = Literal["round_robin", "fifo"]


class _Greedy:
    def __init__(self, batch: CompiledBatch, priority: Priority, park: bool):
        if priority not in ("round_robin", "fifo"):
            raise ValueError(f"unknown greedy priority {priority!r}")
        self.batch, self.priority, self.park = batch, priority, park
        self.workflows = batch.workflows
        self.wf_index = {wf.id: i for i, wf in enumerate(self.workflows)}
        self.capacity = {n: nest.capacity for n, nest in batch.workcell.nests().items()}
        self.location = batch.initial_locations()          # plate -> nest (reserved at move start)
        self.placed_by: dict[str, str] = {}                # plate -> workflow that last moved it
        self.bindings: dict[str, dict[str, str]] = {wf.id: {} for wf in self.workflows}
        self.borrowers = {p: wf.id for wf in self.workflows for p in wf.borrowed}
        self.borrow_holder: dict[str, str] = {}            # plate -> workflow holding the borrow
        self.borrow_spans = {(wf.id, p): wf.borrow_span(p)
                             for wf in self.workflows for p in wf.borrowed}
        self.blocks: dict[str, Block] = {}                 # scheduled, bound
        self.start: dict[str, int] = {}
        self.running: dict[str, int] = {}                  # block id -> end time
        self.done: set[str] = set()
        self.last_wf: int | None = None                    # index of the most recent starter

    # -- state queries -----------------------------------------------------
    def _busy_instruments(self) -> set[str]:
        return {i for b in self.running for i in self.blocks[b].instruments}

    def _locked_plates(self) -> set[str]:
        return {p for b in self.running for p in self.blocks[b].plates}

    def _occupants(self, nest: str) -> list[str]:
        return [p for p, n in self.location.items() if n == nest]

    def _describe(self, plate: str) -> str:
        wf = self.placed_by.get(plate)
        return f"{plate} of {wf}" if wf else plate

    def _ready(self) -> list[Block]:
        def ready(b: Block) -> bool:
            return b.id not in self.start and all(d in self.done for d in b.deps)

        n = len(self.workflows)
        if self.priority == "fifo" or self.last_wf is None:
            order = range(n)
        else:
            order = [(self.last_wf + 1 + k) % n for k in range(n)]
        return [b for i in order for b in self.workflows[i].blocks if ready(b)]

    # -- startability ------------------------------------------------------
    def _blockers(self, blk: Block) -> list[str]:
        """Reasons the concrete (bound) block `blk` cannot start now; [] if startable."""
        reasons = []
        busy = self._busy_instruments()
        reasons += [f"instrument {i} busy" for i in sorted(blk.instruments & busy)]
        locked = self._locked_plates()
        reasons += [f"plate {p} in use" for p in sorted(blk.plates & locked)]
        delta = Counter()
        for m in blk.moves:
            delta[m.src] -= 1
            delta[m.dst] += 1
        for m in blk.moves:
            occupants = self._occupants(m.dst)
            if len(occupants) + delta[m.dst] > self.capacity[m.dst]:
                held = ", ".join(self._describe(p) for p in occupants)
                reason = f"nest {m.dst} full ({held})"
                if reason not in reasons:
                    reasons.append(reason)
        for p in sorted(blk.plates):
            holder = self.borrow_holder.get(p)
            if p in self.borrowers and holder not in (None, blk.workflow):
                reasons.append(f"plate {p} borrowed by {holder}")
        return reasons

    def _try_bind(self, blk: Block) -> tuple[Block | None, dict[str, str], list[str]]:
        """Bind `blk`'s still-unbound vars to the first candidates that make it startable.
        Returns (bound block or None, new bindings, blocking reasons)."""
        wf: CompiledWorkflow = self.workflows[self.wf_index[blk.workflow]]
        bound = self.bindings[blk.workflow]
        names = [i.instrument for i in blk.instructions] + [n for m in blk.moves
                                                            for n in (m.src, m.dst)]
        unbound = list(dict.fromkeys(n.partition(".")[0] for n in names
                                     if n.startswith("?") and n.partition(".")[0] not in bound))
        first_reasons: list[str] | None = None
        for choice in itertools.product(*(wf.variables[v] for v in unbound)):
            new = dict(zip(unbound, choice))
            concrete = blk.bind({**bound, **new})
            reasons = self._blockers(concrete)
            if not reasons:
                return concrete, new, []
            if first_reasons is None:
                first_reasons = reasons
        return None, {}, first_reasons or []

    # -- transitions -------------------------------------------------------
    def _start(self, blk: Block, new_bindings: dict[str, str], t: int) -> None:
        self.bindings[blk.workflow].update(new_bindings)
        self.blocks[blk.id] = blk
        self.start[blk.id] = t
        self.running[blk.id] = t + blk.duration
        for m in blk.moves:
            self.location[m.plate] = m.dst
            self.placed_by[m.plate] = blk.workflow
        for p in blk.plates:
            if self.borrow_spans.get((blk.workflow, p), (None,))[0] == blk.id:
                self.borrow_holder[p] = blk.workflow
        self.last_wf = self.wf_index[blk.workflow]

    def _finish(self, bid: str) -> None:
        del self.running[bid]
        self.done.add(bid)
        blk = self.blocks[bid]
        for p in blk.plates:
            if self.borrow_spans.get((blk.workflow, p), (None, None))[1] == bid:
                del self.borrow_holder[p]

    def _park_hook(self, blk: Block, reasons: list[str], t: int) -> bool:
        """Task 8 (greedy_hotel): when self.park is set and a move block's only blocker is a
        full destination nest, insert a "park" block here and return True. Inactive for now."""
        return False

    def _start_one(self, t: int) -> bool:
        for cand in self._ready():
            concrete, new, reasons = self._try_bind(cand)
            if concrete is not None:
                self._start(concrete, new, t)
                return True
            if self.park and self._park_hook(cand, reasons, t):
                return True
        return False

    def run(self, mode: str) -> Plan:
        t = 0
        total = len(self.batch.all_blocks())
        while True:
            for bid in [b for b, end in self.running.items() if end == t]:
                self._finish(bid)
            while self._start_one(t):
                pass
            if len(self.done) == total:
                return self._plan(mode, "complete")
            if not self.running:
                waits = [Wait(b.id, "; ".join(self._try_bind(b)[2])) for b in self._ready()]
                return self._plan(mode, "deadlock", Deadlock(time=t, waits=waits))
            t = min(self.running.values())

    def _plan(self, mode: str, status, deadlock: Deadlock | None = None) -> Plan:
        return Plan(mode=mode, blocks=dict(self.blocks), start=dict(self.start),
                    bindings={wf: dict(b) for wf, b in self.bindings.items()},
                    status=status, deadlock=deadlock)


def plan_greedy(batch: CompiledBatch, priority: Priority = "round_robin",
                park: bool = False) -> Plan:
    """Greedy list scheduling; returns a "complete" plan or a "deadlock" plan of the
    blocks scheduled before the stall."""
    mode = f"{'greedy_hotel' if park else 'greedy'}:{priority}"
    return _Greedy(batch, priority, park).run(mode)
