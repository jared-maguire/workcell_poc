"""Compiler: workflow specs -> per-workflow DAGs of synchronized blocks (spec §6)."""
from __future__ import annotations

from workcell.config import INSTRUMENT_TYPES, STEP_TYPES, TWO_POSITION_TYPES, Workcell
from workcell.dsl import Op, WorkflowSpec
from workcell.ir import Block, CompiledBatch, CompiledWorkflow, Instruction, PlateMove


class CompileError(ValueError):
    pass


# instrument variable prefixes for `step` instruments (`?lh`, `?hs0`, `?seal0`, ...)
_PREFIX = {"liquid_handler": "lh", "heated_shaker": "hs", "plate_sealer": "seal",
           "plate_peeler": "peel", "centrifuge": "spin", "plate_reader": "read",
           "vacuum_concentrator": "dry", "thermocycler": "tc", "head_stamper": "stamp"}


def _arm(workcell: Workcell) -> str:
    arms = workcell.of_type("arm")
    if not arms:
        raise CompileError("the workcell has no arm")
    return arms[0]


class AmbiguousRoute(CompileError):
    """A move's route depends on how an instrument variable is bound (its candidates are
    reached by different arms)."""


def _arms_of(workcell: Workcell, owner: str, candidates: dict[str, list[str]] | None) -> list[str]:
    if not owner.startswith("?"):
        return workcell.arms_reaching(owner)
    if len(workcell.of_type("arm")) == 1:
        return workcell.of_type("arm")
    cands = (candidates or {}).get(owner)
    if cands is None:
        raise AmbiguousRoute(f"no candidates known for {owner}")
    sets = {tuple(workcell.arms_reaching(c)) for c in cands}
    if len(sets) > 1:
        raise AmbiguousRoute(f"{owner} candidates are reached by different arms")
    return list(sets.pop())


def route(workcell: Workcell, src_owner: str, dst_owner: str,
          candidates: dict[str, list[str]] | None = None) -> list[tuple[str, str | None, str | None]]:
    """Hops (arm, from handoff, to handoff) moving a plate from `src_owner` to `dst_owner`:
    one hop when an arm reaches both, else the shortest relay through handoff spots (None =
    the move's own src/dst). Ties go to sorted ids."""
    first, last = _arms_of(workcell, src_owner, candidates), _arms_of(workcell, dst_owner, candidates)
    common = sorted(set(first) & set(last))
    if common:
        return [(common[0], None, None)]
    handoffs = sorted(workcell.of_type("handoff"))
    prev: dict[str, tuple[str, str] | None] = {a: None for a in sorted(first)}
    todo = sorted(first)
    while todo:
        a = todo.pop(0)
        if a in last:
            arms, hos = [a], []
            while prev[arms[-1]] is not None:
                b, h = prev[arms[-1]]
                arms.append(b)
                hos.append(h)
            arms.reverse()
            hos.reverse()
            return [(arm, hos[k - 1] if k else None, hos[k] if k < len(hos) else None)
                    for k, arm in enumerate(arms)]
        for h in handoffs:
            reachers = workcell.arms_reaching(h)
            if a in reachers:
                for b in reachers:
                    if b not in prev:
                        prev[b] = (a, h)
                        todo.append(b)
    raise CompileError(f"no route from {src_owner} to {dst_owner}")


def _type_of(workcell: Workcell, instrument: str, var_types: dict[str, str] | None) -> str:
    if instrument.startswith("?"):
        if var_types is None or instrument not in var_types:
            raise CompileError(f"no instrument type known for variable {instrument!r}")
        return var_types[instrument]
    return workcell.instruments[instrument].type


def _duration(workcell: Workcell, itype: str, op: str, times: dict[str, int] | None,
              own: int | None = None) -> int:
    """Instruction duration: op's own seconds (timed only), workflow times table
    (timed only), then workcell durations."""
    if times is not None:
        if own is not None:
            return own
        for key in (f"{itype}.{op}", op):
            if key in times:
                return times[key]
    return workcell.duration(itype, op)


