"""Command line interface: `python -m workcell run WORKCELL BATCH`."""
from __future__ import annotations

import argparse
import sys

from workcell.compiler import CompileError
from workcell.config import ConfigError
from workcell.report import write_report
from workcell.run import run_files

DEFAULT_MODES = ["greedy", "greedy_hotel", "cpsat:makespan", "cpsat:sum_completion"]
HEADERS = ["mode", "status", "makespan", "done", "mean latency", "solve s"]


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="workcell")
    sub = p.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="plan, simulate and report a batch")
    run.add_argument("workcell", help="workcell YAML file")
    run.add_argument("batch", help="Python file defining batch()")
    run.add_argument("--mode", action="append", dest="modes", metavar="M",
                     help=f"planner mode, repeatable (default: {' '.join(DEFAULT_MODES)})")
    run.add_argument("--time-limit", type=float, default=30, metavar="S",
                     help="CP-SAT time limit in seconds (default 30)")
    run.add_argument("--out", default="out/report.html", help="report path (default out/report.html)")
    return p


def _table(rows: list[list[str]]) -> str:
    widths = [max(len(r[i]) for r in [HEADERS, *rows]) for i in range(len(HEADERS))]
    fmt = lambda r: "  ".join(c.ljust(w) for c, w in zip(r, widths)).rstrip()
    return "\n".join([fmt(HEADERS), *map(fmt, rows)])


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    try:
        session = run_files(args.workcell, args.batch, args.modes or DEFAULT_MODES, args.time_limit)
    except (ConfigError, CompileError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    rows = []
    for r in session.runs:
        s = r.stats
        lat = "-" if s.mean_latency is None else f"{s.mean_latency:.1f}"
        rows.append([r.mode, s.status, str(s.makespan), f"{s.completed}/{s.total}", lat,
                     f"{s.solve_seconds:.2f}"])
    print(_table(rows))
    print(f"report: {write_report(session, args.out)}")
    return 0
