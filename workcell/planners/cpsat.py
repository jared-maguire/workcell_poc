"""CP-SAT planner (spec §7): an exact model of the batch with direct transports only."""
from __future__ import annotations

import pickle
import subprocess
import sys
import time
import warnings
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Literal

from ortools.sat.python import cp_model

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


@dataclass
class ReplanState:
    """What has already happened when re-planning at time `now`: `fixed` maps each done or
    running block to its (start, end); `bindings` are var bindings that must stay; `hint`
    holds suggested starts and `hint_bindings` suggested var bindings (when either is
    non-empty they replace `plan_cpsat`'s own `hint`)."""
    now: int
    fixed: dict[str, tuple[int, int]]
    bindings: dict[str, dict[str, str]]
    hint: dict[str, int] = field(default_factory=dict)
    hint_bindings: dict[str, dict[str, str]] = field(default_factory=dict)


def serial_hint(batch: CompiledBatch) -> tuple[dict[str, int], dict[str, dict[str, str]]]:
    """Ruling R5(b): a plan running one block at a time at planned durations. Workflows go
    in batch order; within one, the next block is the ready one (all deps done) that is a
    return, else the earliest compiled. Every var takes its first candidate. Valid for
    workflows whose returns free their nests before the next workflow needs them (e.g. PCR);
    not guaranteed otherwise. Returns (starts, bindings); raises ValueError if a workflow
    has no ready block (a dependency cycle or a dep outside the workflow)."""
    t, starts, bindings = 0, {}, {}
    for wf in batch.workflows:
        bindings[wf.id] = {var: cands[0] for var, cands in wf.variables.items()}
        index = {b.id: i for i, b in enumerate(wf.blocks)}
        todo = list(wf.blocks)
        while todo:
            ready = [b for b in todo if all(d in starts for d in b.deps)]
            if not ready:
                raise ValueError(f"serial_hint: no ready block in workflow {wf.id!r} "
                                 f"(remaining: {[b.id for b in todo]})")
            nxt = min(ready, key=lambda b: (b.kind != "return", index[b.id]))
            starts[nxt.id] = t
            t += nxt.duration
            todo.remove(nxt)
    return starts, bindings


