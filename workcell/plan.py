"""Plan model: a (possibly partial) schedule of bound blocks with start times."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from workcell.ir import Block, CompiledBatch


@dataclass
class Wait:
    block: str
    reason: str


@dataclass
class Deadlock:
    time: int
    waits: list[Wait]
    cycle: list[str] = field(default_factory=list)   # waiting block ids forming a wait-for cycle


@dataclass
class Plan:
    mode: str
    blocks: dict[str, Block]
    start: dict[str, int]
    bindings: dict[str, dict[str, str]]
    status: Literal["complete", "deadlock", "optimal", "feasible", "infeasible", "no_solution"]
    deadlock: Deadlock | None = None
    solve_seconds: float = 0.0
    objective: str | None = None
    solver_status: str | None = None     # raw CP-SAT status name (cpsat modes only)

    def end(self, block_id: str) -> int:
        return self.start[block_id] + self.blocks[block_id].duration

    @property
    def makespan(self) -> int:
        return max((self.end(b) for b in self.start), default=0)

    @property
    def finished(self) -> bool:
        return self.status in ("complete", "optimal", "feasible")

    def ordered(self) -> list[Block]:
        return [self.blocks[b] for b in sorted(self.start, key=lambda b: (self.start[b], b))]


def bind_batch(batch: CompiledBatch, bindings: dict[str, dict[str, str]]) -> dict[str, Block]:
    """Bind every block's `?vars` using its workflow's bindings (KeyError if unbound)."""
    return {b.id: b.bind(bindings.get(wf.id, {}))
            for wf in batch.workflows for b in wf.blocks}


def plate_stays(plan: Plan, initial: dict[str, str]) -> list[tuple[str, str, int, int | None]]:
    """(plate, nest, arrive, leave) stays: from the START of the placing block to the START of the removing block (initial from 0); leave is None while still there."""
    moves: dict[str, list[tuple[int, str, str]]] = {}
    for bid in plan.start:
        if bid not in plan.blocks:
            continue
        for m in plan.blocks[bid].moves:
            moves.setdefault(m.plate, []).append((plan.start[bid], bid, m.dst))
    stays = []
    for plate in dict.fromkeys([*initial, *moves]):
        nest, arrive = initial.get(plate), 0
        for start, bid, dst in sorted(moves.get(plate, [])):
            if nest is not None:
                stays.append((plate, nest, arrive, start))
            nest, arrive = dst, start
        if nest is not None:
            stays.append((plate, nest, arrive, None))
    return stays
