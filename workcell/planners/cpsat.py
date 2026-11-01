"""CP-SAT planner (spec §7): an exact model of the batch with direct transports only."""
from __future__ import annotations

import atexit
import os
import pickle
import selectors
import subprocess
import sys
import tempfile
import time
import warnings
from collections import defaultdict
from dataclasses import dataclass
from typing import Literal

from ortools.sat.python import cp_model

from workcell.compiler import route
from workcell.ir import CompiledBatch, resolve
from workcell.plan import Plan, bind_batch
from workcell.planners.greedy import plan_greedy

Objective = Literal["makespan", "sum_completion"]
Hint = Literal["greedy", "serial"]

_STATUS = {"OPTIMAL": "optimal", "FEASIBLE": "feasible", "INFEASIBLE": "infeasible",
           "UNKNOWN": "no_solution", "CRASHED": "no_solution"}
_ATTEMPTS = 3       # ruling R7: child solves tried before giving up as "CRASHED"


def _var(name: str) -> str | None:
    """The `?var` a name refers to, or None for a concrete name."""
    return name.partition(".")[0] if name.startswith("?") else None


def _longest(b) -> int:
    """A block's longest duration over its route variants (its duration otherwise)."""
    if not b.variants:
        return b.duration
    return max(sum(i.duration for i in v) for v in b.variants.values())


def serial_hint(batch: CompiledBatch) -> tuple[dict[str, int], dict[str, dict[str, str]]]:
    """Ruling R5(b): a plan running one block at a time at planned durations. Workflows go
    in batch order; within one, the next block is the ready one (all deps done) that is a
    return, else the earliest compiled. Every var takes its first candidate. Valid for
    workflows whose returns free their nests before the next workflow needs them (e.g. PCR);
    not guaranteed otherwise. Returns (starts, bindings); raises ValueError if a workflow
    has no ready block (a dependency cycle or a dep outside the workflow).
    With several arms, a var moved into takes instead the candidate nearest the plate (fewest
    arm hops; ties rotate by workflow), so the warm start isn't relays everywhere."""
    wc = batch.workcell
    near = len(wc.of_type("arm")) > 1
    t, starts, bindings = 0, {}, {}
    for w, wf in enumerate(batch.workflows):
        bindings[wf.id] = {var: cands[0] for var, cands in wf.variables.items()}
        chosen: dict[str, str] = {}
        index = {b.id: i for i, b in enumerate(wf.blocks)}
        todo = list(wf.blocks)
        while todo:
            ready = [b for b in todo if all(d in starts for d in b.deps)]
            if not ready:
                raise ValueError(f"serial_hint: no ready block in workflow {wf.id!r} "
                                 f"(remaining: {[b.id for b in todo]})")
            nxt = min(ready, key=lambda b: (b.kind != "return", index[b.id]))
            if near:
                for m in nxt.moves:
                    var = _var(m.dst)
                    if var and var not in chosen:
                        here = wc.nest_owner(resolve(m.src, chosen))
                        cands = wf.variables[var]
                        chosen[var] = min(cands, key=lambda c: (
                            len(route(wc, here, c)), (cands.index(c) - w) % len(cands)))
                bindings[wf.id].update(chosen)
            starts[nxt.id] = t
            t += nxt.bind(bindings[wf.id]).duration if near else nxt.duration
            todo.remove(nxt)
    return starts, bindings


