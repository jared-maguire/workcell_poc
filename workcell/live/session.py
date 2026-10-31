"""run_live: plan, execute live, validate what actually happened, and package it for stats and
the report."""
from __future__ import annotations

import dataclasses
from dataclasses import asdict, dataclass

from workcell.compiler import compile_batch
from workcell.idle import idle_gaps, violations
from workcell.config import Workcell
from workcell.ir import CompiledBatch
from workcell.live.executor import LiveResult, execute_live
from workcell.live.noise import NoiseParams
from workcell.plan import Plan, bind_batch
from workcell.planners.cpsat import plan_cpsat, plan_idle_hold, replan_cpsat
from workcell.run import RunResult, Session
from workcell.stats import compute_stats
from workcell.validate import validate_plan


@dataclass
class LiveStats:
    seed: int
    threshold: int
    replans: int
    adopted_replans: int
    not_worth_it: int
    failed_replans: int
    reassignments: int
    total_delay: int            # Σ over executed blocks of actual − planned duration
    planned_makespan: int | None     # None when the first plan covers only a window
    actual_makespan: int
    max_idle_hold: int | None = None
    parks: int = 0              # park blocks run
    idle_replans: int = 0       # re-plans triggered by a projected idle-hold violation
    idle_violations: int = 0    # as-run idle-hold gaps over the limit
    worst_idle: int = 0         # the longest of those gaps (s)


@dataclass
class LiveSession:
    session: Session            # one RunResult ("live") over the as-run plan
    result: LiveResult
    live: LiveStats
    errors: list[str]


def as_run_plan(batch: CompiledBatch, result: LiveResult) -> Plan:
    """What actually happened, as a Plan: executed blocks (bound under the final bindings) with
    their actual instruction durations, started at their actual times."""
    trace = result.trace
    bound = bind_batch(batch, result.bindings)
    blocks = {}
    for bid, ds in result.actual.items():
        blk = result.blocks.get(bid) or bound[bid]
        blocks[bid] = dataclasses.replace(blk, instructions=[
            dataclasses.replace(ins, duration=d) for ins, d in zip(blk.instructions, ds)])
    if trace.alarm is not None:
        status = "alarm"
    elif trace.completed >= {b.id for b in batch.all_blocks()}:
        status = "complete"
    else:
        status = "incomplete"
    return Plan(mode="live", blocks=blocks, start={b: s for b, (s, _) in trace.spans.items()},
                bindings=result.bindings, status=status)


def _live_stats(batch: CompiledBatch, result: LiveResult, seed: int, threshold: int,
                max_idle_hold: int | None = None) -> LiveStats:
    planned = {bid: blk.duration for bid, blk in result.blocks.items()}
    events = result.replans
    over = []
    if max_idle_hold:
        spans = result.trace.spans
        over = violations(idle_gaps(result.blocks, {b: s for b, (s, _) in spans.items()},
                                    {b: e for b, (_, e) in spans.items()}, batch), max_idle_hold)
    return LiveStats(
        seed=seed, threshold=threshold, replans=len(events),
        adopted_replans=sum(e.adopted for e in events),
        not_worth_it=sum(e.status == "not_worth_it" for e in events),
        failed_replans=sum(not e.adopted and e.status != "not_worth_it" for e in events),
        reassignments=sum(e.reassigned for e in events),
        total_delay=sum(sum(ds) - planned[bid] for bid, ds in result.actual.items()),
        planned_makespan=None if result.plans[0].partial else result.plans[0].makespan,
        actual_makespan=result.trace.end_time,
        max_idle_hold=max_idle_hold or None,
        parks=sum(b.endswith(".park") for b in result.actual),
        idle_replans=sum(e.reason == "idle_hold" for e in events),
        idle_violations=len(over), worst_idle=max((g.length for g in over), default=0))


def run_live(workcell: Workcell, specs, *, seed: int = 0, noise: float = 0.05,
             delay_prob: float = 0.02, delay_range: tuple[float, float] = (2.0, 5.0),
             replan_threshold: int = 300, replan_time_limit: float = 0.5,
             time_limit: float = 8.0, workcell_source: str = "",
             batch_source: str = "", max_idle_hold: int | None = 300, grid: int = 10,
             window: int | None = 4 * 3600) -> LiveSession:
    """Plan, run live, and validate what actually happened. CP-SAT plans in units of `grid`
    seconds and only `window` seconds ahead, extending the plan every window / 2 (None =
    the whole batch at once)."""
    max_idle_hold, window = max_idle_hold or None, window or None
    batch = compile_batch(workcell, specs)
    if max_idle_hold:
        initial = plan_idle_hold(batch, time_limit, max_idle_hold, grid=grid, window=window)
    else:
        initial = plan_cpsat(batch, time_limit=time_limit, deterministic=True, hint="serial",
                             grid=grid, window=window)
    if not initial.finished:
        raise RuntimeError(f"initial plan failed: {initial.status} ({initial.solver_status})")
    result = execute_live(batch, initial, NoiseParams(seed, noise, delay_prob, delay_range),
                          lambda st: replan_cpsat(batch, st, time_limit=replan_time_limit,
                                                  max_idle_hold=max_idle_hold, grid=grid,
                                                  window=window),
                          replan_threshold, max_idle_hold, window)
    asrun = as_run_plan(batch, result)
    errors = validate_plan(asrun, batch, check_durations=False)
    live = _live_stats(batch, result, seed, replan_threshold, max_idle_hold)
    run = RunResult("live", asrun, result.trace, compute_stats(asrun, result.trace, batch), errors)
    session = Session(workcell, batch, [run], workcell_source, batch_source,
                      live={"seed": seed, "threshold": replan_threshold,
                            "max_idle_hold": max_idle_hold, "grid": grid, "window": window,
                            "replans": [asdict(e) for e in result.replans],
                            "stats": asdict(live),
                            "solve_seconds": {"initial": initial.solve_seconds,
                                              "replans": sum(e.solve_seconds for e in result.replans)}})
    return LiveSession(session, result, live, errors)
