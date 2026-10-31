"""CP-SAT planner (spec §7): an exact model of the batch with direct transports only."""
from __future__ import annotations

import time
from collections import defaultdict
from typing import Literal

from ortools.sat.python import cp_model

from workcell.ir import CompiledBatch, resolve
from workcell.plan import Plan, bind_batch
from workcell.planners.greedy import plan_greedy

Objective = Literal["makespan", "sum_completion"]

_STATUS = {cp_model.OPTIMAL: "optimal", cp_model.FEASIBLE: "feasible",
           cp_model.INFEASIBLE: "infeasible", cp_model.UNKNOWN: "no_solution"}


def _var(name: str) -> str | None:
    """The `?var` a name refers to, or None for a concrete name."""
    return name.partition(".")[0] if name.startswith("?") else None


class _Model:
    def __init__(self, batch: CompiledBatch):
        self.batch = batch
        self.m = cp_model.CpModel()
        blocks = batch.all_blocks()
        horizon = sum(b.duration for b in blocks)
        self.start = {b.id: self.m.NewIntVar(0, horizon, f"s[{b.id}]") for b in blocks}
        self.end = {b.id: self.start[b.id] + b.duration for b in blocks}
        self.x = {(wf.id, var, cand): self.m.NewBoolVar(f"x[{wf.id},{var},{cand}]")
                  for wf in batch.workflows for var, cands in wf.variables.items()
                  for cand in cands}
        for wf in batch.workflows:
            for var, cands in wf.variables.items():
                self.m.AddExactlyOne(self.x[wf.id, var, c] for c in cands)
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
            for inst in b.instruments:
                for name, lit in self._choices(b.workflow, inst):
                    if lit is not None and name in concrete:
                        continue      # already locked by the concrete interval
                    groups[name].append(self._interval(
                        self.start[b.id], self.end[b.id], lit, b.duration, f"{b.id}@{name}"))
            for plate in b.plates:
                groups[plate].append(self._interval(
                    self.start[b.id], self.end[b.id], None, b.duration, f"{b.id}@{plate}"))
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
            for plate in wf.borrowed:
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


def plan_cpsat(batch: CompiledBatch, objective: Objective = "makespan",
               time_limit: float = 30.0, hint: bool = True) -> Plan:
    """Solve the batch with CP-SAT; status "optimal", "feasible", "infeasible" (proven), or
    "no_solution" (time limit / unknown). An invalid model is a bug and raises RuntimeError."""
    t0 = time.perf_counter()
    model = _Model(batch)
    model.objective(objective)
    if hint:
        model.hint()
    solver = cp_model.CpSolver()
    solver.parameters.num_workers = 8
    solver.parameters.max_time_in_seconds = time_limit
    code = solver.Solve(model.m)
    raw = solver.StatusName(code)
    if code not in _STATUS:
        raise RuntimeError(f"CP-SAT returned {raw}: {model.m.Validate() or 'invalid model'}")
    status = _STATUS[code]
    mode = f"cpsat:{objective}"
    if status in ("infeasible", "no_solution"):
        return Plan(mode=mode, blocks={}, start={}, bindings={}, status=status,
                    solve_seconds=time.perf_counter() - t0, objective=objective,
                    solver_status=raw)
    bindings = {wf.id: {} for wf in batch.workflows}
    for (wf, var, cand), lit in model.x.items():
        if solver.Value(lit):
            bindings[wf][var] = cand
    return Plan(mode=mode, blocks=bind_batch(batch, bindings),
                start={bid: solver.Value(s) for bid, s in model.start.items()},
                bindings=bindings, status=status,
                solve_seconds=time.perf_counter() - t0, objective=objective, solver_status=raw)
