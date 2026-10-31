"""Run statistics computed from what actually happened (the trace), not the plan."""
from __future__ import annotations

from dataclasses import dataclass, replace

from workcell.ir import CompiledBatch
from workcell.plan import Plan, plate_stays
from workcell.sim.executor import Trace


@dataclass
class Stats:
    status: str
    makespan: int
    completed: int
    total: int
    latency: dict[str, int | None]
    mean_latency: float | None
    max_latency: int | None
    waiting: dict[str, int | None]
    total_waiting: int | None          # over completed workflows
    mean_waiting: float | None
    mean_completion: float | None      # mean last-block end over completed workflows
    utilization: dict[str, float]
    blocked: dict[str, int]            # minutes a plate rests in an idle instrument's nest
    solve_seconds: float
    lock_waits: int
    alarm: str | None


def _minutes(intervals) -> set[int]:
    return {m for s, e in intervals for m in range(s, e)}


def blocked_minutes(plan: Plan, trace: Trace, batch: CompiledBatch) -> dict[str, int]:
    """Per working instrument (not arm/storage/hotel): minutes during which a plate rests in
    one of its nests while no block is using the instrument. Stays follow Ruling R4 on the
    trace's actual start times; a plate still there at the end stays until trace.end_time."""
    wc = batch.workcell
    owners = {n.id: n.instrument for n in wc.nests().values()
              if wc.instruments[n.instrument].type not in ("storage", "hotel")}
    actual = replace(plan, start={bid: s for bid, (s, _) in trace.spans.items()})
    occupied: dict[str, list[tuple[int, int]]] = {i: [] for i in dict.fromkeys(owners.values())}
    for _, nest, arrive, leave in plate_stays(actual, batch.initial_locations()):
        if nest in owners:
            occupied[owners[nest]].append((arrive, trace.end_time if leave is None else leave))
    busy: dict[str, list[tuple[int, int]]] = {i: [] for i in occupied}
    for bid, span in trace.spans.items():
        for inst in plan.blocks[bid].instruments:
            if inst in busy:
                busy[inst].append(span)
    return {i: len(_minutes(occupied[i]) - _minutes(busy[i])) for i in occupied}


def compute_stats(plan: Plan, trace: Trace, batch: CompiledBatch) -> Stats:
    latency: dict[str, int | None] = {}
    waiting: dict[str, int | None] = {}
    completion: list[int] = []
    for wf in batch.workflows:
        if wf.blocks and all(b.id in trace.completed for b in wf.blocks):
            spans = [trace.spans[b.id] for b in wf.blocks]
            end = max(e for _, e in spans)
            lat = end - min(s for s, _ in spans)
            latency[wf.id] = lat
            waiting[wf.id] = lat - sum(b.duration for b in wf.blocks)
            completion.append(end)
        else:
            latency[wf.id] = waiting[wf.id] = None
    done = [v for v in latency.values() if v is not None]
    waits = [v for v in waiting.values() if v is not None]

    busy = {inst: 0 for inst in batch.workcell.instruments}
    for bid, (s, e) in trace.spans.items():
        blk = plan.blocks.get(bid)
        if blk is None:
            continue
        for inst in blk.instruments:
            if inst in busy:
                busy[inst] += e - s
    utilization = {i: (b / trace.end_time if trace.end_time else 0.0) for i, b in busy.items()}

    if trace.alarm:
        status = "alarm"
    elif plan.status != "deadlock" and set(plan.start) - trace.completed:
        status = "incomplete"          # the executor could not finish every scheduled block
    else:
        status = plan.status

    return Stats(
        status=status,
        makespan=trace.end_time,
        completed=len(done),
        total=len(batch.workflows),
        latency=latency,
        mean_latency=sum(done) / len(done) if done else None,
        max_latency=max(done) if len(done) == len(latency) and done else None,
        waiting=waiting,
        total_waiting=sum(waits) if waits else None,
        mean_waiting=sum(waits) / len(waits) if waits else None,
        mean_completion=sum(completion) / len(completion) if completion else None,
        utilization=utilization,
        blocked=blocked_minutes(plan, trace, batch),
        solve_seconds=plan.solve_seconds,
        lock_waits=len(trace.lock_waits),
        alarm=trace.alarm.message if trace.alarm else None,
    )
