"""Plan executor: runs a plan against a World, minute by minute, and records a trace."""
from __future__ import annotations

import heapq
from dataclasses import dataclass, field

from workcell.ir import CompiledBatch
from workcell.plan import Plan
from workcell.sim.world import InstrumentError, World


@dataclass
class InstructionEvent:
    time: int
    block: str
    instrument: str
    op: str
    args: dict
    duration: int


@dataclass
class MoveEvent:
    time: int
    plate: str
    src: str
    dst: str


@dataclass
class Alarm:
    time: int
    block: str
    instrument: str
    op: str
    message: str


@dataclass
class Trace:
    events: list[InstructionEvent] = field(default_factory=list)
    moves: list[MoveEvent] = field(default_factory=list)
    spans: dict[str, tuple[int, int]] = field(default_factory=dict)
    lock_waits: list[tuple[str, int]] = field(default_factory=list)
    completed: set[str] = field(default_factory=set)
    alarm: Alarm | None = None
    end_time: int = 0
    initial_locations: dict[str, str] = field(default_factory=dict)
    final_locations: dict[str, str] = field(default_factory=dict)


_RELEASE, _INSTR = 0, 1


def execute(plan: Plan, batch: CompiledBatch) -> Trace:
    initial = batch.initial_locations()
    world = World(batch.workcell, initial)
    trace = Trace(initial_locations=dict(initial))

    order = {b.id: i for i, b in enumerate(plan.ordered())}
    pending = [b.id for b in plan.ordered()]
    ended: set[str] = set()
    busy: set[str] = set()
    started: dict[str, int] = {}
    # heap items: (time, kind, block order, instruction index, block id)
    heap: list[tuple[int, int, int, int, str]] = []

    def locks(bid: str) -> set[str]:
        blk = plan.blocks[bid]
        return blk.instruments | blk.plates

    def try_starts(t: int) -> None:
        for bid in list(pending):
            blk = plan.blocks[bid]
            if (plan.start[bid] <= t and all(d in ended for d in blk.deps)
                    and not locks(bid) & busy):
                pending.remove(bid)
                busy.update(locks(bid))
                started[bid] = t
                if t > plan.start[bid]:
                    trace.lock_waits.append((bid, t - plan.start[bid]))
                kind, idx = (_INSTR, 0) if blk.instructions else (_RELEASE, 0)
                heapq.heappush(heap, (t, kind, order[bid], idx, bid))

    def finish_alarm(alarm: Alarm) -> Trace:
        trace.alarm = alarm
        trace.end_time = alarm.time
        for bid, s in started.items():
            if bid not in ended:
                trace.spans[bid] = (s, alarm.time)
        trace.final_locations = world.snapshot()["locations"]
        return trace

    now = -1
    while True:
        candidates = [h[0] for h in heap[:1]]
        candidates += [plan.start[b] for b in pending if plan.start[b] > now]
        if not candidates:
            break
        t = now = min(candidates)
        while True:
            while heap and heap[0][0] == t and heap[0][1] == _RELEASE:
                _, _, _, _, bid = heapq.heappop(heap)
                busy -= locks(bid)
                ended.add(bid)
                trace.completed.add(bid)
                trace.spans[bid] = (started[bid], t)
            try_starts(t)
            if not (heap and heap[0][0] == t):
                break
            if heap[0][1] == _RELEASE:
                continue
            _, _, o, idx, bid = heapq.heappop(heap)
            blk = plan.blocks[bid]
            instr = blk.instructions[idx]
            try:
                world.apply(instr)
            except InstrumentError as e:
                return finish_alarm(Alarm(t, bid, e.instrument, e.op, e.message))
            trace.events.append(InstructionEvent(t, bid, instr.instrument, instr.op,
                                                 dict(instr.args), instr.duration))
            if instr.instrument in batch.workcell.of_type("arm") and instr.op == "move":
                a = instr.args
                trace.moves.append(MoveEvent(t, a["plate"], a["src"], a["dst"]))
            done = t + instr.duration
            trace.end_time = max(trace.end_time, done)
            if idx + 1 < len(blk.instructions):
                heapq.heappush(heap, (done, _INSTR, o, idx + 1, bid))
            else:
                heapq.heappush(heap, (done, _RELEASE, o, 0, bid))

    trace.final_locations = world.snapshot()["locations"]
    return trace
