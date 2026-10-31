"""Build the self-contained HTML report: session -> JSON data -> inlined template."""
from __future__ import annotations

import dataclasses
import inspect
import json
from pathlib import Path

from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import PythonLexer, YamlLexer

from workcell.ir import Block
from workcell.run import RunResult, Session

_HERE = Path(__file__).parent
_LIGHT_STYLE, _DARK_STYLE = "default", "github-dark"


def _jsonable(x):
    """Recursively convert tuples/sets (e.g. a thermocycler program) to lists."""
    if isinstance(x, dict):
        return {str(k): _jsonable(v) for k, v in x.items()}
    if isinstance(x, (set, frozenset)):
        return sorted(_jsonable(v) for v in x)
    if isinstance(x, (list, tuple)):
        return [_jsonable(v) for v in x]
    return x


def _highlight(code: str, lexer) -> str:
    return highlight(code, lexer, HtmlFormatter(cssclass="highlight", linenos="inline")) if code else ""


def _workflow_source(session: Session) -> str:
    funcs = list(dict.fromkeys(spec.func for spec in session.batch.specs))
    parts = []
    for func in funcs:
        try:
            parts.append(inspect.getsource(func).rstrip())
        except (OSError, TypeError):
            parts.append(f"# source unavailable for {getattr(func, '__name__', func)!r}")
    return "\n\n\n".join(parts)


def _instructions(blk: Block) -> list[dict]:
    return [{"instrument": i.instrument, "op": i.op, "args": _jsonable(i.args), "duration": i.duration}
            for i in blk.instructions]


def _compiled_block(blk: Block) -> dict:
    return {"id": blk.id, "kind": blk.kind, "label": blk.label, "duration": blk.duration,
            "deps": list(blk.deps), "plates": sorted(blk.plates), "instruments": sorted(blk.instruments),
            "instructions": _instructions(blk)}


def _run(res: RunResult) -> dict:
    plan, trace = res.plan, res.trace
    blocks = []
    for bid, (start, end) in sorted(trace.spans.items(), key=lambda kv: (kv[1][0], kv[0])):
        blk = plan.blocks[bid]
        blocks.append({"id": bid, "workflow": blk.workflow, "kind": blk.kind, "label": blk.label,
                       "inserted": blk.inserted, "start": start, "end": end,
                       "instruments": sorted(blk.instruments), "plates": sorted(blk.plates),
                       # extra (beyond the contract): bound instructions for tooltips, incl. park blocks
                       "instructions": _instructions(blk)})
    return {
        "mode": res.mode,
        "status": plan.status,
        "solver_status": plan.solver_status,
        "stats": _jsonable(dataclasses.asdict(res.stats)),
        "errors": list(res.errors),
        "blocks": blocks,
        "moves": [dataclasses.asdict(m) for m in trace.moves],
        "initial_locations": dict(trace.initial_locations),
        "alarm": dataclasses.asdict(trace.alarm) if trace.alarm else None,
        "deadlock": _jsonable(dataclasses.asdict(plan.deadlock)) if plan.deadlock else None,
        "bindings": _jsonable(plan.bindings),
    }


def report_data(session: Session) -> dict:
    wc, batch = session.workcell, session.batch
    nests: dict[str, list[dict]] = {}
    for n in wc.nests().values():
        nests.setdefault(n.instrument, []).append({"id": n.id, "capacity": n.capacity})
    return {
        "instruments": [{"id": i.id, "type": i.type, "nests": nests.get(i.id, [])}
                        for i in wc.instruments.values()],
        "workflows": [{"id": wf.id, "name": wf.name, "args": _jsonable(wf.args),
                       "owned": sorted(wf.owned), "borrowed": sorted(wf.borrowed)}
                      for wf in batch.workflows],
        "sources": {"workflows": _highlight(_workflow_source(session), PythonLexer()),
                    "batch": _highlight(session.batch_source, PythonLexer()),
                    "workcell": _highlight(session.workcell_source, YamlLexer())},
        "compiled": [{"workflow": wf.id, "variables": _jsonable(wf.variables),
                      "blocks": [_compiled_block(b) for b in wf.blocks]}
                     for wf in batch.workflows],
        "runs": [_run(r) for r in session.runs],
        "live": _jsonable(session.live),
    }


def _pygments_css() -> str:
    light = HtmlFormatter(style=_LIGHT_STYLE).get_style_defs(".highlight")
    dark = HtmlFormatter(style=_DARK_STYLE).get_style_defs(".highlight")
    return f"{light}\n@media (prefers-color-scheme: dark) {{\n{dark}\n}}"


def build_report(session: Session) -> str:
    # `<` -> \u003c: no `</script`, `<!--` or `<script` can appear inside the data block;
    # anything not JSON-native (an odd workflow arg) falls back to its str()
    data = json.dumps(report_data(session), separators=(",", ":"), default=str).replace("<", "\\u003c")
    html = (_HERE / "template.html").read_text()
    # data last: it is user content and must not be scanned for placeholders
    for key, value in (("/*__CSS__*/", (_HERE / "report.css").read_text()),
                       ("/*__PYGMENTS_CSS__*/", _pygments_css()),
                       ("/*__JS__*/", (_HERE / "report.js").read_text()),
                       ("__DATA__", data)):
        html = html.replace(key, value, 1)
    return html


def write_report(session: Session, path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_report(session), encoding="utf-8")
    return path