class _Model:
    def __init__(self, batch: CompiledBatch):
        self.batch = batch
        self.m = cp_model.CpModel()
        self.blocks = blocks = batch.all_blocks()
        self.size = {b.id: b.duration for b in blocks}
        horizon = sum(_longest(b) for b in blocks)
        self.start = {b.id: self.m.NewIntVar(0, horizon, f"s[{b.id}]") for b in blocks}
        self.end = {b.id: self.start[b.id] + self.size[b.id] for b in blocks}
        self.x = {(wf.id, var, cand): self.m.NewBoolVar(f"x[{wf.id},{var},{cand}]")
                  for wf in batch.workflows for var, cands in wf.variables.items()
                  for cand in cands}
        for wf in batch.workflows:
            for var, cands in wf.variables.items():
                self.m.AddExactlyOne(self.x[wf.id, var, c] for c in cands)
        self.horizon = horizon
        self._variants()
        self._dependencies()
        self._locks()
        self._stays()
        self._borrows()

    def _variants(self):
        """Route-variant blocks: one literal per variant (its bindings), and a size that is
        the chosen variant's duration."""
        self.vlits: dict[str, dict[tuple, object]] = {}
        for b in self.blocks:
            if not b.variants:
                continue
            lits = {}
            for key in b.variants:
                xs = [self.x[b.workflow, var, cand] for var, cand in key]
                lit = xs[0]
                for x in xs[1:]:
                    lit = self._and(lit, x)
                lits[key] = lit
            self.vlits[b.id] = lits
            sz = self.m.NewIntVar(0, self.horizon, f"size[{b.id}]")
            self.m.Add(sz == sum(sum(i.duration for i in b.variants[k]) * lit
                                 for k, lit in lits.items()))
            end = self.m.NewIntVar(0, self.horizon, f"end[{b.id}]")
            self.m.Add(end == self.start[b.id] + sz)
            self.size[b.id], self.end[b.id] = sz, end

    def _and(self, a, b):
        c = self.m.NewBoolVar("")
        self.m.AddBoolAnd([a, b]).OnlyEnforceIf(c)
        self.m.AddBoolOr([a.Not(), b.Not()]).OnlyEnforceIf(c.Not())
        return c

    def _choices(self, wf_id: str, name: str):
        """Yield (concrete name, presence literal or None) for each way `name` can resolve."""
        var = _var(name)
        if var is None:
            yield name, None
            return
        wf = next(w for w in self.batch.workflows if w.id == wf_id)
        for cand in wf.variables[var]:
            yield resolve(name, {var: cand}), self.x[wf_id, var, cand]

    def _interval(self, start, end, presence, size=None, name=""):
        size = size if size is not None else self.m.NewIntVar(0, self.horizon, f"len[{name}]")
        if presence is None:
            return self.m.NewIntervalVar(start, size, end, name)
        return self.m.NewOptionalIntervalVar(start, size, end, presence, name)

    def _dependencies(self):
        for b in self.blocks:
            for d in b.deps:
                self.m.Add(self.start[b.id] >= self.end[d])

    def _locks(self):
        """NoOverlap per concrete instrument and per plate over the blocks that lock them."""
        groups = defaultdict(list)
        for b in self.blocks:
            if b.id in self.vlits:          # route variants: each variant's own instruments
                for key, lit in sorted(self.vlits[b.id].items()):
                    for name in sorted({i.instrument for i in b.variants[key]}):
                        groups[name].append(self._interval(
                            self.start[b.id], self.end[b.id], lit, self.size[b.id],
                            f"{b.id}@{name}:{key}"))
            else:
                concrete = {i for i in b.instruments if _var(i) is None}
                for inst in sorted(b.instruments):
                    for name, lit in self._choices(b.workflow, inst):
                        if lit is not None and name in concrete:
                            continue      # already locked by the concrete interval
                        groups[name].append(self._interval(
                            self.start[b.id], self.end[b.id], lit, self.size[b.id],
                            f"{b.id}@{name}"))
            for plate in sorted(b.plates):
                groups[plate].append(self._interval(
                    self.start[b.id], self.end[b.id], None, self.size[b.id], f"{b.id}@{plate}"))
        for ivs in groups.values():
            self.m.AddNoOverlap(ivs)

    def _stays(self):
        """Ruling R4: a plate occupies a nest from the start of the block that places it to
        the start of the block that removes it. Storage and hotel stays are skipped."""
        wc = self.batch.workcell
        nests = wc.nests()
        skip = {n for n, nest in nests.items()
                if wc.instruments[nest.instrument].type in ("storage", "hotel")}
        by_nest = defaultdict(list)
        for wf in self.batch.workflows:
            chains = defaultdict(list)      # plate -> its moving blocks in compiled order
            for b in wf.blocks:
                for mv in b.moves:
                    chains[mv.plate].append((b, mv))
            for plate, chain in chains.items():
                for (a, mv), (d, _) in zip(chain, chain[1:]):
                    self.m.Add(self.start[d.id] >= self.end[a.id])
                    for nest, lit in self._choices(wf.id, mv.dst):
                        if nest in skip:
                            continue
                        by_nest[nest].append(self._interval(
                            self.start[a.id], self.start[d.id], lit,
                            name=f"stay[{plate}@{nest}:{a.id}]"))
        for nest, ivs in by_nest.items():
            if nests[nest].capacity == 1:
                self.m.AddNoOverlap(ivs)
            else:
                self.m.AddCumulative(ivs, [1] * len(ivs), nests[nest].capacity)

    def _borrows(self):
        spans = defaultdict(list)
        for wf in self.batch.workflows:
            for plate in sorted(wf.borrowed):
                first, last = wf.borrow_span(plate)
                spans[plate].append(self._interval(self.start[first], self.end[last], None,
                                                   name=f"borrow[{plate}:{wf.id}]"))
        for ivs in spans.values():
            self.m.AddNoOverlap(ivs)

    def objective(self, objective: Objective):
        self.mk = self.m.NewIntVar(0, self.horizon, "makespan")
        self.m.AddMaxEquality(self.mk, list(self.end.values()))
        if objective == "makespan":
            self.m.Minimize(self.mk)
        elif objective == "sum_completion":
            done = []
            for wf in self.batch.workflows:
                c = self.m.NewIntVar(0, self.horizon, f"done[{wf.id}]")
                self.m.AddMaxEquality(c, [self.end[b.id] for b in wf.blocks])
                done.append(c)
            self.m.Minimize(sum(done))
        else:
            raise ValueError(f"unknown CP-SAT objective {objective!r}")

    def hint(self):
        """Ruling R6(c): hint from greedy_hotel fifo — compiled block starts and bindings."""
        g = plan_greedy(self.batch, priority="fifo", park=True)
        if g.status != "complete":
            return
        for bid, s in g.start.items():
            if bid in self.start and not g.blocks[bid].inserted:
                self.m.AddHint(self.start[bid], s)
        for (wf, var, cand), lit in self.x.items():
            self.m.AddHint(lit, g.bindings.get(wf, {}).get(var) == cand)

    def hint_starts(self, starts: dict[str, int]):
        for bid, s in starts.items():
            if bid in self.start:
                self.m.AddHint(self.start[bid], s)

    def hint_bindings(self, bindings: dict[str, dict[str, str]]):
        """Hint each listed var's literals: 1 for its hinted candidate, 0 for the others."""
        for (wf, var, cand), lit in self.x.items():
            if var in bindings.get(wf, {}):
                self.m.AddHint(lit, bindings[wf][var] == cand)


