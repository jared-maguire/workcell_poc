from types import SimpleNamespace

import pytest

from workcell.config import Nest
from workcell.ir import Block, PlateMove
from workcell.live.order import max_push, plan_preds, project
from workcell.plan import Plan, plate_stays
from workcell.planners.cpsat import plan_cpsat


def _move(bid, plate, src, dst, deps=()):
    return Block(bid, "w", "move", bid, [], [PlateMove(plate, src, dst)], {plate}, list(deps))


def _setup(blocks, start, initial, capacities):
    """A hand-built plan plus a stub batch exposing only what plan_preds reads."""
    plan = Plan("t", {b.id: b for b in blocks}, start, {}, "optimal")
    nests = {n: Nest(n, n, c) for n, c in capacities.items()}
    batch = SimpleNamespace(initial_locations=lambda: dict(initial),
                            workcell=SimpleNamespace(nests=lambda: nests))
    return plan, batch


def test_preds_include_deps_and_resource_order(timed_batch):
    b = timed_batch(2); p = plan_cpsat(b, time_limit=5, deterministic=True); pr = plan_preds(p, b)
    for bid, blk in p.blocks.items():
        assert {d for d in blk.deps} <= {x for x, w in pr[bid] if w == "end"}
    arm = sorted((bid for bid, blk in p.blocks.items() if "arm1" in blk.instruments), key=lambda x: (p.start[x], x))
    for a, c in zip(arm, arm[1:]):
        assert (a, "end") in pr[c]


def test_projection_without_delay_is_plan(timed_batch):
    b = timed_batch(2); p = plan_cpsat(b, time_limit=5, deterministic=True); pr = plan_preds(p, b)
    proj = project(p, pr, actual_start={}, actual_end={}, now=0)
    assert proj == p.start and max_push(p, proj) == (0, None)


def test_delay_propagates(timed_batch):
    b = timed_batch(2); p = plan_cpsat(b, time_limit=5, deterministic=True); pr = plan_preds(p, b)
    first = min(p.start, key=lambda x: (p.start[x], x))
    proj = project(p, pr, {first: p.start[first]}, {first: p.end(first) + 400}, now=p.end(first) + 400)
    push, who = max_push(p, proj)
    assert push >= 400 and who is not None


def test_nothing_left_no_push(timed_batch):
    b = timed_batch(1); p = plan_cpsat(b, time_limit=5, deterministic=True)
    s = {bid: p.start[bid] for bid in p.start}; e = {bid: p.end(bid) + 999 for bid in p.start}
    assert max_push(p, project(p, plan_preds(p, b), s, e, now=max(e.values()))) == (0, None)


def test_start_preds_are_removers_of_previous_occupant(timed_batch):
    b = timed_batch(2); p = plan_cpsat(b, time_limit=5, deterministic=True); pr = plan_preds(p, b)
    nests = b.workcell.nests()
    starts = [(r, bid) for bid in pr for r, w in pr[bid] if w == "start"]
    assert starts                                  # two PCR workflows share capacity-1 nests
    stays = plate_stays(p, b.initial_locations())
    for r, bid in starts:
        assert p.start[r] <= p.start[bid]
        into = {m.dst for m in p.blocks[bid].moves if nests[m.dst].capacity == 1}
        out = {m.src for m in p.blocks[r].moves}
        assert into & out
        assert any(n in into and leave == p.start[r] for _, n, _, leave in stays)


def test_running_block_ends_no_earlier_than_planned(timed_batch):
    b = timed_batch(2); p = plan_cpsat(b, time_limit=5, deterministic=True); pr = plan_preds(p, b)
    first = min(p.start, key=lambda x: (p.start[x], x))
    succ = [x for x in pr if (first, "end") in pr[x]]
    assert succ
    # started 100 s late and still running at its own start: it ends at actual start + planned
    s0 = p.start[first] + 100
    proj = project(p, pr, {first: s0}, {}, now=s0)
    assert first not in proj
    assert all(proj[x] >= s0 + p.blocks[first].duration for x in succ)