def transport_instructions(workcell: Workcell, plate: str, src: str, dst: str,
                           var_types: dict[str, str] | None = None,
                           times: dict[str, int] | None = None,
                           candidates: dict[str, list[str]] | None = None) -> list[Instruction]:
    """Instructions that move `plate` from nest `src` to nest `dst`: one arm, or a relay
    through handoff spots (see `route`; AmbiguousRoute if the route depends on a binding)."""
    src_owner, dst_owner = workcell.nest_owner(src), workcell.nest_owner(dst)
    hops = route(workcell, src_owner, dst_owner, candidates)
    if len(hops) > 1:
        return _relay(workcell, plate, src, dst, hops, var_types, times)
    arm = hops[0][0]
    types = {name: _type_of(workcell, name, var_types) for name in (src_owner, arm, dst_owner)}

    def ins(instrument: str, op: str, args: dict | None = None) -> Instruction:
        return Instruction(instrument, op, args or {}, _duration(workcell, types[instrument], op, times))

    out = [ins(name, "check_status") for name in dict.fromkeys((src_owner, arm, dst_owner))]
    if types[src_owner] == "storage":
        out.append(ins(src_owner, "retrieve", {"plate": plate}))
    doors = [n for n in dict.fromkeys((src_owner, dst_owner)) if types[n] == "thermocycler"]
    out += [ins(n, "open_door") for n in doors]
    out.append(ins(arm, "move", {"plate": plate, "src": src, "dst": dst}))
    out += [ins(n, "close_door") for n in doors]
    if types[dst_owner] == "storage":
        out.append(ins(dst_owner, "store", {"plate": plate}))
    return out


def _relay(workcell: Workcell, plate: str, src: str, dst: str, hops, var_types, times):
    """A move relayed through handoff spots, as one instruction list: check everything on the
    route, then each arm's hop (doors open just around the hop that uses them)."""
    src_owner, dst_owner = workcell.nest_owner(src), workcell.nest_owner(dst)
    chain = [src_owner] + [n for arm, _, h in hops for n in (arm, h) if n] + [dst_owner]
    chain = list(dict.fromkeys(chain))

    def ins(instrument: str, op: str, args: dict | None = None) -> Instruction:
        itype = _type_of(workcell, instrument, var_types)
        return Instruction(instrument, op, args or {}, _duration(workcell, itype, op, times))

    def is_type(name, t):
        return _type_of(workcell, name, var_types) == t

    out = [ins(n, "check_status") for n in chain]
    if is_type(src_owner, "storage"):
        out.append(ins(src_owner, "retrieve", {"plate": plate}))
    for k, (arm, a, b) in enumerate(hops):
        here, there = a or src, b or dst
        door = (src_owner if k == 0 and is_type(src_owner, "thermocycler") else
                dst_owner if k == len(hops) - 1 and is_type(dst_owner, "thermocycler") else None)
        if door:
            out.append(ins(door, "open_door"))
        out.append(ins(arm, "move", {"plate": plate, "src": here, "dst": there}))
        if door:
            out.append(ins(door, "close_door"))
    if is_type(dst_owner, "storage"):
        out.append(ins(dst_owner, "store", {"plate": plate}))
    return out


def _resolve_plates(workcell: Workcell, specs: list[WorkflowSpec],
                    wf_ids: list[str]) -> list[dict[str, str]]:
    """Map each workflow's plate handles to barcodes, validating explicit use and
    allocating `new_plate` pools in batch order."""
    users: dict[str, list[tuple[str, str]]] = {}   # barcode -> [(wf_id, mode)]
    for spec, wf_id in zip(specs, wf_ids):
        seen: set[str] = set()
        for decl in spec.plates.values():
            if decl.barcode is None:
                continue
            if decl.barcode not in workcell.plates:
                raise CompileError(f"unknown plate barcode {decl.barcode!r} (workflow {wf_id})")
            if decl.barcode in seen:
                raise CompileError(f"plate {decl.barcode} is used twice in workflow {wf_id}")
            seen.add(decl.barcode)
            users.setdefault(decl.barcode, []).append((wf_id, decl.mode))
    for barcode, uses in users.items():
        for owner, mode in uses:
            if mode == "own":
                other = next((w for w, _ in uses if w != owner), None)
                if other is not None:
                    raise CompileError(f"plate {barcode} owned by {owner} is also used by {other}")

    taken = set(users)
    result: list[dict[str, str]] = []
    for spec, wf_id in zip(specs, wf_ids):
        barcodes: dict[str, str] = {}
        for decl in spec.plates.values():
            if decl.barcode is not None:
                barcodes[decl.id] = decl.barcode
                continue
            bc = next((p.barcode for p in workcell.plates.values()
                       if p.type == decl.pool and p.barcode not in taken), None)
            if bc is None:
                raise CompileError(
                    f"no unused plates of type {decl.pool!r} left (needed by {wf_id})")
            taken.add(bc)
            barcodes[decl.id] = bc
        result.append(barcodes)
    return result