def plan_cpsat(batch: CompiledBatch, objective: Objective = "makespan",
               time_limit: float = 30.0, hint: Hint | bool | None = True,
               deterministic: bool = False) -> Plan:
    """Solve the batch with CP-SAT; status "optimal", "feasible", "infeasible" (proven), or
    "no_solution" (time limit / unknown). An invalid model is a bug and raises RuntimeError.
    `hint` warm-starts the solve: "greedy" (or True) from greedy_hotel fifo, "serial" from
    `serial_hint`, None (or False) not at all. `deterministic` (ruling R5) uses 8
    interleaved workers, a fixed seed and a deterministic-time limit (`time_limit`) instead
    of wall time, so the same inputs give the same plan in any process; it also solves in a
    child process (ruling R7), retried if it crashes, with status "no_solution" and
    solver_status "CRASHED" if every attempt does."""
    t0 = time.perf_counter()
    model = _Model(batch)
    model.objective(objective)
    if hint is True or hint == "greedy":
        model.hint()
    elif hint == "serial":
        starts, bindings = serial_hint(batch)
        model.hint_starts(starts)
        model.hint_bindings(bindings)
    elif hint not in (None, False):
        raise ValueError(f"unknown CP-SAT hint {hint!r}")
    solver = cp_model.CpSolver()
    solver.parameters.num_workers = 8
    if deterministic:
        solver.parameters.interleave_search = True
        solver.parameters.random_seed = 0
        solver.parameters.max_deterministic_time = time_limit
    else:
        solver.parameters.max_time_in_seconds = time_limit
    sol = _solve(model.m, solver, isolate=deterministic)
    if sol.status not in _STATUS:
        raise RuntimeError(
            f"CP-SAT returned {sol.status}: {model.m.Validate() or 'invalid model'}")
    status = _STATUS[sol.status]
    mode = f"cpsat:{objective}"
    if status in ("infeasible", "no_solution"):
        return Plan(mode=mode, blocks={}, start={}, bindings={}, status=status,
                    solve_seconds=time.perf_counter() - t0, objective=objective,
                    solver_status=sol.status)
    bindings = {wf.id: {} for wf in batch.workflows}
    for (wf, var, cand), lit in model.x.items():
        if sol.values[lit.Index()]:
            bindings[wf][var] = cand
    return Plan(mode=mode, blocks=bind_batch(batch, bindings),
                start={bid: sol.values[s.Index()] for bid, s in model.start.items()},
                bindings=bindings, status=status, solve_seconds=time.perf_counter() - t0,
                objective=objective, solver_status=sol.status)


