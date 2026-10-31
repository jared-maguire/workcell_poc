"""Shared test helpers: hand-written plans, report data extraction."""
import json
import re

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


def data_of(html):
    """The report's embedded JSON data (its `<` are escaped as \\u003c; json decodes them)."""
    m = re.search(r'<script id="workcell-data" type="application/json">(.*?)</script>', html, re.S)
    return json.loads(m.group(1))
