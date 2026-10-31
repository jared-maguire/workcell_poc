"""Command line interface: `python -m workcell run|scale|live ...`."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

from workcell.compiler import CompileError
from workcell.config import ConfigError, parse_workcell
from workcell.config import load_workcell
from workcell.dsl import BatchFileError, load_batch
from workcell.live.session import run_live
from workcell.planners import MODES
from workcell.report import write_report
from workcell.run import run_files, run_session
from workcell.scenarios import scale_specs, scale_workcell_dict

DEFAULT_MODES = ["greedy", "greedy_hotel", "cpsat:makespan", "cpsat:sum_completion"]


def default_modes(workcell) -> list[str]:
    """The default modes, without the greedy planners on a multi-arm workcell (one arm only)."""
    if len(workcell.of_type("arm")) > 1:
        return [m for m in DEFAULT_MODES if not m.startswith("greedy")]
    return list(DEFAULT_MODES)
HEADERS = ["mode", "status", "makespan", "done", "mean latency", "solve s"]


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="workcell")
    sub = p.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="plan, simulate and report a batch")
    run.add_argument("workcell", help="workcell YAML file")
    run.add_argument("batch", help="Python file defining batch()")
    run.add_argument("--mode", action="append", dest="modes", metavar="M", choices=MODES,
                     help=f"planner mode, repeatable (default: {' '.join(DEFAULT_MODES)}; "
                          f"valid: {', '.join(MODES)})")
    run.add_argument("--time-limit", type=float, default=30, metavar="S",
                     help="CP-SAT time limit in seconds (default 30)")
    run.add_argument("--out", default="out/report.html", help="report path (default out/report.html)")
    scale = sub.add_parser("scale", help="run a generated PCR batch at scale")
    scale.add_argument("--workflows", type=int, required=True, metavar="N")
    scale.add_argument("--stampers", type=int, default=4)
    scale.add_argument("--thermocyclers", type=int, default=16)
    scale.add_argument("--scanners", type=int, default=2)
    scale.add_argument("--primers", type=int, default=8)
    scale.add_argument("--mode", action="append", dest="modes", metavar="M", choices=MODES,
                       help=f"planner mode, repeatable (default: {' '.join(DEFAULT_MODES)}; "
                            f"valid: {', '.join(MODES)})")
    scale.add_argument("--time-limit", type=float, default=60, metavar="S",
                       help="CP-SAT time limit in seconds (default 60)")
    scale.add_argument("--out", default="out/scale.html", help="report path (default out/scale.html)")
    live = sub.add_parser("live", help="run a batch live: random step times, re-plan on long delays")
    live.add_argument("workcell", help="workcell YAML file")
    live.add_argument("batch", help="Python file defining batch() (use @workflow(times=...) for real times)")
    live.add_argument("--seed", type=int, default=0)
    live.add_argument("--replan-threshold", type=int, default=300, metavar="SECONDS",
                      help="re-plan when a delay would push a step later by more than this (default 300)")
    live.add_argument("--replan-time-limit", type=float, default=2, metavar="S",
                      help="CP-SAT deterministic seconds per re-plan phase (default 2)")
    live.add_argument("--time-limit", type=float, default=30, metavar="S",
                      help="CP-SAT deterministic seconds for the initial plan (default 30)")
    live.add_argument("--noise", type=float, default=0.05, help="relative sigma of step times (default 0.05)")
    live.add_argument("--delay-prob", type=float, default=0.02,
                      help="chance a step runs 2-5x long (default 0.02)")
    live.add_argument("--grid", type=int, default=10, metavar="SECONDS",
                      help="plan in units of this many seconds (default 10; 1 = exact)")
    live.add_argument("--window-hours", type=float, default=4, metavar="H",
                      help="plan this many hours ahead, extending as the run goes "
                           "(default 4; 0 = plan the whole batch at once)")
    live.add_argument("--max-idle-hold", type=int, default=300, metavar="SECONDS",
                      help="longest an instrument may sit idle holding a plate; longer waits park "
                           "the plate in a hotel or storage (default 300; 0 disables)")
    live.add_argument("--out", default="out/live.html", help="report path (default out/live.html)")
    return p


def _table(rows: list[list[str]]) -> str:
    widths = [max(len(r[i]) for r in [HEADERS, *rows]) for i in range(len(HEADERS))]
    fmt = lambda r: "  ".join(c.ljust(w) for c, w in zip(r, widths)).rstrip()
    return "\n".join([fmt(HEADERS), *map(fmt, rows)])


def _scale_session(args, argv):
    n = args.workflows
    data = scale_workcell_dict(args.stampers, args.thermocyclers, args.scanners,
                               samples=n, primers=args.primers, empties=n)
    command = "python -m workcell " + " ".join(argv if argv is not None else sys.argv[1:])
    return run_session(parse_workcell(data), scale_specs(n, args.primers), args.modes or DEFAULT_MODES,
                       args.time_limit, yaml.safe_dump(data, sort_keys=False), command)


def _print_session(session, out: str) -> None:
    rows = []
    for r in session.runs:
        s = r.stats
        lat = "-" if s.mean_latency is None else f"{s.mean_latency:.1f}"
        rows.append([r.mode, s.status, str(s.makespan), f"{s.completed}/{s.total}", lat,
                     f"{s.solve_seconds:.2f}"])
    print(_table(rows))
    print(f"report: {write_report(session, out)}")


def _hms(sec: int) -> str:
    return f"{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


def _live(args) -> int:
    specs, source = load_batch(args.batch)
    ls = run_live(load_workcell(args.workcell), specs, seed=args.seed, noise=args.noise,
                  delay_prob=args.delay_prob, replan_threshold=args.replan_threshold,
                  replan_time_limit=args.replan_time_limit, time_limit=args.time_limit,
                  workcell_source=Path(args.workcell).read_text(), batch_source=source,
                  max_idle_hold=args.max_idle_hold, grid=args.grid,
                  window=round(args.window_hours * 3600) or None)
    L, s = ls.live, ls.session.runs[0].stats
    print(f"seed: {L.seed}")
    live = ls.session.live
    print(f"planning: grid {live['grid']}s, "
          + (f"window {live['window'] / 3600:g} h" if live["window"] else "whole batch"))
    print(f"planned makespan: {_hms(L.planned_makespan) if L.planned_makespan is not None else '— (rolling window)'}")
    print(f"actual makespan:  {_hms(L.actual_makespan)}")
    print(f"re-plans: {L.replans} ({L.adopted_replans} adopted, {L.not_worth_it} not worth it, "
          f"{L.failed_replans} failed)")
    if L.max_idle_hold:
        print(f"idle hold: limit {L.max_idle_hold}s, parks {L.parks}, "
              f"violations {L.idle_violations} (worst {L.worst_idle}s)")
    print(f"completed: {s.completed}/{s.total}")
    if ls.errors:
        print(f"as-run validation errors: {len(ls.errors)}", file=sys.stderr)
    print(f"report: {write_report(ls.session, args.out)}")
    return 0


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "live":
            return _live(args)
        if args.command == "scale":
            session = _scale_session(args, argv)
        else:
            modes = args.modes
            if not modes:
                modes = default_modes(load_workcell(args.workcell))
                if modes != DEFAULT_MODES:
                    print("note: several arms; skipping the greedy modes (one arm only)",
                          file=sys.stderr)
            session = run_files(args.workcell, args.batch, modes, args.time_limit)
    except (ConfigError, CompileError, BatchFileError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    _print_session(session, args.out)
    return 0
