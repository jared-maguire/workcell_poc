"""Conveyors: a belt between two arm zones (`type: conveyor`, `slots`, `seconds`). A move across
one is three blocks: the upstream arm puts the plate on, a `convey` block carries it (locks
only the plate), the downstream arm takes it off. Plates ride together, up to `slots`."""
import pytest

from workcell.compiler import CompileError, compile_batch
from workcell.config import ConfigError, parse_workcell
from workcell.dsl import workflow
from workcell.planners.dispatch import plan_dispatch
from workcell.sim.executor import execute
from workcell.validate import validate_plan

TIMES = {"arm.move": 10, "retrieve": 60, "store": 60, "scan": 4, "check_status": 0}


def belt(slots=3, seconds=200):
    """Station a (storage, scanner, liquid handler) --belt--> station b (liquid handler,
    storage)."""
    inst = [
        {"id": "arm_a", "type": "arm", "reach": ["store_a", "scan_a", "lh_a", "belt"]},
        {"id": "arm_b", "type": "arm", "reach": ["lh_b", "store_b", "belt"]},
        {"id": "belt", "type": "conveyor", "slots": slots, "seconds": seconds, "to": "arm_b"},
        {"id": "store_a", "type": "storage", "slots": 50, "stage": "a"},
        {"id": "scan_a", "type": "barcode_scanner", "stage": "a"},
        {"id": "lh_a", "type": "liquid_handler", "stage": "a"},
        {"id": "lh_b", "type": "liquid_handler", "stage": "b"},
        {"id": "store_b", "type": "storage", "slots": 50, "stage": "b"},
    ]
    plates = [{"barcode": f"P{i}", "type": "sample", "location": "store_a"} for i in range(4)]
    return parse_workcell({"instruments": inst, "plates": plates,
                           "durations": {"default": 1, "check_status": 0}})


@workflow(times=TIMES)
def a_then_b(w, i):
    p = w.own(f"P{i}")
    w.stage("a")
    w.step("liquid_handler", "mix", p, seconds=5)
    w.stage("b")
    w.step("liquid_handler", "wash", p, seconds=5)
    w.store(p)                                           # into store_b: plates never go back


def run(wc, specs):
    b = compile_batch(wc, specs)
    p = plan_dispatch(b)
    assert p.finished and validate_plan(p, b) == []
    tr = execute(p, b)
    assert tr.alarm is None and tr.lock_waits == []
    return b, p, tr


def test_conveyor_parsed():
    wc = belt()
    c = wc.instruments["belt"]
    assert (c.type, c.slots, c.seconds) == ("conveyor", 3, 200)
    assert wc.nests()["belt"].capacity == 3


@pytest.mark.parametrize("drop", ["slots", "seconds", "to"])
def test_conveyor_needs_slots_seconds_and_direction(drop):
    inst = [{"id": "a", "type": "arm"},
            {"id": "c", "type": "conveyor", "slots": 2, "seconds": 5, "to": "a"}]
    del inst[1][drop]
    with pytest.raises(ConfigError, match=drop):
        parse_workcell({"instruments": inst})


def test_move_across_is_put_convey_take():
    b = compile_batch(belt(), [a_then_b(0)])
    blocks = b.workflows[0].blocks
    k = next(i for i, blk in enumerate(blocks) if blk.kind == "convey")
    put, convey, take = blocks[k - 1], blocks[k], blocks[k + 1]
    assert [m.dst for m in put.moves] == ["belt"] and put.instruments == {"arm_a", "lh_a", "belt"}
    assert [(i.instrument, i.op, i.duration) for i in convey.instructions] == [("belt", "convey", 200)]
    assert convey.instruments == set() and convey.moves == [] and convey.deps == [put.id]
    assert [m.src for m in take.moves] == ["belt"] and take.instruments == {"arm_b", "lh_b", "belt"}
    assert take.deps == [convey.id]


def test_plates_ride_together_and_arms_stay_free():
    b, p, tr = run(belt(), [a_then_b(i) for i in range(4)])
    rides = sorted(tr.spans[blk.id] for wf in b.workflows for blk in wf.blocks if blk.kind == "convey")
    assert len(rides) == 4
    assert max(sum(s <= t < e for s, e in rides) for t, _ in rides) >= 2      # on the belt together
    arm_a = [ev.time for ev in tr.events if ev.instrument == "arm_a" and ev.op == "move"]
    assert any(s < t < e for s, e in rides for t in arm_a)                   # arm works meanwhile


