"""Live execution: follow a plan against random actual durations, wait out small delays, and
re-plan on long ones (spec §5)."""
from __future__ import annotations

import dataclasses
import heapq
import time
from dataclasses import dataclass, field
from typing import Callable

from workcell.idle import hold_chains, idle_gaps
from workcell.ir import Block, CompiledBatch
from workcell.live.noise import NoiseParams, actual_block
from workcell.live.order import max_push, plan_preds, project
from workcell.parking import park_pair, parking_spots, plan_parks
from workcell.plan import Plan
from workcell.planners.cpsat import ReplanState, _var
from workcell.sim.executor import Alarm, InstructionEvent, MoveEvent, Trace
from workcell.sim.world import InstrumentError, World

_RELEASE, _INSTR = 0, 1


@dataclass
class ReplanEvent:
    time: int
    trigger: str            # the started block that has overrun the most (may still be running)
    delay: int              # its actual (so far) minus planned duration
    push: int               # largest projected push of an unstarted block
    pushed_block: str
    solve_seconds: float
    status: str             # solver status, or "not_worth_it"
    makespan_before: int    # projected finish if we keep sliding
    makespan_after: int | None
    adopted: bool
    reassigned: int         # bindings changed by an adopted re-plan
    reason: str = "delay"   # "delay", or "idle_hold" (a new projected idle-hold violation)
    idle_before: int = 0    # total projected idle-hold excess (s) if we keep sliding
    idle_after: int | None = None


@dataclass
class LiveResult:
    trace: Trace
    plans: list[Plan]
    replans: list[ReplanEvent]
    actual: dict[str, list[int]] = field(default_factory=dict)
    bindings: dict[str, dict[str, str]] = field(default_factory=dict)
    blocks: dict[str, Block] = field(default_factory=dict)   # the block run under each id


def _vars_of(blk: Block) -> set[str]:
    names = list(blk.instruments) + [n for mv in blk.moves for n in (mv.src, mv.dst)]
    return {_var(n) for n in names} - {None}


def _excess(gaps, limit: int | None) -> int:
    return sum(max(0, g.length - limit) for g in gaps) if limit else 0


