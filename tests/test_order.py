from workcell.live.order import max_push, plan_preds, project
from workcell.planners.cpsat import plan_cpsat
from workcell.plan import plate_stays


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
    from workcell.ir import Block, PlateMove
    from workcell.plan import Plan
    # r moves plate q out of nest n at 10 (10 s long); b moves p into n at 10.
    r = Block("w/00", "w", "move", "r", [], [PlateMove("q", "n", "h")], {"q"}, [])
    blk = Block("w/01", "w", "move", "b", [], [PlateMove("p", "h2", "n")], {"p"}, [])
    plan = Plan("t", {"w/00": r, "w/01": blk}, {"w/00": 10, "w/01": 10}, {}, "optimal")
    preds = {"w/00": [], "w/01": [("w/00", "start")]}
    proj = project(plan, preds, {}, {}, now=0)
    assert proj == {"w/00": 10, "w/01": 10}
    proj = project(plan, preds, {"w/00": 30}, {}, now=30)
    assert proj == {"w/01": 30}