def test_belt_capacity_respected():
    b, p, tr = run(belt(slots=1), [a_then_b(i) for i in range(3)])
    on = []                         # (time plate put on, time taken off) per plate
    for wf in b.workflows:
        blks = wf.blocks
        k = next(i for i, blk in enumerate(blks) if blk.kind == "convey")
        on.append((tr.spans[blks[k - 1].id][0], tr.spans[blks[k + 1].id][0]))
    on.sort()
    assert all(a[1] <= b_[0] for a, b_ in zip(on, on[1:]))


def test_belt_time_is_not_an_idle_hold():
    from workcell.idle import idle_gaps
    b, p, tr = run(belt(seconds=1000), [a_then_b(0)])
    ends = {bid: s + p.blocks[bid].duration for bid, s in p.start.items()}
    assert all(g.nest != "belt" for g in idle_gaps(p.blocks, p.start, ends, b))


def test_conveyor_runs_one_way():
    @workflow(times=TIMES)
    def back(w):
        p = w.own("P0")
        w.stage("b")
        w.step("liquid_handler", "wash", p, seconds=5)    # P0 starts in store_a: fine
        w.stage("a")
        w.step("liquid_handler", "mix", p, seconds=5)     # back upstream: no route

    with pytest.raises(CompileError, match="no route"):
        compile_batch(belt(), [back()])


def test_conveyor_needs_its_to_arm():
    with pytest.raises(ConfigError, match="'to' arm"):
        parse_workcell({"instruments": [
            {"id": "a1", "type": "arm", "reach": ["c"]}, {"id": "a2", "type": "arm", "reach": ["c"]},
            {"id": "c", "type": "conveyor", "slots": 2, "seconds": 5, "to": "a3"}]})


@pytest.mark.parametrize("mode", ["cpsat", "greedy_hotel"])
def test_other_planners_handle_belts(mode):
    from workcell.planners.cpsat import plan_cpsat
    from workcell.planners.greedy import plan_greedy
    b = compile_batch(belt(slots=2), [a_then_b(i) for i in range(3)])
    p = (plan_cpsat(b, time_limit=5, deterministic=True) if mode == "cpsat"
         else plan_greedy(b, park=True))
    assert p.finished and validate_plan(p, b) == []
    tr = execute(p, b)
    assert tr.alarm is None and tr.lock_waits == []


def test_put_on_full_belt_cannot_meet_take_off():
    """Occupancy ends at the START of the removing block, so a put-on may start on a full belt
    the moment a take-off does. If the put-on's arm move ran first (a take-off into a
    thermocycler opens the door first), the world would find the belt full. Putting on and
    taking off both lock the belt, so the validator rejects that timing."""
    import dataclasses

    @workflow(times=TIMES)
    def to_tc(w, i):
        p = w.own(f"P{i}")
        w.stage("a")
        w.step("liquid_handler", "mix", p, seconds=5)
        w.stage("b")
        w.thermocycle(p, program=[(95, 1)])
        w.store(p)

    data = {"instruments": [
        {"id": "arm_a", "type": "arm", "reach": ["store_a", "scan_a", "lh_a", "belt"]},
        {"id": "arm_b", "type": "arm", "reach": ["tc_b", "store_b", "belt"]},
        {"id": "belt", "type": "conveyor", "slots": 1, "seconds": 500, "to": "arm_b"},
        {"id": "store_a", "type": "storage", "slots": 5, "stage": "a"},
        {"id": "scan_a", "type": "barcode_scanner", "stage": "a"},
        {"id": "lh_a", "type": "liquid_handler", "stage": "a"},
        {"id": "tc_b", "type": "thermocycler", "stage": "b"},
        {"id": "store_b", "type": "storage", "slots": 5, "stage": "b"}],
        "plates": [{"barcode": f"P{i}", "type": "sample", "location": "store_a"} for i in range(2)],
        "durations": {"default": 1, "check_status": 0}}
    b = compile_batch(parse_workcell(data), [to_tc(i) for i in range(2)])
    p = plan_dispatch(b, release={"to_tc-0": 2000})        # P1 rides first; P0's id sorts first
    take = next(x for x in b.workflows[1].blocks if any(m.src == "belt" for m in x.moves))
    put = next(x for x in b.workflows[0].blocks if any(m.dst == "belt" for m in x.moves))
    assert "belt" in take.instruments and "belt" in put.instruments
    start, k = dict(p.start), b.workflows[1].blocks.index(take)
    shift = start[put.id] - start[take.id]
    for x in b.workflows[1].blocks[k:]:                    # take P1 off as P0 is put on
        start[x.id] += shift
    q = dataclasses.replace(p, start=start)
    assert any("belt" in e for e in validate_plan(q, b))
