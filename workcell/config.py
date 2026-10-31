"""Workcell configuration: instruments, nests, plates, durations, failures."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

INSTRUMENT_TYPES = frozenset(
    {"arm", "storage", "hotel", "barcode_scanner", "head_stamper", "thermocycler"}
)


UNLIMITED_HOTEL_SLOTS = 10**9      # a hotel without `slots`: capacity is a v1 non-goal

_FAILURE_KEYS = frozenset({"op", "kind", "instrument", "plate"})


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
                slots = inst.slots
                if slots is None:
                    slots = UNLIMITED_HOTEL_SLOTS if inst.type == "hotel" else 1
                out[inst.id] = Nest(inst.id, inst.id, slots)
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


def _require(raw, keys: tuple[str, ...], what: str):
    if not isinstance(raw, dict):
        raise ConfigError(f"{what}: expected a mapping, got {raw!r}")
    for k in keys:
        if k not in raw:
            raise ConfigError(f"{what}: missing {k!r} in {raw!r}")
    return [raw[k] for k in keys]


def _failure(raw, what: str) -> FailureSpec:
    if not isinstance(raw, dict):
        raise ConfigError(f"{what}: expected a mapping, got {raw!r}")
    raw = dict(raw)
    if "instruction" in raw:                     # spec §4 spelling
        if "op" in raw:
            raise ConfigError(f"{what}: give either 'op' or 'instruction', not both: {raw!r}")
        raw["op"] = raw.pop("instruction")
    unknown = sorted(set(raw) - _FAILURE_KEYS)
    if unknown:
        raise ConfigError(f"{what}: unknown key(s) {', '.join(unknown)} in {raw!r}")
    _require(raw, ("op",), what)
    return FailureSpec(**raw)


def parse_workcell(data: dict) -> Workcell:
    if not isinstance(data, dict):
        raise ConfigError(f"workcell config must be a mapping, got {type(data).__name__}")
    instruments: dict[str, Instrument] = {}
    for n, raw in enumerate(data.get("instruments") or []):
        iid, itype = _require(raw, ("id", "type"), f"instrument entry {n}")
        if itype not in INSTRUMENT_TYPES:
            raise ConfigError(f"unknown instrument type {itype!r} for {iid!r}")
        if iid in instruments:
            raise ConfigError(f"duplicate instrument id {iid!r}")
        if itype == "storage" and raw.get("slots") is None:
            raise ConfigError(f"storage {iid!r} needs 'slots'")
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

    for n, raw in enumerate(data.get("plates") or []):
        what = f"plate entry {n}"
        if isinstance(raw, dict) and "generate" in raw:
            count, prefix, location = _require(raw, ("count", "prefix", "location"), what)
            for i in range(1, count + 1):
                add_plate(Plate(f"{prefix}{i:04d}", raw["generate"], location,
                                raw.get("contents", "")))
        else:
            barcode, ptype, location = _require(raw, ("barcode", "type", "location"), what)
            add_plate(Plate(barcode, ptype, location, raw.get("contents", "")))

    durations = dict(data.get("durations") or {"default": 1})
    failures = [_failure(f, f"failure entry {n}") for n, f in enumerate(data.get("failures") or [])]
    return Workcell(instruments, plates, durations, failures)


def load_workcell(path: str | Path) -> Workcell:
    path = Path(path)
    if not path.is_file():
        raise ConfigError(f"workcell file not found: {path}")
    try:
        data = yaml.safe_load(path.read_text())
    except yaml.YAMLError as e:
        raise ConfigError(f"{path}: malformed YAML: {e}") from None
    return parse_workcell(data)
