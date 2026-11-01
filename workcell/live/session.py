"""run_live: forecast with planned times, run the dispatcher on noisy actual times, replay what
happened in the simulator, validate it, and package it for stats and the report."""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass

from workcell.compiler import compile_batch
from workcell.config import Workcell
from workcell.idle import idle_gaps, violations
from workcell.live.noise import NoiseParams, actual_block
from workcell.plan import Plan
from workcell.planners.dispatch import plan_dispatch
from workcell.run import RunResult, Session
from workcell.sim.executor import Trace, execute
from workcell.stats import compute_stats
from workcell.validate import validate_plan


@dataclass
class LiveStats:
    seed: int
    total_delay: int            # Σ over executed blocks of actual − planned duration
    planned_makespan: int | None     # the forecast's (None if the forecast deadlocked)
    actual_makespan: int
    max_idle_hold: int | None = None
    parks: int = 0              # park blocks run
    idle_violations: int = 0    # as-run idle-hold gaps over the limit
    worst_idle: int = 0         # the longest of those gaps (s)
    dispatch_seconds: float = 0.0


@dataclass
class LiveSession:
    session: Session            # one RunResult ("live") over the as-run plan
    forecast: Plan              # dispatched with planned durations
    trace: Trace
    live: LiveStats
    errors: list[str]


def run_live(workcell: Workcell, specs, *, seed: int = 0, noise: float = 0.05,
             delay_prob: float = 0.02, delay_range: tuple[float, float] = (2.0, 5.0),
             workcell_source: str = "", batch_source: str = "",
             max_idle_hold: int | None = 300) -> LiveSession:
    """Dispatch the batch just in time against seeded random step times (same seed, same
    run) and validate what actually happened."""
    max_idle_hold = max_idle_hold or None
    batch = compile_batch(workcell, specs)
    t0 = time.perf_counter()
    forecast = plan_dispatch(batch, max_idle_hold, mode="forecast")
    params = NoiseParams(seed, noise, delay_prob, delay_range)
    planned: dict[str, int] = {}            # block id -> planned duration, as it starts

    def actual(blk):
        planned[blk.id] = blk.duration
        return actual_block(params, blk)
    asrun = plan_dispatch(batch, max_idle_hold, actual=actual, mode="live")
    seconds = asrun.solve_seconds = time.perf_counter() - t0
    trace = execute(asrun, batch)
    errors = validate_plan(asrun, batch, check_durations=False)
    if trace.alarm is not None:
        errors.append(f"simulator alarm: {trace.alarm}")
    over = []
    if max_idle_hold:
        over = violations(idle_gaps(asrun.blocks, asrun.start,
                                    {b: asrun.end(b) for b in asrun.start}, batch), max_idle_hold)
    live = LiveStats(
        seed=seed,
        total_delay=sum(asrun.blocks[b].duration - d for b, d in planned.items()),
        planned_makespan=forecast.makespan if forecast.status == "complete" else None,
        actual_makespan=trace.end_time, max_idle_hold=max_idle_hold,
        parks=sum(b.inserted for b in asrun.blocks.values()),
        idle_violations=len(over), worst_idle=max((g.length for g in over), default=0),
        dispatch_seconds=seconds)
    run = RunResult("live", asrun, trace, compute_stats(asrun, trace, batch), errors)
    session = Session(workcell, batch, [run], workcell_source, batch_source,
                      live={"seed": seed, "max_idle_hold": max_idle_hold, "stats": asdict(live)})
    return LiveSession(session, forecast, trace, live, errors)