def _op_order(spec: WorkflowSpec, wf_id: str) -> tuple[list[Op], dict[str, list[str]]]:
    """Topologically sort ops (stable by program order); return order and op deps."""
    deps: dict[str, list[str]] = {}
    last_on_plate: dict[str, str] = {}
    for op in spec.ops:
        d = list(op.after)
        for pid in op.plates:
            if pid in last_on_plate:
                d.append(last_on_plate[pid])
            last_on_plate[pid] = op.id
        deps[op.id] = list(dict.fromkeys(d))
    order: list[Op] = []
    done: set[str] = set()
    pending = list(spec.ops)
    while pending:
        ready = next((op for op in pending if all(d in done for d in deps[op.id])), None)
        if ready is None:
            raise CompileError(f"cycle in op dependencies of workflow {wf_id}")
        pending.remove(ready)
        done.add(ready.id)
        order.append(ready)
    return order, deps


class _WorkflowCompiler:
    def __init__(self, workcell: Workcell, spec: WorkflowSpec, wf_id: str,
                 barcodes: dict[str, str]):
        self.wc, self.spec, self.wf_id, self.barcodes = workcell, spec, wf_id, barcodes
        self.blocks: list[Block] = []
        self.variables: dict[str, list[str]] = {}
        self.var_types: dict[str, str] = {}
        self.counters: dict[str, int] = {}
        self.home = {pid: workcell.plates[bc].location for pid, bc in barcodes.items()}
        self.loc = dict(self.home)
        self.stamper: str | None = None
        self.two_pos: dict[str, str] = {}

    # -- instruments -------------------------------------------------------
    def _instrument(self, itype: str, var: str) -> str:
        candidates = self.wc.of_type(itype)
        if not candidates:
            raise CompileError(f"no {itype} instrument available (needed by {self.wf_id})")
        if len(candidates) == 1:
            return candidates[0]
        self.variables[var] = list(candidates)
        self.var_types[var] = itype
        return var

    def _two_position(self, itype: str) -> str:
        """One instrument variable per workflow per two-position lab type (a workflow keeps
        its plates on one liquid handler)."""
        if itype not in self.two_pos:
            self.two_pos[itype] = self._instrument(itype, f"?{_PREFIX[itype]}")
        return self.two_pos[itype]

    def _numbered(self, itype: str, prefix: str) -> str:
        k = self.counters.get(prefix, 0)
        self.counters[prefix] = k + 1
        return self._instrument(itype, f"?{prefix}{k}")

    def _ins(self, instrument: str, op: str, args: dict | None = None,
             own: int | None = None) -> Instruction:
        itype = _type_of(self.wc, instrument, self.var_types)
        return Instruction(instrument, op, args or {},
                           _duration(self.wc, itype, op, self.spec.times, own))

    # -- emission ----------------------------------------------------------
    def _emit(self, kind, label, instructions, moves, plates, deps) -> str:
        bid = f"{self.wf_id}/{len(self.blocks):02d}"
        self.blocks.append(Block(bid, self.wf_id, kind, label, instructions, moves,
                                 set(plates), list(dict.fromkeys(deps))))
        return bid

    def _transport(self, kind, pid: str, dst: str, deps: list[str]) -> str:
        bc, src = self.barcodes[pid], self.loc[pid]
        instrs = transport_instructions(self.wc, bc, src, dst, self.var_types,
                                      self.spec.times)
        self.loc[pid] = dst
        return self._emit(kind, f"{kind} {bc} {src} → {dst}", instrs,
                          [PlateMove(bc, src, dst)], {bc}, deps)

    def _route(self, pid: str, dst: str, deps: list[str]) -> str | None:
        """Bring plate `pid` to nest `dst`; return the chain's last block id (None if no move)."""
        if self.loc[pid] == dst:
            return None
        owner = self.wc.nest_owner(self.loc[pid])
        if _type_of(self.wc, owner, self.var_types) != "storage":
            return self._transport("move", pid, dst, deps)
        bc = self.barcodes[pid]
        scanner = self._numbered("barcode_scanner", "scan")
        fetch = self._transport("fetch", pid, scanner, deps)
        scan = self._emit("scan", f"scan {bc}",
                          [self._ins(scanner, "check_status"),
                           self._ins(scanner, "scan", {"expect": bc})],
                          [], {bc}, [fetch])
        return self._transport("move", pid, dst, [scan])

    def compile(self) -> CompiledWorkflow:
        order, op_deps = _op_order(self.spec, self.wf_id)
        op_end: dict[str, list[str]] = {}   # op id -> the block ids that finish it
        last_op: dict[str, str] = {}
        stored: set[str] = set()

        def ends(op_ids) -> list[str]:
            return [b for o in op_ids for b in op_end[o]]

        for op in order:
            deps = ends(op_deps[op.id])
            for pid in op.plates:
                last_op[pid] = op.id
            if op.kind == "store":
                (pid,) = op.plates
                stored.add(pid)
                if self.loc[pid] == self.home[pid]:
                    op_end[op.id] = deps
                else:
                    op_end[op.id] = [self._transport("return", pid, self.home[pid], deps)]
                continue
            if op.kind == "stamp":
                if self.stamper is None:
                    self.stamper = self._instrument("head_stamper", "?stamp")
                inst = self.stamper
                nests = [f"{inst}.src", f"{inst}.dst"]
                src, dst = (self.barcodes[p] for p in op.plates)
                instr = self._ins(inst, "stamp", {"src": src, "dst": dst},
                                  own=op.params.get("seconds"))
                label = f"stamp {src} → {dst}"
            elif op.kind == "step":
                itype, bcs = op.params["itype"], [self.barcodes[p] for p in op.plates]
                if itype in INSTRUMENT_TYPES and itype not in STEP_TYPES:
                    raise CompileError(
                        f"step on {itype} in {self.wf_id}: steps run on lab instruments "
                        f"({', '.join(sorted(STEP_TYPES))}); use stamp/thermocycle/store")
                if itype in TWO_POSITION_TYPES:
                    inst = self._two_position(itype)
                    nests = [f"{inst}.src", f"{inst}.dst"][-len(op.plates):]
                else:
                    here = self.loc[op.plates[0]]
                    if _type_of(self.wc, self.wc.nest_owner(here), self.var_types) == itype:
                        inst = here         # already on one: stay (no move to itself)
                    else:
                        inst = self._numbered(itype, _PREFIX.get(itype, itype))
                    nests = [inst]
                instr = self._ins(inst, op.params["op"], {"plates": bcs},
                                  own=op.params["seconds"])
                label = f"{op.params['op']} {' '.join(bcs)}"
            elif op.kind == "thermocycle":
                inst = self._numbered("thermocycler", "tc")
                nests = [inst]
                bc = self.barcodes[op.plates[0]]
                program = op.params["program"]
                own = (round(sum(m for _, m in program) * 60)
                       if self.spec.times is not None else None)
                instr = self._ins(inst, "run_program", {"plate": bc, "program": program},
                                  own=own)
                label = f"thermocycle {bc}"
            else:
                raise CompileError(f"unknown op kind {op.kind!r} in {self.wf_id}")
            chain_ends = [self._route(pid, nest, deps) for pid, nest in zip(op.plates, nests)]
            op_end[op.id] = [self._emit(
                "op", label, [self._ins(inst, "check_status"), instr], [],
                {self.barcodes[p] for p in op.plates},
                [e for e in chain_ends if e is not None] + deps)]

        for pid in self.spec.plates:
            if pid not in stored and self.loc[pid] != self.home[pid]:
                self._transport("return", pid, self.home[pid], ends([last_op[pid]]))

        owned = {self.barcodes[d.id] for d in self.spec.plates.values() if d.mode == "own"}
        borrowed = {self.barcodes[d.id] for d in self.spec.plates.values() if d.mode == "borrow"}
        return CompiledWorkflow(self.wf_id, self.spec.name, dict(self.spec.args), self.blocks,
                                self.variables, owned, borrowed, dict(self.barcodes))


def compile_batch(workcell: Workcell, specs: list[WorkflowSpec]) -> CompiledBatch:
    _arm(workcell)
    wf_ids = [f"{spec.name}-{i}" for i, spec in enumerate(specs)]
    all_barcodes = _resolve_plates(workcell, specs, wf_ids)
    workflows = [_WorkflowCompiler(workcell, spec, wf_id, barcodes).compile()
                 for spec, wf_id, barcodes in zip(specs, wf_ids, all_barcodes)]
    return CompiledBatch(workcell, workflows, list(specs))