@dataclass
class _Solution:
    """A solve's outcome: CP-SAT status name (or "CRASHED"), objective value (None without a
    solution), the value of every model variable by proto index, and solver wall time."""
    status: str
    objective: float | None
    values: list[int]
    wall: float


def _run(m: cp_model.CpModel, solver: cp_model.CpSolver) -> _Solution:
    code = solver.Solve(m)
    found = code in (cp_model.OPTIMAL, cp_model.FEASIBLE)
    return _Solution(solver.StatusName(code), solver.ObjectiveValue() if found else None,
                     list(solver.ResponseProto().solution), solver.WallTime())


def _solve(m: cp_model.CpModel, solver: cp_model.CpSolver, isolate: bool) -> _Solution:
    """Solve here, or (ruling R7) in a child process so an OR-Tools abort cannot kill us."""
    if not isolate:
        return _run(m, solver)
    params = solver.parameters
    return _solve_isolated(str(m.Proto()), str(params),
                           _child_timeout(params.max_deterministic_time))


def _child_timeout(time_limit: float) -> float:
    """Wall-clock seconds a child solve may take before it counts as a failed attempt. Generous:
    deterministic seconds can take ~10x as long in wall time on a loaded machine, and a
    timeout must not decide a live run's outcome."""
    return max(300.0, 60 * time_limit)


def _worker_main() -> None:
    """Solver process entry: serve requests until stdin closes. Each request and reply is an
    8-byte big-endian length then a pickle: (model, parameters) in text format -> `_Solution`.
    Stray prints go to stderr so they can't corrupt the reply stream."""
    inp = sys.stdin.buffer
    out = os.fdopen(os.dup(1), "wb")
    os.dup2(2, 1)
    while True:
        head = inp.read(8)
        if len(head) < 8:
            return
        model_text, params_text = pickle.loads(inp.read(int.from_bytes(head, "big")))
        m = cp_model.CpModel()
        m.Proto().parse_text_format(model_text)
        solver = cp_model.CpSolver()
        solver.parameters.parse_text_format(params_text)
        data = pickle.dumps(_run(m, solver))
        out.write(len(data).to_bytes(8, "big") + data)
        out.flush()


# the solver process command; a test seam (replaced to inject crashes)
_CHILD_CMD = [sys.executable, "-c",
              "from workcell.planners.cpsat import _worker_main; _worker_main()"]