def execute_live(batch: CompiledBatch, plan: Plan, noise: NoiseParams,
                 replan: Callable[[ReplanState], Plan], threshold: int,
                 max_idle_hold: int | None = None) -> LiveResult:
    """Run `plan` with actual durations from `noise`. A block starts once its planned start has
    passed, its plan-order predecessors allow it (see order.plan_preds) and its locks are
    free. When any block finishes, project the rest (running blocks end no earlier than now,
    so a still-running overrun shows up too); when the projected push of some unstarted block exceeds
    `threshold`, ask `replan` for a new plan and adopt it only if it finishes at least
    `threshold` sooner than sliding; otherwise keep sliding (the projection becomes the new
    baseline, so the same delay does not trigger again). With `max_idle_hold`, a projected
    idle-hold violation that the baseline does not already have also triggers a re-plan,
    adopted if it lowers the total projected excess over the limit. Park blocks inserted by
    a plan run like any other block. And like a lab tech, if a plate has waited in an
    instrument so long that it must leave now to stay within the limit, and its next move has
    not started, it is parked at once (see `tech_parks`)."""
    initial = batch.initial_locations()
    world = World(batch.workcell, initial)
    trace = Trace(initial_locations=dict(initial))
    compiled = {blk.id: blk for blk in batch.all_blocks()}
    blocks = dict(plan.blocks)              # bound blocks; started ones never change
    result = LiveResult(trace=trace, plans=[plan], replans=[], bindings=plan.bindings,
                        blocks=blocks)
    start_at = dict(plan.start)             # gate and push baseline
    preds = plan_preds(plan, batch)
    pending = sorted(plan.start, key=lambda b: (start_at[b], b))
    started: dict[str, int] = {}
    ended: dict[str, int] = {}
    busy: set[str] = set()
    heap: list[tuple[int, int, int, int, str]] = []   # (time, kind, seq, instr idx, block)
    seq = {bid: i for i, bid in enumerate(pending)}
    durations: dict[str, list[int]] = {}

    def locks(bid: str) -> set[str]:
        return blocks[bid].instruments | blocks[bid].plates

    def ready(bid: str, t: int) -> bool:
        if start_at[bid] > t or locks(bid) & busy:
            return False
        return all((p in ended) if when == "end" else (p in started)
                   for p, when in preds.get(bid, []))

    def try_starts(t: int) -> None:
        for bid in list(pending):
            if not ready(bid, t):
                continue
            pending.remove(bid)
            busy.update(locks(bid))
            started[bid] = t
            durations[bid] = actual_block(noise, blocks[bid])
            kind = _INSTR if blocks[bid].instructions else _RELEASE
            heapq.heappush(heap, (t, kind, seq[bid], 0, bid))

    def shifted() -> Plan:
        return dataclasses.replace(plan, start=start_at)

    def running_end(bid: str, t: int) -> int:
        return max(t, started[bid] + blocks[bid].duration)

    removals = {c.blocks[-1] for c in hold_chains(batch)} if max_idle_hold else set()
    def spot_for(d: str) -> str | None:
        """The parking spot nearest the plate's nest (fewest arm hops)."""
        return (parking_spots(batch.workcell, near=blocks[d].moves[0].src) or [None])[0]

    def waiting(t: int) -> list[tuple[int, str]]:
        """(deadline, removal) for each pending, unparked removal whose deps have all ended and
        whose plate sits in its nest, idle: the plate must start leaving by the deadline to
        stay within the limit. (A park a re-plan dropped is neither pending nor started.)"""
        out = []
        for d in pending:
            k = f"{d}.park"
            if (d not in removals or k in pending or k in started
                    or blocks[d].moves[0].src in parking_spots(batch.workcell)):
                continue
            if not all(x in ended for x in blocks[d].deps if x in start_at):
                continue                # the plate still has a use here: parking won't help
            (mv,) = blocks[d].moves
            if mv.plate in busy or world.location(mv.plate) != mv.src:
                continue
            last = max((ended[b] for b in ended if mv.plate in blocks[b].plates), default=None)
            if last is not None:
                park, _ = park_pair(batch, blocks[d], spot_for(d))
                out.append((last + max_idle_hold - park.duration, d))
        return sorted(out)

    def tech_parks(t: int) -> bool:
        """Park every waiting plate whose deadline has come: insert `<removal>.park` (nest ->
        hotel, starting now, after only its own deps) and start the removal from the hotel."""
        nonlocal plan, preds
        due = [d for deadline, d in waiting(t) if deadline <= t]
        for d in due:
            park, moved = park_pair(batch, blocks[d], spot_for(d))
            blocks[d], blocks[park.id] = moved, park
            start_at[park.id] = t
            seq.setdefault(park.id, len(seq))
            pending.append(park.id)
        if due:
            plan = dataclasses.replace(
                plan, blocks={**plan.blocks, **{b: blocks[b] for b in pending}}, start=start_at)
            for d in due:               # edit the order graph in place (no re-sort)
                k = f"{d}.park"
                preds[k] = [(x, "end") for x in blocks[k].deps]
                preds[d] = [*preds.get(d, []), (k, "end")]
                for b, ps in preds.items():     # the nest now frees when the park starts
                    if b != k:
                        preds[b] = [(k, "start") if p == (d, "start") else p for p in ps]
            pending.sort(key=lambda b: (start_at[b], b))
        return bool(due)

    def gaps(blks: dict[str, Block], starts: dict[str, int], ends: dict[str, int]):
        return idle_gaps(blks, starts, ends, batch) if max_idle_hold else []

    def check_delay(trigger: str, t: int) -> None:
        nonlocal plan, preds, start_at
        if not pending:
            return
        base = shifted()
        actual_end = dict(ended)
        proj = project(base, preds, dict(started), actual_end, t)
        push, who = max_push(base, proj)
        fixed = {bid: (s, ended[bid] if bid in ended else running_end(bid, t))
                 for bid, s in sorted(started.items())}
        done = {b: s for b, (s, _) in fixed.items()}
        done_end = {b: e for b, (_, e) in fixed.items()}
        now_gaps = gaps(blocks, {**done, **proj},
                        {**done_end, **{b: proj[b] + blocks[b].duration for b in pending}})
        was = {(g.plate, g.prev, g.next) for g in gaps(
            blocks, {**done, **{b: start_at[b] for b in pending}},
            {**done_end, **{b: start_at[b] + blocks[b].duration for b in pending}})
            if g.length > max_idle_hold}
        new_idle = [g for g in now_gaps if g.length > max_idle_hold
                    and (g.plate, g.prev, g.next) not in was]
        if push <= threshold and not new_idle:
            return
        reason = "idle_hold" if new_idle else "delay"
        # started park blocks and their rewritten removals are given to the re-plan as is,
        # with the removal's instrument choices kept
        given = {}
        for k in sorted(b for b in started if b not in compiled and b.endswith(".park")):
            given[k] = blocks[k]
            given[k[:-len(".park")]] = blocks[k[:-len(".park")]]
        keep: dict[str, dict[str, str]] = {}
        for bid in sorted(set(started) | set(given)):
            if bid not in compiled:             # park blocks bind nothing of their own
                continue
            wf = compiled[bid].workflow
            for var in sorted(_vars_of(compiled[bid])):
                keep.setdefault(wf, {})[var] = plan.bindings[wf][var]
        before = max([e for _, e in fixed.values()]
                     + [proj[b] + blocks[b].duration for b in pending])
        idle_before = _excess(now_gaps, max_idle_hold)
        parks = plan_parks(plan)
        state = ReplanState(now=t, fixed=fixed, bindings=keep, hint=dict(proj),
                            hint_bindings={wf: dict(v) for wf, v in plan.bindings.items()},
                            parks={d: sp for d, sp in parks.items() if f"{d}.park" in started},
                            hint_parks=parks, blocks=given)
        t0 = time.perf_counter()
        new = replan(state)
        solve = time.perf_counter() - t0
        after = idle_after = None
        if new.finished:
            after = max(fixed[b][1] if b in fixed else new.end(b) for b in new.start)
            rest = [b for b in new.start if b not in fixed]
            idle_after = _excess(gaps(
                {**blocks, **{b: new.blocks[b] for b in rest}},
                {**done, **{b: new.start[b] for b in rest}},
                {**done_end, **{b: new.end(b) for b in rest}}), max_idle_hold)
        adopt = after is not None and (before - after >= threshold or (
            reason == "idle_hold" and idle_after < idle_before))
        reassigned = 0
        if adopt:
            reassigned = sum(new.bindings[wf].get(v) != c
                             for wf, bd in plan.bindings.items() for v, c in bd.items())
            plan = new
            result.plans.append(new)
            result.bindings = new.bindings
            preds = plan_preds(new, batch)
            # the new plan's unstarted blocks replace ours (park blocks may come or go)
            pending[:] = [b for b in new.start if b not in started]
            start_at = {**{b: start_at[b] for b in started}, **{b: new.start[b] for b in pending}}
            for bid in pending:
                blocks[bid] = new.blocks[bid]
                seq.setdefault(bid, len(seq))
        else:
            for bid in pending:                 # keep sliding: projection is the new baseline
                start_at[bid] = proj[bid]
        pending.sort(key=lambda b: (start_at[b], b))
        # Blame the started block that has overrun the most so far — it may still be running
        # (its lateness shows up in the projection as soon as any other block finishes).
        late, delay = max(((bid, (ended.get(bid, t) - s) - blocks[bid].duration)
                           for bid, s in sorted(started.items())),
                          key=lambda x: (x[1], x[0] == trigger))
        result.replans.append(ReplanEvent(
            time=t, trigger=late, delay=delay, push=push,
            pushed_block=who, solve_seconds=solve,
            status=new.status if (adopt or after is None) else "not_worth_it",
            makespan_before=before, makespan_after=after, adopted=adopt,
            reassigned=reassigned, reason=reason, idle_before=idle_before,
            idle_after=idle_after))

    def finish_alarm(alarm: Alarm) -> LiveResult:
        trace.alarm = alarm
        trace.end_time = alarm.time
        for bid, s in started.items():
            if bid not in ended:
                trace.spans[bid] = (s, alarm.time)
        trace.final_locations = world.snapshot()["locations"]
        result.actual = durations
        return result

    now = -1
    while True:
        candidates = [h[0] for h in heap[:1]]
        candidates += [start_at[b] for b in pending if start_at[b] > now]
        if not candidates:
            break
        if removals:
            candidates += [dl for dl, _ in waiting(now) if dl > now]
        t = now = min(candidates)
        while True:
            while heap and heap[0][0] == t and heap[0][1] == _RELEASE:
                _, _, _, _, bid = heapq.heappop(heap)
                busy -= locks(bid)
                ended[bid] = t
                trace.completed.add(bid)
                trace.spans[bid] = (started[bid], t)
                check_delay(bid, t)
            try_starts(t)
            if removals and tech_parks(t):
                try_starts(t)
            if not (heap and heap[0][0] == t):
                break
            if heap[0][1] == _RELEASE:
                continue
            _, _, o, idx, bid = heapq.heappop(heap)
            instr = blocks[bid].instructions[idx]
            try:
                world.apply(instr)
            except InstrumentError as e:
                return finish_alarm(Alarm(t, bid, e.instrument, e.op, e.message))
            dur = durations[bid][idx]
            trace.events.append(InstructionEvent(t, bid, instr.instrument, instr.op,
                                                 dict(instr.args), dur))
            if instr.op == "move" and instr.instrument in batch.workcell.of_type("arm"):
                a = instr.args
                trace.moves.append(MoveEvent(t, a["plate"], a["src"], a["dst"]))
            done = t + dur
            trace.end_time = max(trace.end_time, done)
            nxt = (done, _INSTR, o, idx + 1, bid) if idx + 1 < len(blocks[bid].instructions) \
                else (done, _RELEASE, o, 0, bid)
            heapq.heappush(heap, nxt)

    trace.final_locations = world.snapshot()["locations"]
    result.actual = durations
    return result
