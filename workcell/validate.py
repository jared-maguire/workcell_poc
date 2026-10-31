"""Independent plan validator (spec §8): returns human-readable errors, [] when valid."""
from __future__ import annotations

from collections import defaultdict

from workcell.idle import idle_gaps, violations
from workcell.ir import CompiledBatch
from workcell.plan import Plan, plate_stays


def _overlaps(intervals):
    """Yield (id_a, id_b) for intervals (start, end, id) whose half-open ranges intersect."""
    latest = None
    for s, e, i in sorted(intervals):
        if latest is not None and s < latest[0]:
            yield latest[1], i
        if latest is None or e > latest[0]:
            latest = (e, i)


def _is_park(block) -> bool:
    return block.inserted and block.kind == "park"


def _compare(plan: Plan, batch: CompiledBatch, bid: str, check_durations: bool) -> list[str]:
    """Check one plan block against its compiled block bound under the plan's bindings
    (instruction durations too, unless `check_durations` is False)."""
    comp, got = batch.block(bid), plan.blocks[bid]
    try:
        want = comp.bind(plan.bindings.get(comp.workflow, {}))
    except KeyError as e:
        return [f"block {bid} uses unbound variable {e.args[0]}"]
    errs = []
    if (got.kind, got.workflow, got.plates) != (want.kind, want.workflow, want.plates):
        errs.append(f"block {bid} differs from compiled kind/workflow/plates")
    missing = [d for d in want.deps if d not in got.deps]
    if missing:
        errs.append(f"block {bid} lost compiled dependencies {missing}")
    parked = {p for d in got.deps if d in plan.blocks and _is_park(plan.blocks[d])
              for p in plan.blocks[d].plates}
    if len(got.moves) != len(want.moves) or any(
            g.plate != w.plate or g.dst != w.dst or (g.src != w.src and w.plate not in parked)
            for g, w in zip(got.moves, want.moves)):
        errs.append(f"block {bid} moves differ from compiled moves")
    def shape(ins):
        return [(i.instrument, i.op, i.args) + ((i.duration,) if check_durations else ())
                for i in ins]
    if want.kind in ("op", "scan") and shape(got.instructions) != shape(want.instructions):
        errs.append(f"block {bid} instructions differ from compiled instructions")
    return errs


