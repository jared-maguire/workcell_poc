"""Shared test helpers for building hand-written plans."""
from workcell.plan import Plan, bind_batch

VALID = ["00", "01", "02", "03", "04", "05", "06", "13", "07", "08", "09", "10", "14", "11", "12", "15"]


def sequential(batch, order, bindings={"pcr-0": {"?tc0": "tc1"}}, status="complete"):
    blocks = bind_batch(batch, bindings)
    t = 0
    start = {}
    for s in order:
        bid = f"pcr-0/{s}"
        start[bid] = t
        t += blocks[bid].duration
    return Plan(mode="test", blocks=blocks, start=start, bindings=bindings, status=status)