class _Model:
    def __init__(self, batch: CompiledBatch, state: ReplanState | None = None):
        self.batch = batch
        self.m = cp_model.CpModel()
        blocks = batch.all_blocks()
        fixed = state.fixed if state else {}
        # interval size per block: actual (end - start) for fixed blocks, else planned duration
        self.size = {b.id: fixed[b.id][1] - fixed[b.id][0] if b.id in fixed else b.duration
                     for b in blocks}
        if state is None:
            horizon = sum(b.duration for b in blocks)
            bounds = {b.id: (0, horizon) for b in blocks}
        else:
            horizon = (max([state.now] + [e for _, e in fixed.values()])
                       + sum(b.duration for b in blocks if b.id not in fixed))
            bounds = {b.id: (fixed[b.id][0],) * 2 if b.id in fixed else (state.now, horizon)
                  for b in blocks}
        self.start = {b.id: self.m.NewIntVar(*bounds[b.id], f"s[{b.id}]") for b in blocks}
        self.end = {b.id: self.start[b.id] + self.size[b.id] for b in blocks}
        self.x = {(wf.id, var, cand): self.m.NewBoolVar(f"x[{wf.id},{var},{cand}]")
                  for wf in batch.workflows for var, cands in wf.variables.items()
                  for cand in cands}
        for wf in batch.workflows:
            for var, cands in wf.variables.items():
                self.m.AddExactlyOne(self.x[wf.id, var, c] for c in cands)
        if state is not None:
            for wf_id, binds in state.bindings.items():
                for var, cand in binds.items():
                    self.m.Add(self.x[wf_id, var, cand] == 1)
        self.horizon = horizon
        self._dependencies()
        self._locks()
        self._stays()
        self._borrows()

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
        for b in self.batch.all_blocks():
            for d in b.deps:
                self.m.Add(self.start[b.id] >= self.end[d])

    def _locks(self):
        """NoOverlap per concrete instrument and per plate over the blocks that lock them."""
        groups = defaultdict(list)
        for b in self.batch.all_blocks():
            concrete = {i for i in b.instruments if _var(i) is None}
            for inst in sorted(b.instruments):
                for name, lit in self._choices(b.workflow, inst):
                    if lit is not None and name in concrete:
                        continue      # already locked by the concrete interval
                    groups[name].append(self._interval(
                        self.start[b.id], self.end[b.id], lit, self.size[b.id], f"{b.id}@{name}"))
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
        if objective == "makespan":
            mk = self.m.NewIntVar(0, self.horizon, "makespan")
            self.m.AddMaxEquality(mk, list(self.end.values()))
            self.m.Minimize(mk)
        elif objective == "sum_completion":
            done = []
            for wf in self.batch.workflows:
                c = self.m.NewIntVar(0, self.horizon, f"done[{wf.id}]")
                self.m.AddMaxEquality(c, [self.end[b.id] for b in wf.blocks])
                done.append(c)
            self.m.Minimize(sum(done))
        else:
            raise ValueError(f"unknown CP-SAT objective {objective!r}")

    def churn_objective(self, state: ReplanState, cap: int, weight: int):
        """Ruling R8 phase 2: hold makespan at `cap` and minimize changes to the current plan —
        `weight` per binding that differs from `state.hint_bindings`, plus the total distance
        of each unfixed block's start from its `state.hint`."""
        for e in self.end.values():
            self.m.Add(e <= cap)
        terms = []
        for (wf, var, cand), lit in self.x.items():
            if state.hint_bindings.get(wf, {}).get(var) == cand:
                terms.append(weight * (1 - lit))
        for bid, h in sorted(state.hint.items()):
            if bid in self.start and bid not in state.fixed:
                d = self.m.NewIntVar(0, self.horizon, f"dev[{bid}]")
                self.m.AddAbsEquality(d, self.start[bid] - h)
                terms.append(d)
        self.m.Minimize(sum(terms))

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
               state: ReplanState | None = None, deterministic: bool = False) -> Plan:
    """Solve the batch with CP-SAT; status "optimal", "feasible", "infeasible" (proven), or
    "no_solution" (time limit / unknown). An invalid model is a bug and raises RuntimeError.

    `hint` warm-starts the solve: "greedy" (or True) from greedy_hotel fifo, "serial" from
    `serial_hint`, None (or False) not at all. `state` re-plans from the current time: fixed
    blocks keep their actual (start, end), the rest start at or after `state.now`; its
    non-empty `hint`/`hint_bindings` replace `hint`. `deterministic` (ruling R5) uses 8
    interleaved workers, a fixed seed and a deterministic-time limit (`time_limit`) instead
    of wall time, so the same inputs give the same plan in any process; it also solves in a
    child process (ruling R7), retried if it crashes, with status "no_solution" and
    solver_status "CRASHED" if every attempt does."""
    t0 = time.perf_counter()
    model = _Model(batch, state)
    model.objective(objective)
    if state is not None and (state.hint or state.hint_bindings):
        model.hint_starts(state.hint)
        model.hint_bindings(state.hint_bindings)
    elif hint is True or hint == "greedy":
        model.hint()
    elif hint == "serial":
        starts, bindings = serial_hint(batch)
        model.hint_starts(starts)
        model.hint_bindings(bindings)
    elif hint not in (None, False):
        raise ValueError(f"unknown CP-SAT hint {hint!r}")
    return _solve_plan(model, time_limit, deterministic, objective, t0)[0]


def replan_cpsat(batch: CompiledBatch, state: ReplanState, time_limit: float = 2.0,
                 reassign_weight: int = 600) -> Plan:
    """Ruling R8: a low-churn re-plan. Phase 1 minimizes makespan from `state`; phase 2 holds
    that makespan and minimizes churn against `state.hint`/`state.hint_bindings` (see
    `_Model.churn_objective`). Both are deterministic and isolated, each with `time_limit`.
    If phase 2 finds nothing, phase 1's plan is returned. solver_status is "<p1>/<p2>"."""
    t0 = time.perf_counter()
    m1 = _Model(batch, state)
    m1.objective("makespan")
    m1.hint_starts(state.hint)
    m1.hint_bindings(state.hint_bindings)
    first, sol1 = _solve_plan(m1, time_limit, True, "makespan", t0)
    if not first.finished:
        return first
    m2 = _Model(batch, state)
    m2.churn_objective(state, round(sol1.objective), reassign_weight)
    m2.hint_starts(first.start)
    m2.hint_bindings(first.bindings)
    second, sol2 = _solve_plan(m2, time_limit, True, "makespan", t0)
    best = second if second.finished else first
    best.solver_status = f"{sol1.status}/{sol2.status}"
    best.solve_seconds = time.perf_counter() - t0
    return best


