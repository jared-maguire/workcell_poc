"""Python-embedded workflow DSL. Records explicit ops and `after` edges only;
implicit same-plate edges are added by the compiler."""
from __future__ import annotations

import functools
import importlib.util
import inspect
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal

from workcell.config import TWO_POSITION_TYPES


@dataclass(frozen=True)

class PlateHandle:
    id: str


@dataclass(frozen=True)
class NewPlate:
    type: str


@dataclass
class PlateDecl:
    id: str
    mode: Literal["own", "borrow"]
    barcode: str | None
    pool: str | None


@dataclass
class Op:
    id: str
    kind: Literal["stamp", "thermocycle", "store", "step"]
    plates: tuple[str, ...]
    params: dict
    after: tuple[str, ...]


@dataclass
class WorkflowSpec:
    name: str
    args: dict
    plates: dict[str, PlateDecl]
    ops: list[Op]
    func: Callable
    times: dict[str, int] | None = None   # set => timed workflow (seconds)


class Workflow:
    def __init__(self) -> None:
        self.plates: dict[str, PlateDecl] = {}
        self.ops: list[Op] = []
        self._stored: set[str] = set()

    def new_plate(self, type: str) -> NewPlate:
        return NewPlate(type)

    def _declare(self, mode: str, barcode: str | None, pool: str | None) -> PlateHandle:
        pid = f"p{len(self.plates)}"
        self.plates[pid] = PlateDecl(pid, mode, barcode, pool)  # type: ignore[arg-type]
        return PlateHandle(pid)

    def own(self, x: str | NewPlate) -> PlateHandle:
        if isinstance(x, NewPlate):
            return self._declare("own", None, x.type)
        return self._declare("own", x, None)

    def borrow(self, barcode: str) -> PlateHandle:
        if not isinstance(barcode, str):
            raise ValueError("borrow() needs a barcode; a new plate must be owned (use own())")
        return self._declare("borrow", barcode, None)

    def _add(self, kind: str, plates: tuple[PlateHandle, ...], params: dict, after) -> str:
        for h in plates:
            if h.id not in self.plates:
                raise ValueError(f"unknown plate {h.id!r}")
            if h.id in self._stored:
                raise ValueError(f"{kind}: plate {h.id} used after it was stored")
        if after is None:
            after_ids: tuple[str, ...] = ()
        elif isinstance(after, str):
            after_ids = (after,)
        else:
            after_ids = tuple(after)
        known = {o.id for o in self.ops}
        for a in after_ids:
            if a not in known:
                raise ValueError(f"unknown after op id {a!r}")
        op = Op(f"op{len(self.ops)}", kind, tuple(h.id for h in plates), params, after_ids)  # type: ignore[arg-type]
        self.ops.append(op)
        return op.id

    def stamp(self, src: PlateHandle, dst: PlateHandle, after=None,
              seconds: int | None = None) -> str:
        if src.id == dst.id:
            raise ValueError("stamp: src and dst must be different plates")
        params = {} if seconds is None else {"seconds": seconds}
        return self._add("stamp", (src, dst), params, after)

    def step(self, itype: str, op: str, *plates: PlateHandle, seconds: int, after=None) -> str:
        """A timed step (`seconds`) on an instrument of type `itype`: one plate, or two (src,
        dst) on a two-position instrument (liquid handler, head stamper)."""
        if len(plates) not in (1, 2):
            raise ValueError(f"step {op!r}: takes one or two plates, got {len(plates)}")
        if len(plates) == 2:
            if itype not in TWO_POSITION_TYPES:
                raise ValueError(f"step {op!r}: two plates need a two-position instrument "
                                 f"({', '.join(sorted(TWO_POSITION_TYPES))}), not {itype}")
            if plates[0].id == plates[1].id:
                raise ValueError(f"step {op!r}: src and dst must be different plates")
        return self._add("step", plates, {"itype": itype, "op": op, "seconds": seconds}, after)

    def thermocycle(self, plate: PlateHandle, program, after=None) -> str:
        return self._add("thermocycle", (plate,), {"program": program}, after)

    def store(self, plate: PlateHandle, after=None) -> str:
        op_id = self._add("store", (plate,), {}, after)
        self._stored.add(plate.id)
        return op_id


def workflow(func: Callable | None = None, *, times: dict[str, int] | None = None):
    """Decorate a workflow function: `@workflow` or `@workflow(times={...})`."""
    if func is None:
        return lambda f: workflow(f, times=dict(times) if times is not None else None)
    sig = inspect.signature(func)

    @functools.wraps(func)
    def build(*args: Any, **kwargs: Any) -> WorkflowSpec:
        w = Workflow()
        bound = sig.bind(w, *args, **kwargs)
        bound.apply_defaults()
        call_args = {k: v for k, v in bound.arguments.items() if k != next(iter(sig.parameters))}
        func(*bound.args, **bound.kwargs)
        return WorkflowSpec(func.__name__, call_args, w.plates, w.ops, func, times)

    return build


class BatchFileError(ValueError):
    """A batch file that is missing or does not define `batch()`."""


def load_batch(path: str | Path) -> tuple[list[WorkflowSpec], str]:
    path = Path(path)
    if not path.is_file():
        raise BatchFileError(f"batch file not found: {path}")
    spec = importlib.util.spec_from_file_location(f"_batch_{path.stem}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not callable(getattr(module, "batch", None)):
        raise BatchFileError(f"{path} does not define a batch() function")
    return list(module.batch()), path.read_text()
