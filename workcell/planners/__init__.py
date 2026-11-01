"""Planner registry: mode string -> Plan."""
from __future__ import annotations

import time

from workcell.ir import CompiledBatch
from workcell.plan import Plan
from workcell.planners.cpsat import plan_cpsat
from workcell.planners.dispatch import plan_dispatch
from workcell.planners.greedy import plan_greedy

MODES = ("jit", "greedy", "greedy:round_robin", "greedy:fifo",
         "greedy_hotel", "greedy_hotel:round_robin", "greedy_hotel:fifo",
         "cpsat", "cpsat:makespan", "cpsat:sum_completion")


def make_plan(batch: CompiledBatch, mode: str, time_limit: float = 30.0) -> Plan:
    if mode not in MODES:
        raise ValueError(f"unknown mode {mode!r}; valid modes: {', '.join(MODES)}")
    if mode == "jit":
        t0 = time.perf_counter()
        plan = plan_dispatch(batch)
        plan.solve_seconds = time.perf_counter() - t0
        return plan
    family, _, option = mode.partition(":")
    if family in ("greedy", "greedy_hotel"):
        return plan_greedy(batch, priority=option or "round_robin", park=family == "greedy_hotel")
    return plan_cpsat(batch, objective=option or "makespan", time_limit=time_limit)
