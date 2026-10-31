"""Run statistics computed from what actually happened (the trace), not the plan."""
from __future__ import annotations

from dataclasses import dataclass

from workcell.ir import CompiledBatch
from workcell.plan import Plan
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
    utilization: dict[str, float]
    solve_seconds: float
    lock_waits: int
    alarm: str | None


def _block_done(trace: Trace, plan: Plan, bid: str, duration: int) -> bool:
    """A block is done if it has a span that was not cut short by an alarm."""
    span = trace.spans.get(bid)
    return span is not None and span[1] - span[0] >= duration


def compute_stats(plan: Plan, trace: Trace, batch: CompiledBatch) -> Stats:
    latency: dict[str, int | None] = {}
    waiting: dict[str, int | None] = {}
    for wf in batch.workflows:
        if wf.blocks and all(_block_done(trace, plan, b.id, b.duration) for b in wf.blocks):
            spans = [trace.spans[b.id] for b in wf.blocks]
            lat = max(e for _, e in spans) - min(s for s, _ in spans)
            latency[wf.id] = lat
            waiting[wf.id] = lat - sum(b.duration for b in wf.blocks)
        else:
            latency[wf.id] = waiting[wf.id] = None
    done = [v for v in latency.values() if v is not None]

    busy = {inst: 0 for inst in batch.workcell.instruments}
    for bid, (s, e) in trace.spans.items():
        blk = plan.blocks.get(bid)
        if blk is None:
            continue
        for inst in blk.instruments:
            if inst in busy:
                busy[inst] += e - s
    utilization = {i: (b / trace.end_time if trace.end_time else 0.0) for i, b in busy.items()}

    return Stats(
        status="alarm" if trace.alarm else plan.status,
        makespan=trace.end_time,
        completed=len(done),
        total=len(batch.workflows),
        latency=latency,
        mean_latency=sum(done) / len(done) if done else None,
        max_latency=max(done) if len(done) == len(latency) and done else None,
        waiting=waiting,
        utilization=utilization,
        solve_seconds=plan.solve_seconds,
        lock_waits=len(trace.lock_waits),
        alarm=trace.alarm.message if trace.alarm else None,
    )