class _Dead(Exception):
    def __init__(self, code: int):
        self.code = code


class _Worker:
    """A long-lived solver process (ruling R7: an OR-Tools abort kills it, not us)."""

    def __init__(self, cmd: list[str]):
        self.cmd = list(cmd)
        self.err = tempfile.TemporaryFile()
        self.proc = subprocess.Popen(self.cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=self.err)

    def stderr_tail(self) -> str:
        self.err.seek(0)
        return self.err.read().decode(errors="replace")[-2000:]

    def kill(self) -> None:
        if self.proc.poll() is None:
            self.proc.kill()
        self.proc.wait()

    def ask(self, request: bytes, timeout: float) -> bytes:
        """Send one request and read its reply; TimeoutError, or _Dead(exit code)."""
        try:
            self.proc.stdin.write(len(request).to_bytes(8, "big") + request)
            self.proc.stdin.flush()
        except BrokenPipeError:
            raise _Dead(self.proc.wait()) from None
        deadline = time.monotonic() + timeout
        head = self._read(8, deadline)
        return self._read(int.from_bytes(head, "big"), deadline)

    def _read(self, n: int, deadline: float) -> bytes:
        fd, buf = self.proc.stdout.fileno(), bytearray()
        with selectors.DefaultSelector() as sel:
            sel.register(fd, selectors.EVENT_READ)
            while len(buf) < n:
                left = deadline - time.monotonic()
                if left <= 0 or not sel.select(left):
                    raise TimeoutError
                chunk = os.read(fd, min(n - len(buf), 1 << 20))
                if not chunk:
                    raise _Dead(self.proc.wait())
                buf += chunk
        return bytes(buf)


_WORKER: _Worker | None = None


def _worker() -> _Worker:
    global _WORKER
    if _WORKER is None or _WORKER.proc.poll() is not None or _WORKER.cmd != _CHILD_CMD:
        if _WORKER is not None:
            _WORKER.kill()
        _WORKER = _Worker(_CHILD_CMD)
    return _WORKER


def _drop_worker() -> None:
    global _WORKER
    if _WORKER is not None:
        _WORKER.kill()
        _WORKER = None


atexit.register(_drop_worker)


def _solve_isolated(model_text: str, params_text: str, timeout: float) -> _Solution:
    """Solve in the solver process. A process killed by a signal (the OR-Tools abort), hung
    past `timeout`, or returning an unreadable result is restarted and the solve retried
    (deterministic, so the same answer), up to `_ATTEMPTS` times in all, then given up with
    status "CRASHED". A process exiting with an error code failed in Python,
    deterministically: raise RuntimeError. If every attempt timed out, raise too: a
    load-dependent "no solution" would make the same inputs give different live runs."""
    request = pickle.dumps((model_text, params_text))
    why = ""
    timeouts = 0
    for _ in range(_ATTEMPTS):
        w = _worker()
        try:
            reply = w.ask(request, timeout)
        except TimeoutError:
            why = f"timed out after {timeout:g} s"
            timeouts += 1
            _drop_worker()
            continue
        except _Dead as dead:
            err = w.stderr_tail()
            _drop_worker()
            if dead.code > 0:
                raise RuntimeError(f"CP-SAT child exited {dead.code}:\n{err}") from None
            why = (f"killed by signal {-dead.code}:\n{err}" if dead.code < 0
                   else f"exited before replying:\n{err}")
            continue
        try:
            return pickle.loads(reply)
        except Exception as e:      # noqa: BLE001 — any unreadable result is a failed attempt
            why = f"unreadable result ({e!r}):\n{w.stderr_tail()}"
            _drop_worker()
    if timeouts == _ATTEMPTS:
        raise RuntimeError(f"CP-SAT child timed out {_ATTEMPTS} times ({why}); "
                           "the machine may be overloaded")
    warnings.warn(f"CP-SAT child failed {_ATTEMPTS} times; last {why}", RuntimeWarning,
                  stacklevel=3)
    return _Solution("CRASHED", None, [], 0.0)
