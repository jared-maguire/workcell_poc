"""Independent plan validator (spec §8): returns human-readable errors, [] when valid."""
from __future__ import annotations

from collections import defaultdict

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


def validate_plan(plan: Plan, batch: CompiledBatch) -> list[str]:
    errors: list[str] = []
    scheduled = [b for b in plan.start if b in plan.blocks]
    for bid in plan.start:
        if bid not in plan.blocks:
            errors.append(f"block {bid} is scheduled but has no bound block")

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
    nests = batch.workcell.nests()
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

    # completeness
    if plan.finished:
        for b in batch.all_blocks():
            if b.id not in plan.start:
                errors.append(f"block {b.id} is not scheduled")
    return errors