def _solve_plan(model: _Model, time_limit: float, deterministic: bool, objective: str,
                t0: float) -> tuple[Plan, "_Solution"]:
    """Solve `model` and turn the solution into a Plan (see plan_cpsat for the parameters)."""
    batch = model.batch
    solver = cp_model.CpSolver()
    if deterministic:
        solver.parameters.num_workers = 8
        solver.parameters.interleave_search = True
        solver.parameters.random_seed = 0
        solver.parameters.max_deterministic_time = time_limit
    else:
        solver.parameters.num_workers = 8
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
                    solver_status=sol.status), sol
    bindings = {wf.id: {} for wf in batch.workflows}
    for (wf, var, cand), lit in model.x.items():
        if sol.values[lit.Index()]:
            bindings[wf][var] = cand
    return Plan(mode=mode, blocks=bind_batch(batch, bindings),
                start={bid: sol.values[s.Index()] for bid, s in model.start.items()},
                bindings=bindings, status=status, solve_seconds=time.perf_counter() - t0,
                objective=objective, solver_status=sol.status), sol


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


def _child_main() -> None:
    """Child process entry: read (model, parameters) in text format as a pickle on stdin,
    solve, write the pickled `_Solution` to stdout."""
    model_text, params_text = pickle.load(sys.stdin.buffer)
    m = cp_model.CpModel()
    m.Proto().parse_text_format(model_text)
    solver = cp_model.CpSolver()
    solver.parameters.parse_text_format(params_text)
    pickle.dump(_run(m, solver), sys.stdout.buffer)


# the child command; a test seam (replaced to inject child crashes)
_CHILD_CMD = [sys.executable, "-c",
              "from workcell.planners.cpsat import _child_main; _child_main()"]


def _solve_isolated(model_text: str, params_text: str, timeout: float) -> _Solution:
    """Solve in a child process. A child killed by a signal (the OR-Tools abort), hung past
    `timeout`, or returning an unreadable result is retried (deterministic, so the same
    answer), up to `_ATTEMPTS` times in all, then given up with status "CRASHED". A child
    exiting with an error code failed in Python, deterministically: raise RuntimeError. If
    every attempt timed out, raise too: a load-dependent "no solution" would make the same
    inputs give different live runs."""
    request = pickle.dumps((model_text, params_text))
    why = ""
    timeouts = 0
    for _ in range(_ATTEMPTS):
        try:
            child = subprocess.run(_CHILD_CMD, input=request, capture_output=True,
                                   timeout=timeout)
        except subprocess.TimeoutExpired:
            why = f"timed out after {timeout:g} s"
            timeouts += 1
            continue
        err = child.stderr.decode(errors="replace")[-2000:]
        if child.returncode > 0:
            raise RuntimeError(f"CP-SAT child exited {child.returncode}:\n{err}")
        if child.returncode < 0:
            why = f"killed by signal {-child.returncode}:\n{err}"
            continue
        try:
            return pickle.loads(child.stdout)
        except Exception as e:      # noqa: BLE001 — any unreadable result is a failed attempt
            why = f"unreadable result ({e!r}):\n{err}"
    if timeouts == _ATTEMPTS:
        raise RuntimeError(f"CP-SAT child timed out {_ATTEMPTS} times ({why}); "
                           "the machine may be overloaded")
    warnings.warn(f"CP-SAT child failed {_ATTEMPTS} times; last {why}", RuntimeWarning,
                  stacklevel=3)
    return _Solution("CRASHED", None, [], 0.0)
