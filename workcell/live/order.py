"""Plan order as a precedence graph, and projecting delays through it."""
from __future__ import annotations

import heapq
from typing import Literal

from workcell.ir import CompiledBatch
from workcell.plan import Plan, plate_stays

When = Literal["end", "start"]


def plan_preds(plan: Plan, batch: CompiledBatch) -> dict[str, list[tuple[str, When]]]:
    """Each planned block's (pred, when) pairs: it may start once `pred` has ended ("end") or
    started ("start"). "end": deps in the plan and the previous block on each of its
    instruments and plates. "start": for a placement into a nest, the block that frees the
    slot it takes (see `_nest_handoffs`), unless that block already waits on this one."""
    order = sorted(plan.start, key=lambda b: (plan.start[b], b))
    preds: dict[str, dict[tuple[str, When], None]] = {b: {} for b in order}
    last: dict[str, str] = {}                       # resource -> latest block using it
    for bid in order:
        blk = plan.blocks[bid]
        for d in blk.deps:
            if d in plan.start:
                preds[bid][(d, "end")] = None
        for res in [*sorted(blk.instruments), *sorted(blk.plates)]:
            if res in last:
                preds[bid][(last[res], "end")] = None
            last[res] = bid
    for placer, remover in _nest_handoffs(plan, batch):
        if remover != placer and not _depends_on(plan, preds, remover, placer):
            preds[placer].setdefault((remover, "start"), None)
    return {b: list(p) for b, p in preds.items()}


def _depends_on(plan: Plan, preds: dict[str, dict[tuple[str, When], None]],
                src: str, target: str) -> bool:
    """Whether `src` already (transitively) waits on `target`. Preds never start later than
    their block, so only blocks starting no earlier than `target` are searched."""
    floor, stack, seen = plan.start[target], [src], {src}
    while stack:
        for p, _ in preds[stack.pop()]:
            if p == target:
                return True
            if p not in seen and plan.start[p] >= floor:
                seen.add(p)
                stack.append(p)
    return False


def _nest_handoffs(plan: Plan, batch: CompiledBatch) -> list[tuple[str, str]]:
    """(placer, remover) handoffs: in each nest of capacity c, the block placing the k-th stay
    (planned arrival order, initial occupants first) waits for the remover of the (k-c)-th."""
    initial = batch.initial_locations()
    movers: dict[str, list[tuple[int, str, str]]] = {}   # same order plate_stays uses
    for bid in plan.start:
        for m in plan.blocks[bid].moves:
            movers.setdefault(m.plate, []).append((plan.start[bid], bid, m.dst))
    for chain in movers.values():
        chain.sort()
    # plate_stays emits each plate's stays in order: stay i is placed by movers[i - 1] (or is
    # initial when the plate has an initial location) and removed by movers[i].
    by_nest: dict[str, list[tuple[int, str, str | None, str | None]]] = {}
    seen: dict[str, int] = {}
    for plate, nest, arrive, leave in plate_stays(plan, initial):
        i = seen.get(plate, 0)
        seen[plate] = i + 1
        k = i if plate in initial else i + 1           # index of the remover in movers
        chain = movers.get(plate, [])
        placer = chain[k - 1][1] if k >= 1 else None
        remover = chain[k][1] if leave is not None else None
        by_nest.setdefault(nest, []).append((arrive, placer or "", plate, remover))
    nests = batch.workcell.nests()
    out = []
    for nest in sorted(by_nest):
        if nest not in nests:
            continue
        cap = nests[nest].capacity
        stays = sorted(by_nest[nest], key=lambda s: (s[1] != "", s[0], s[1], s[2]))
        for (_, _, _, remover), (_, placer, _, _) in zip(stays, stays[cap:]):
            if placer and remover is not None:
                out.append((placer, remover))
    return out


def project(plan: Plan, preds: dict[str, list[tuple[str, When]]], actual_start: dict[str, int],
            actual_end: dict[str, int], now: int) -> dict[str, int]:
    """Projected start of every unstarted planned block, keeping the plan's order. Started
    blocks keep their actual start; they end at their actual end, or (still running) no
    earlier than now or their planned duration after their actual start."""
    assert set(actual_end) <= set(actual_start), "a finished block must have started"
    start: dict[str, int] = {}
    end: dict[str, int] = {}
    for bid in plan.start:
        if bid in actual_start:
            start[bid] = actual_start[bid]
            end[bid] = actual_end[bid] if bid in actual_end else max(
                now, actual_start[bid] + plan.blocks[bid].duration)
    # unstarted blocks in (plan start, id) order, but never before one of their preds
    # (a "start" pred may share the start time and sort after its successor)
    todo = [b for b in plan.start if b not in start]
    waiting = {b: sum(1 for p, _ in preds.get(b, []) if p in plan.start and p not in start)
               for b in todo}
    succs: dict[str, list[str]] = {}
    for b in todo:
        for p, _ in preds.get(b, []):
            if p in plan.start and p not in start:
                succs.setdefault(p, []).append(b)
    ready = [(plan.start[b], b) for b in todo if waiting[b] == 0]
    heapq.heapify(ready)
    projected: dict[str, int] = {}
    while ready:
        _, bid = heapq.heappop(ready)
        t = max(plan.start[bid], now)
        for p, when in preds.get(bid, []):
            if p in start:
                t = max(t, start[p] if when == "start" else end[p])
        start[bid] = projected[bid] = t
        end[bid] = t + plan.blocks[bid].duration
        for s in succs.get(bid, []):
            waiting[s] -= 1
            if waiting[s] == 0:
                heapq.heappush(ready, (plan.start[s], s))
    if len(projected) != len(todo):
        raise ValueError("plan order has a cycle")
    return projected


def max_push(plan: Plan, projected: dict[str, int]) -> tuple[int, str | None]:
    """The largest delay of a projected block past its planned start, and that block;
    (0, None) when nothing is pushed later."""
    best, who = 0, None
    for bid in sorted(projected, key=lambda b: (plan.start[b], b)):
        push = projected[bid] - plan.start[bid]
        if push > best:
            best, who = push, bid
    return best, who