def test_start_pred_waits_only_for_start():
    # r moves plate q out of nest n at 10; b moves p into n at 10 (both 0 s).
    r = Block("w/00", "w", "move", "r", [], [PlateMove("q", "n", "h")], {"q"}, [])
    blk = Block("w/01", "w", "move", "b", [], [PlateMove("p", "h2", "n")], {"p"}, [])
    plan = Plan("t", {"w/00": r, "w/01": blk}, {"w/00": 10, "w/01": 10}, {}, "optimal")
    preds = {"w/00": [], "w/01": [("w/00", "start")]}
    proj = project(plan, preds, {}, {}, now=0)
    assert proj == {"w/00": 10, "w/01": 10}
    proj = project(plan, preds, {"w/00": 30}, {}, now=30)
    assert proj == {"w/01": 30}


def test_start_pred_for_initial_occupant():
    r = _move("w/00", "q", "n", "s")                # q starts in n; r takes it out
    p = _move("w/01", "p", "s2", "n")               # p is placed into n afterwards
    plan, batch = _setup([r, p], {"w/00": 10, "w/01": 20}, {"q": "n", "p": "s2"},
                         {"n": 1, "s": 10**9, "s2": 10**9})
    assert ("w/00", "start") in plan_preds(plan, batch)["w/01"]


def test_start_preds_capacity_two():
    # a, b arrive in h (capacity 2); c may enter once a leaves, d once b leaves
    blocks = [_move("w/00", "a", "s", "h"), _move("w/01", "b", "s", "h"),
              _move("w/02", "a", "h", "s"), _move("w/03", "c", "s", "h"),
              _move("w/04", "b", "h", "s"), _move("w/05", "d", "s", "h")]
    start = {"w/00": 0, "w/01": 1, "w/02": 5, "w/03": 6, "w/04": 7, "w/05": 8}
    plan, batch = _setup(blocks, start, {x: "s" for x in "abcd"}, {"h": 2, "s": 10**9})
    starts = {b: [x for x, w in ps if w == "start"] for b, ps in plan_preds(plan, batch).items()}
    assert starts == {"w/00": [], "w/01": [], "w/02": [], "w/03": ["w/02"], "w/04": [],
                      "w/05": ["w/04"]}


def test_start_pred_skipped_when_it_would_close_a_cycle():
    # 0 s blocks at t=5: r removes q from n but already waits on p (dep), which places into n
    p = _move("w/00", "p", "s", "n")
    r = _move("w/01", "q", "n", "s2", deps=["w/00"])
    plan, batch = _setup([p, r], {"w/00": 5, "w/01": 5}, {"q": "n", "p": "s"},
                         {"n": 1, "s": 10**9, "s2": 10**9})
    preds = plan_preds(plan, batch)
    assert ("w/01", "start") not in preds["w/00"]
    assert project(plan, preds, {}, {}, now=0) == {"w/00": 5, "w/01": 5}


def test_project_rejects_cycle():
    a, b = _move("w/00", "a", "s", "n"), _move("w/01", "b", "s", "m")
    plan, _ = _setup([a, b], {"w/00": 0, "w/01": 0}, {}, {})
    with pytest.raises(ValueError, match="cycle"):
        project(plan, {"w/00": [("w/01", "end")], "w/01": [("w/00", "start")]}, {}, {}, now=0)


def test_project_rejects_end_without_start():
    a = _move("w/00", "a", "s", "n")
    plan, _ = _setup([a], {"w/00": 0}, {}, {})
    with pytest.raises(AssertionError):
        project(plan, {"w/00": []}, {}, {"w/00": 3}, now=3)


def test_start_pred_pairs_with_kth_departure():
    # Ruling R6: h holds 2; a arrives 0, b arrives 1, b leaves 5, c arrives 6, a leaves 9.
    # c takes the slot b freed, so it waits on b's remover (@5), not a's (@9).
    blocks = [_move("w/00", "a", "s", "h"), _move("w/01", "b", "s", "h"),
              _move("w/02", "b", "h", "s"), _move("w/03", "c", "s", "h"),
              _move("w/04", "a", "h", "s")]
    start = {"w/00": 0, "w/01": 1, "w/02": 5, "w/03": 6, "w/04": 9}
    plan, batch = _setup(blocks, start, {x: "s" for x in "abc"}, {"h": 2, "s": 10**9})
    preds = plan_preds(plan, batch)
    assert [x for x, w in preds["w/03"] if w == "start"] == ["w/02"]
    assert project(plan, preds, {}, {}, now=0)["w/03"] == 6
