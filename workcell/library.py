"""Library workflows."""
from workcell.dsl import Workflow, workflow

PCR_PROGRAM = [(95, 2.0), *[(95, 0.5), (58, 0.5), (72, 1.0)] * 30, (72, 5.0)]  # (°C, min)


@workflow
def pcr(w: Workflow, sample: str, primer: str):
    s = w.own(sample)                 # specific plate, exclusive to this workflow
    p = w.borrow(primer)              # shared plate
    out = w.own(w.new_plate("empty"))  # any empty plate from the pool; becomes the PCR plate

    a = w.stamp(src=s, dst=out)
    b = w.stamp(src=p, dst=out, after=a)
    c = w.thermocycle(out, program=PCR_PROGRAM, after=b)
    w.store(s, after=a)
    w.store(p, after=b)
    w.store(out, after=c)