def validate_plan(plan: Plan, batch: CompiledBatch, check_durations: bool = True,
                  max_idle_hold: int | None = None) -> list[str]:
    """Errors in `plan` (empty if valid). With `check_durations=False` (a live as-run plan,
    whose blocks carry actual durations) instruction durations may differ from compiled.
    With `max_idle_hold`, a plate idling longer than that in an instrument nest is an error."""
    errors: list[str] = []
    compiled = {b.id for b in batch.all_blocks()}
    scheduled = [b for b in plan.start if b in plan.blocks]
    for bid in plan.start:
        if bid not in plan.blocks or (bid not in compiled and not _is_park(plan.blocks[bid])):
            errors.append(f"unknown block {bid} is scheduled")
        if plan.start[bid] < 0:
            errors.append(f"block {bid} has negative start {plan.start[bid]}")
    for bid, blk in plan.blocks.items():
        if bid not in compiled and not _is_park(blk):
            errors.append(f"block {bid} is not compiled and is not an inserted park block")

    # bindings must name real candidates
    workflows = {wf.id: wf for wf in batch.workflows}
    for wf_id, binding in plan.bindings.items():
        wf = workflows.get(wf_id)
        if wf is None:
            errors.append(f"binding for unknown workflow {wf_id}")
            continue
        for var, target in binding.items():
            if var not in wf.variables:
                errors.append(f"workflow {wf_id} has no variable {var}")
            elif target not in wf.variables[var]:
                errors.append(f"binding {var}={target} in {wf_id} is not one of {wf.variables[var]}")

    # plan blocks must match the compiled blocks
    for bid in plan.blocks:
        if bid in compiled:
            errors += _compare(plan, batch, bid, check_durations)

    # moves must use real nests
    nests = batch.workcell.nests()
    for bid in scheduled:
        for m in plan.blocks[bid].moves:
            for nest in (m.src, m.dst):
                if not nest.startswith("?") and nest not in nests:
                    errors.append(f"block {bid} moves {m.plate} via unknown nest {nest}")

    # unresolved variables
    for bid in scheduled:
        blk = plan.blocks[bid]
        names = [i.instrument for i in blk.instructions]
        names += [v for i in blk.instructions for v in i.args.values() if isinstance(v, str)]
        names += [n for m in blk.moves for n in (m.src, m.dst)]
        for n in names:
            if n.startswith("?"):
                errors.append(f"block {bid} has unresolved name {n}")

    # dependencies
    for bid in scheduled:
        for dep in plan.blocks[bid].deps:
            if dep not in plan.start:
                errors.append(f"dependency {dep} of {bid} is not scheduled")
            elif dep in plan.blocks and plan.end(dep) > plan.start[bid]:
                errors.append(f"dependency {dep} ends at {plan.end(dep)}, "
                              f"after {bid} starts at {plan.start[bid]}")

    # instrument and plate exclusivity
    by_instrument, by_plate = defaultdict(list), defaultdict(list)
    for bid in scheduled:
        blk = plan.blocks[bid]
        iv = (plan.start[bid], plan.end(bid), bid)
        for inst in blk.instruments:
            by_instrument[inst].append(iv)
        for plate in blk.plates:
            by_plate[plate].append(iv)
    for kind, groups in (("instrument", by_instrument), ("plate", by_plate)):
        for name, ivs in groups.items():
            for a, b in _overlaps(ivs):
                errors.append(f"blocks {a} and {b} overlap on {kind} {name}")

    # move chains continuous from the initial location
    initial = batch.initial_locations()
    moves = defaultdict(list)
    for bid in scheduled:
        for m in plan.blocks[bid].moves:
            moves[m.plate].append((plan.start[bid], bid, m))
    for plate, ms in moves.items():
        here = initial.get(plate)
        if here is None:
            errors.append(f"plate {plate} has no initial location")
            continue
        for _, bid, m in sorted(ms, key=lambda x: x[:2]):
            if m.src != here:
                errors.append(f"block {bid} moves {plate} from {m.src} but it is at {here}")
            here = m.dst

    # nest capacity
    by_nest = defaultdict(list)
    for plate, nest, arrive, leave in plate_stays(plan, initial):
        if leave is None or arrive < leave:
            by_nest[nest].append((arrive, float("inf") if leave is None else leave))
    for nest, stays in by_nest.items():
        if nest not in nests:
            continue
        cap = nests[nest].capacity
        for t, _ in sorted(stays):
            n = sum(1 for a, l in stays if a <= t < l)
            if n > cap:
                errors.append(f"nest {nest} holds {n} plates at t={t}, capacity {cap}")
                break

    # borrow spans must not overlap across workflows
    spans = defaultdict(list)
    for wf in batch.workflows:
        for plate in wf.borrowed:
            first, last = wf.borrow_span(plate)
            if first in plan.start and last in plan.start:
                spans[plate].append((plan.start[first], plan.end(last), wf.id))
    for plate, ivs in spans.items():
        for a, b in _overlaps(ivs):
            errors.append(f"borrow spans of plate {plate} overlap in workflows {a} and {b}")

    # idle holds
    if max_idle_hold:
        ends = {b: plan.end(b) for b in scheduled}
        live = {b: plan.start[b] for b in scheduled}
        for g in violations(idle_gaps(plan.blocks, live, ends, batch), max_idle_hold):
            errors.append(f"plate {g.plate} idles {g.length}s in {g.nest} between {g.prev} "
                          f"and {g.next} (limit {max_idle_hold}s)")

    # completeness (a partial plan covers only its planning window)
    if plan.finished and not plan.partial:
        for b in batch.all_blocks():
            if b.id not in plan.start:
                errors.append(f"block {b.id} is not scheduled")
    return errors
