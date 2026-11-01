from pathlib import Path

from workcell.compiler import compile_batch
from workcell.config import load_workcell
from workcell.dsl import load_batch
from workcell.idle import idle_gaps, violations
from workcell.planners.dispatch import plan_dispatch, remaining_work
from workcell.sim.executor import execute
from workcell.validate import validate_plan

EX = Path(__file__).resolve().parent.parent / "examples"


def clean(p, b, check_durations=True):
    assert p.status == "complete"
    assert validate_plan(p, b, check_durations=check_durations) == []
    tr = execute(p, b)
    assert tr.alarm is None and tr.lock_waits == []
    assert tr.completed >= {blk.id for blk in b.all_blocks()}


def over(p, b, limit):
    return violations(idle_gaps(p.blocks, p.start, {k: p.end(k) for k in p.start}, b), limit)


def test_dispatch_unit_pcr(compiled_small):
    b = compiled_small(4)
    p = plan_dispatch(b)
    assert p.mode == "jit"
    clean(p, b)


def test_dispatch_timed_pcr(timed_batch):
    b = timed_batch(4)
    clean(plan_dispatch(b), b)


def test_remaining_work_is_longest_path_to_end(timed_batch):
    b = timed_batch(1)
    rw = remaining_work(b)
    (wf,) = b.workflows
    first, last = wf.blocks[0], wf.blocks[-1]
    assert rw[last.id] == last.duration
    assert rw[first.id] == max(rw.values())
    assert all(rw[blk.id] >= blk.duration for blk in wf.blocks)


def test_idle_rule_parks_and_cuts_violations(timed_batch):
    b = timed_batch(4)
    loose, tight = plan_dispatch(b), plan_dispatch(b, max_idle_hold=300)
    clean(tight, b)
    assert any(blk.inserted for blk in tight.blocks.values())
    assert len(over(tight, b, 300)) < len(over(loose, b, 300))


def test_actual_durations_run_and_validate(timed_batch):
    b = timed_batch(3)
    p = plan_dispatch(b, max_idle_hold=300,
                      actual=lambda blk: [2 * i.duration + 1 for i in blk.instructions])
    clean(p, b, check_durations=False)
    blk = p.blocks["pcr_live-0/00"]
    assert blk.duration == sum(2 * i.duration + 1 for i in b.block("pcr_live-0/00").instructions)


def test_no_clairvoyance(timed_batch):
    """A longer actual duration of one block changes nothing that starts before the planned
    run of that block would have ended."""
    b = timed_batch(3)
    slow = "pcr_live-1/05"

    def actual(factor):
        return lambda blk: [i.duration * (factor if blk.id == slow else 1)
                            for i in blk.instructions]
    a, z = plan_dispatch(b, actual=actual(1)), plan_dispatch(b, actual=actual(5))
    cut = a.end(slow)
    early = {k: s for k, s in a.start.items() if s < cut}
    assert early and all(z.start.get(k) == s for k, s in early.items())


def test_deterministic(timed_batch):
    b = timed_batch(4)
    p, q = plan_dispatch(b, max_idle_hold=300), plan_dispatch(b, max_idle_hold=300)
    assert p.start == q.start and p.bindings == q.bindings


def test_two_arm_pcr(two_arm):
    b = compile_batch(two_arm, load_batch(EX / "pcr_live.py")[0][:4])
    p = plan_dispatch(b, max_idle_hold=300)
    clean(p, b)
    assert any({"arm1", "arm2", "ho1"} <= blk.instruments for blk in p.blocks.values())


def test_two_arm_parks_nearest(two_arm):
    b = compile_batch(two_arm, load_batch(EX / "pcr_live.py")[0][:4])
    p = plan_dispatch(b, max_idle_hold=60)
    clean(p, b)
    parks = [blk.moves[0] for blk in p.blocks.values() if blk.inserted]
    assert parks
    for m in parks:                 # arm1's own hotel; hotel2 would need a relay
        assert m.dst == ("hotel2" if m.src == "tc2" else "hotel1")


def test_libprep_two_arm():
    b = compile_batch(load_workcell(EX / "libprep_2arm.yaml"),
                      load_batch(EX / "libprep_capture.py")[0])
    p = plan_dispatch(b, max_idle_hold=300)
    clean(p, b)


def test_workflows_under_way_go_first():
    """Ten PCRs sharing two primers: starting a new workflow ahead of one under way lets it
    take the primer the first one's stamper waits for (a long idle hold)."""
    b = compile_batch(load_workcell(EX / "workcell.yaml"), load_batch(EX / "pcr_live.py")[0])
    p = plan_dispatch(b, max_idle_hold=300)
    clean(p, b)
    assert max((g.length for g in over(p, b, 300)), default=0) < 600


def test_release_times(timed_batch):
    """A workflow starts nothing before its release time; the run waits for releases."""
    b = timed_batch(3)
    release = {"pcr_live-1": 5000, "pcr_live-2": 20000}
    p = plan_dispatch(b, max_idle_hold=300, release=release)
    clean(p, b)
    for wf in b.workflows:
        assert min(p.start[x.id] for x in wf.blocks) >= release.get(wf.id, 0)
    assert min(p.start[x.id] for x in b.workflows[2].blocks) == 20000
