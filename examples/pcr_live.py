"""Timed PCR batch: durations in seconds (see workcell.dsl.workflow `times`)."""
from workcell.dsl import Workflow, workflow
from workcell.library import PCR_PROGRAM

TIMES = {"arm.move": 10, "open_door": 5, "close_door": 5, "scan": 4,
         "retrieve": 60, "store": 60, "check_status": 0}


@workflow(times=TIMES)
def pcr_live(w: Workflow, sample: str, primer: str):
    s = w.own(sample)
    p = w.borrow(primer)
    out = w.own(w.new_plate("empty"))

    a = w.stamp(src=s, dst=out, seconds=60)
    b = w.stamp(src=p, dst=out, after=a, seconds=60)
    c = w.thermocycle(out, program=PCR_PROGRAM, after=b)
    w.store(s, after=a)
    w.store(p, after=b)
    w.store(out, after=c)


def batch():
    return [pcr_live(sample=f"S{i+1:04d}", primer=f"P{i % 2 + 1:04d}") for i in range(10)]
