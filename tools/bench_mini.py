"""Small, fast scheduler experiments: timed PCR on the small test workcell, live mode, tiny
budgets. Usage: .venv/bin/python tools/bench_mini.py [n_workflows] (prints one row per config)."""
import sys
import time

sys.path.insert(0, "tests")
from conftest import SMALL  # noqa: E402

from workcell.config import parse_workcell  # noqa: E402
from workcell.dsl import load_batch  # noqa: E402
from workcell.live.session import run_live  # noqa: E402

N = int(sys.argv[1]) if len(sys.argv) > 1 else 4
SPECS = load_batch("examples/pcr_live.py")[0][:N]
WC = parse_workcell(SMALL)
CONFIGS = [("exact", dict(grid=1, window=None)), ("grid10", dict(grid=10, window=None)),
           ("grid10+win1h", dict(grid=10, window=3600))]

for name, kw in CONFIGS:
    t = time.perf_counter()
    try:
        ls = run_live(WC, SPECS, seed=0, time_limit=2, replan_time_limit=0.3, **kw)
        L = ls.live
        print(f"{name:14s} wall {time.perf_counter() - t:5.1f}s  solve "
              f"{ls.session.live['solve_seconds']['initial']:4.1f}+"
              f"{ls.session.live['solve_seconds']['replans']:4.1f}s  makespan "
              f"{L.actual_makespan:6d}  replans {L.replans:3d} ({L.adopted_replans} adopted)  "
              f"errors {len(ls.errors)}", flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"{name:14s} wall {time.perf_counter() - t:5.1f}s  FAILED {type(e).__name__}: {e}",
              flush=True)
