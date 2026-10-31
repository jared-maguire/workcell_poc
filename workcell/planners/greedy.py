"""Greedy planner: event-driven list scheduling that starts whatever can acquire its locks."""
from __future__ import annotations

import itertools
from collections import Counter
from dataclasses import replace
from typing import Literal

from workcell.compiler import transport_instructions
from workcell.ir import Block, CompiledBatch, CompiledWorkflow, PlateMove
from workcell.plan import Deadlock, Plan, Wait

Priority = Literal["round_robin", "fifo"]


class _Greedy:
    def __init__(self, batch: CompiledBatch, priority: Priority, park: bool):
        if priority not in ("round_robin", "fifo"):
            raise ValueError(f"unknown greedy priority {priority!r}")
        self.batch, self.priority, self.park = batch, priority, park
        self.workflows = batch.workflows
        self.wf_index = {wf.id: i for i, wf in enumerate(self.workflows)}
        nests = batch.workcell.nests()
        self.capacity = {n: nest.capacity for n, nest in nests.items()}
        self.siblings = {n: [o for o in nests if o != n and nest.instrument == nests[o].instrument]
                         for n, nest in nests.items()}     # other nests of the same instrument
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
        self.rewritten: dict[str, Block] = {}              # parked block id -> unbound rewrite
        self.last_wf: int | None = None                    # index of the most recent starter
        self.hotel_full: dict[str, str] = {}               # block id -> why parking failed (this pass)

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
        return [b for i in order for b in map(self._current, self.workflows[i].blocks)
                if ready(b)]

    def _current(self, blk: Block) -> Block:
        """The planner's version of a compiled block (its rewrite, if it was parked)."""
        return self.rewritten.get(blk.id, blk)

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
        if self.park:
            reasons += self._reserved(blk)
        for p in sorted(blk.plates):
            holder = self.borrow_holder.get(p)
            if p in self.borrowers and holder not in (None, blk.workflow):
                reasons.append(f"plate {p} borrowed by {holder}")
        return reasons

    def _reserved(self, blk: Block) -> list[str]:
        """greedy_hotel: a multi-nest working instrument (head stamper) serves one workflow at
        a time, so a move into one of its nests waits while another workflow's plate sits in a
        sibling nest. This avoids two workflows each holding half the stamper (hold-and-wait)."""
        wc, reasons = self.batch.workcell, []
        for m in blk.moves:
            if wc.instruments[wc.nest_owner(m.dst)].type in ("storage", "hotel"):
                continue
            for sib in self.siblings[m.dst]:
                held = [p for p in self._occupants(sib) if self.placed_by.get(p) != blk.workflow]
                if held:
                    reasons.append(f"nest {m.dst} reserved ({sib} holds "
                                   f"{', '.join(self._describe(p) for p in held)})")
        return reasons

    def _choices(self, blk: Block):
        """Yield each binding of `blk`'s still-unbound vars (new vars only) in candidate order."""
        wf: CompiledWorkflow = self.workflows[self.wf_index[blk.workflow]]
        bound = self.bindings[blk.workflow]
        names = [i.instrument for i in blk.instructions] + [n for m in blk.moves
                                                            for n in (m.src, m.dst)]
        unbound = list(dict.fromkeys(n.partition(".")[0] for n in names
                                     if n.startswith("?") and n.partition(".")[0] not in bound))
        for choice in itertools.product(*(wf.variables[v] for v in unbound)):
            yield dict(zip(unbound, choice))

    def _try_bind(self, blk: Block) -> tuple[Block | None, dict[str, str], list[str]]:
        """Bind `blk`'s still-unbound vars to the first candidates that make it startable.
        Returns (bound block or None, new bindings, blocking reasons)."""
        bound = self.bindings[blk.workflow]
        first_reasons: list[str] | None = None
        for new in self._choices(blk):
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

    def _park_hook(self, blk: Block, t: int) -> bool:
        """greedy_hotel: when a move block is blocked only by its destination nest (full, or
        reserved) under every binding choice, and its plate sits on a working instrument, park
        the plate in the first hotel with room and rewrite the move to start from that hotel.
        Each block is parked at most once. Returns True if a park block was started."""
        if blk.kind != "move" or blk.id in self.rewritten:
            return False
        (move,) = blk.moves
        bound = self.bindings[blk.workflow]
        for new in self._choices(blk):
            concrete = blk.bind({**bound, **new})
            dst = concrete.moves[0].dst
            reasons = self._blockers(concrete)
            if not reasons or not all(r.startswith(f"nest {dst} ") for r in reasons):
                return False
        wc = self.batch.workcell
        cur = self.location[move.plate]
        if wc.instruments[wc.nest_owner(cur)].type in ("storage", "hotel"):
            return False
        hotel = next((h for h in wc.of_type("hotel")
                      if len(self._occupants(h)) < self.capacity[h]), None)
        if hotel is None:
            hotels = ", ".join(f"{h} {len(self._occupants(h))}/{self.capacity[h]}"
                               for h in wc.of_type("hotel")) or "no hotel configured"
            self.hotel_full[blk.id] = f"hotel full: cannot park {move.plate} ({hotels})"
            return False
        park = Block(id=f"{blk.id}.park", workflow=blk.workflow, kind="park",
                     label=f"park {move.plate} → {hotel}",
                     instructions=transport_instructions(wc, move.plate, cur, hotel),
                     moves=[PlateMove(move.plate, cur, hotel)], plates={move.plate},
                     deps=[], inserted=True)
        if self._blockers(park):       # arm, current owner, hotel, or plate not free
            return False
        wf = self.workflows[self.wf_index[blk.workflow]]
        var_types = {v: wc.instruments[cands[0]].type for v, cands in wf.variables.items()}
        self.rewritten[blk.id] = replace(
            blk, label=f"unpark {move.plate} {hotel} → {move.dst}",
            instructions=transport_instructions(wc, move.plate, hotel, move.dst, var_types),
            moves=[PlateMove(move.plate, hotel, move.dst)], plates=set(blk.plates),
            deps=[*blk.deps, park.id])
        self._start(park, {}, t)
        return True

    # -- deadlock explanation -----------------------------------------------
    def _holders(self, blk: Block) -> set[str]:
        """Other workflows holding something `blk` waits for, over every binding choice: plates
        in its full or reserved destination nests (or in the full hotels it wants to park in),
        and borrowed plates."""
        wc, holders = self.batch.workcell, set()
        bound = self.bindings[blk.workflow]
        for new in self._choices(blk):
            concrete = blk.bind({**bound, **new})
            nests = [m.dst for m in concrete.moves]
            if self.park:
                nests += [s for m in concrete.moves
                          if wc.instruments[wc.nest_owner(m.dst)].type not in ("storage", "hotel")
                          for s in self.siblings[m.dst]]
            if blk.id in self.hotel_full:
                nests += wc.of_type("hotel")
            holders |= {self.placed_by.get(p) for n in nests for p in self._occupants(n)}
            holders |= {self.borrow_holder.get(p) for p in concrete.plates}
        return holders - {None, blk.workflow}

    def _wait_cycle(self, waiting: list[Block]) -> list[str]:
        """A cycle in the wait-for graph: waiting block -> workflow holding what it needs ->
        that workflow's waiting blocks. Returns the cycle's block ids, or [] if none."""
        by_wf: dict[str, list[str]] = {}
        for b in waiting:
            by_wf.setdefault(b.workflow, []).append(b.id)
        edges = {b.id: [w for wf in sorted(self._holders(b)) for w in by_wf.get(wf, [])]
                 for b in waiting}
        done: set[str] = set()

        def dfs(node: str, path: list[str]) -> list[str]:
            if node in path:
                return path[path.index(node):]
            if node in done:
                return []
            for nxt in edges[node]:
                cycle = dfs(nxt, [*path, node])
                if cycle:
                    return cycle
            done.add(node)
            return []

        for b in waiting:
            cycle = dfs(b.id, [])
            if cycle:
                return cycle
        return []

    def _hotel_reason(self, blk: Block) -> list[str]:
        reason = self.hotel_full.get(blk.id)
        return [reason] if reason else []

    def _start_one(self, t: int) -> bool:
        self.hotel_full.clear()
        for cand in self._ready():
            concrete, new, _ = self._try_bind(cand)
            if concrete is not None:
                self._start(concrete, new, t)
                return True
            if self.park and self._park_hook(cand, t):
                return True
        return False

    def run(self, mode: str) -> Plan:
        t = 0
        compiled = [b.id for b in self.batch.all_blocks()]
        while True:
            for bid in [b for b, end in self.running.items() if end == t]:
                self._finish(bid)
            while self._start_one(t):
                pass
            if all(bid in self.done for bid in compiled):
                return self._plan(mode, "complete")
            if not self.running:
                ready = self._ready()
                waits = [Wait(b.id, "; ".join([*self._try_bind(b)[2], *self._hotel_reason(b)]))
                         for b in ready]
                return self._plan(mode, "deadlock",
                                  Deadlock(time=t, waits=waits, cycle=self._wait_cycle(ready)))
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
