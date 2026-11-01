"""Compiled IR: instructions grouped into synchronized blocks, per workflow and per batch."""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Literal

from workcell.config import Workcell
from workcell.dsl import WorkflowSpec


def resolve(name: str, bindings: dict[str, str]) -> str:
    """Resolve `?var` / `?var.suffix` through `bindings`; concrete names pass through.
    Raises KeyError for an unbound var."""
    if not name.startswith("?"):
        return name
    var, dot, suffix = name.partition(".")
    return bindings[var] + dot + suffix


@dataclass
class Instruction:
    instrument: str
    op: str
    args: dict
    duration: int


@dataclass
class PlateMove:
    plate: str
    src: str
    dst: str


@dataclass
class Block:
    id: str
    workflow: str
    kind: Literal["fetch", "scan", "move", "op", "return", "park"]
    label: str
    instructions: list[Instruction]
    moves: list[PlateMove]
    plates: set[str]
    deps: list[str]
    inserted: bool = False
    # route variants: when the route (arms, handoffs) depends on how instrument variables
    # are bound, one concrete instruction list per binding, keyed by sorted ((var, cand), ...);
    # `instructions` is the all-first-candidates variant
    variants: dict[tuple, list[Instruction]] | None = None

    @property
    def duration(self) -> int:
        return sum(i.duration for i in self.instructions)

    @property
    def instruments(self) -> set[str]:
        return {i.instrument for i in self.instructions}

    def bind(self, bindings: dict[str, str]) -> Block:
        def arg(v):
            return resolve(v, bindings) if isinstance(v, str) else v

        if self.variants:
            key = tuple((var, bindings[var]) for var, _ in next(iter(self.variants)))
            instructions = list(self.variants[key])
        else:
            instructions = [Instruction(resolve(i.instrument, bindings), i.op,
                                        {k: arg(v) for k, v in i.args.items()}, i.duration)
                            for i in self.instructions]
        return replace(
            self,
            instructions=instructions,
            variants=None,
            moves=[PlateMove(m.plate, resolve(m.src, bindings), resolve(m.dst, bindings))
                   for m in self.moves],
            plates=set(self.plates),
            deps=list(self.deps),
        )


@dataclass
class CompiledWorkflow:
    id: str
    name: str
    args: dict
    blocks: list[Block]
    variables: dict[str, list[str]]
    owned: set[str]
    borrowed: set[str]
    barcodes: dict[str, str]

    def borrow_span(self, plate: str) -> tuple[str, str]:
        ids = [b.id for b in self.blocks if plate in b.plates]
        if not ids:
            raise KeyError(f"plate {plate!r} is not used by {self.id}")
        return ids[0], ids[-1]


@dataclass
class CompiledBatch:
    workcell: Workcell
    workflows: list[CompiledWorkflow]
    specs: list[WorkflowSpec]
    _index: dict[str, Block] = field(default_factory=dict, init=False, repr=False, compare=False)

    def all_blocks(self) -> list[Block]:
        return [b for wf in self.workflows for b in wf.blocks]

    def block(self, id: str) -> Block:
        if id not in self._index:
            self._index = {b.id: b for b in self.all_blocks()}
        return self._index[id]

    def initial_locations(self) -> dict[str, str]:
        used = {bc for wf in self.workflows for bc in wf.barcodes.values()}
        return {bc: p.location for bc, p in self.workcell.plates.items() if bc in used}
