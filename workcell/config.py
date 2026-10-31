"""Workcell configuration: instruments, nests, plates, durations, failures."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

INSTRUMENT_TYPES = frozenset(
    {"arm", "storage", "hotel", "barcode_scanner", "head_stamper", "thermocycler"}
)


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Instrument:
    id: str
    type: str
    slots: int | None = None


@dataclass(frozen=True)
class Nest:
    id: str
    instrument: str
    capacity: int


@dataclass(frozen=True)
class Plate:
    barcode: str
    type: str
    location: str
    contents: str = ""


@dataclass(frozen=True)
class FailureSpec:
    op: str
    kind: str = "fault"
    instrument: str | None = None
    plate: str | None = None


@dataclass
class Workcell:
    instruments: dict[str, Instrument]
    plates: dict[str, Plate]
    durations: dict[str, int] = field(default_factory=lambda: {"default": 1})
    failures: list[FailureSpec] = field(default_factory=list)

    def nests(self) -> dict[str, Nest]:
        out: dict[str, Nest] = {}
        for inst in self.instruments.values():
            if inst.type in ("storage", "hotel"):
                out[inst.id] = Nest(inst.id, inst.id, inst.slots or 1)
            elif inst.type == "head_stamper":
                for suffix in ("src", "dst"):
                    nid = f"{inst.id}.{suffix}"
                    out[nid] = Nest(nid, inst.id, 1)
            elif inst.type != "arm":
                out[inst.id] = Nest(inst.id, inst.id, 1)
        return out

    def nest_owner(self, nest_id: str) -> str:
        return nest_id.split(".", 1)[0]

    def of_type(self, t: str) -> list[str]:
        return [i.id for i in self.instruments.values() if i.type == t]

    def duration(self, instrument_type: str, op: str) -> int:
        for key in (f"{instrument_type}.{op}", op, "default"):
            if key in self.durations:
                return self.durations[key]
        return 1


def parse_workcell(data: dict) -> Workcell:
    instruments: dict[str, Instrument] = {}
    for raw in data.get("instruments", []):
        iid, itype = raw.get("id"), raw.get("type")
        if itype not in INSTRUMENT_TYPES:
            raise ConfigError(f"unknown instrument type {itype!r} for {iid!r}")
        if iid in instruments:
            raise ConfigError(f"duplicate instrument id {iid!r}")
        instruments[iid] = Instrument(iid, itype, raw.get("slots"))

    plates: dict[str, Plate] = {}

    def add_plate(plate: Plate) -> None:
        loc = instruments.get(plate.location)
        if loc is None or loc.type != "storage":
            raise ConfigError(
                f"plate {plate.barcode!r} must start in a storage instrument, got {plate.location!r}"
            )
        if plate.barcode in plates:
            raise ConfigError(f"duplicate plate barcode {plate.barcode!r}")
        plates[plate.barcode] = plate

    for raw in data.get("plates", []):
        if "generate" in raw:
            for i in range(1, raw["count"] + 1):
                add_plate(Plate(f"{raw['prefix']}{i:04d}", raw["generate"], raw["location"],
                                raw.get("contents", "")))
        else:
            add_plate(Plate(raw["barcode"], raw["type"], raw["location"], raw.get("contents", "")))

    durations = dict(data.get("durations") or {"default": 1})
    failures = [FailureSpec(**f) for f in data.get("failures", [])]
    return Workcell(instruments, plates, durations, failures)


def load_workcell(path: str | Path) -> Workcell:
    return parse_workcell(yaml.safe_load(Path(path).read_text()))
