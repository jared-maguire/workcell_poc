"""Run orchestration: compile a batch, then plan / validate / execute / stats per mode."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from workcell.compiler import compile_batch
from workcell.config import Workcell, load_workcell
from workcell.dsl import load_batch
from workcell.ir import CompiledBatch
from workcell.plan import Plan
from workcell.planners import MODES, make_plan
from workcell.sim.executor import Trace, execute
from workcell.stats import Stats, compute_stats
from workcell.validate import validate_plan


@dataclass
class RunResult:
    mode: str
    plan: Plan
    trace: Trace
    stats: Stats
    errors: list[str]


@dataclass
class Session:
    workcell: Workcell
    batch: CompiledBatch
    runs: list[RunResult]
    workcell_source: str
    batch_source: str


def run_session(workcell: Workcell, specs, modes: list[str], time_limit: float,
                workcell_source: str = "", batch_source: str = "") -> Session:
    bad = [m for m in modes if m not in MODES]
    if bad:       # before any compiling or solving
        raise ValueError(f"unknown mode(s) {', '.join(bad)}; valid modes: {', '.join(MODES)}")
    batch = compile_batch(workcell, specs)
    runs = []
    for mode in modes:
        plan = make_plan(batch, mode, time_limit)
        errors = validate_plan(plan, batch)
        trace = execute(plan, batch)
        runs.append(RunResult(plan.mode, plan, trace, compute_stats(plan, trace, batch), errors))
    return Session(workcell, batch, runs, workcell_source, batch_source)


def run_files(workcell_path, batch_path, modes: list[str], time_limit: float) -> Session:
    specs, batch_source = load_batch(batch_path)
    return run_session(load_workcell(workcell_path), specs, modes, time_limit,
                       Path(workcell_path).read_text(), batch_source)
