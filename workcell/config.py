"""Workcell configuration: instruments, nests, plates, durations, failures."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

# Lab instruments that run generic timed steps (`w.step`): the plate(s) must be in their nests.
STEP_TYPES = frozenset({"liquid_handler", "heated_shaker", "plate_sealer", "plate_peeler",
                        "centrifuge", "plate_reader", "vacuum_concentrator"})
# Instrument types with two plate positions, `<id>.src` and `<id>.dst`.
TWO_POSITION_TYPES = frozenset({"head_stamper", "liquid_handler"})
INSTRUMENT_TYPES = frozenset(
    {"arm", "storage", "hotel", "handoff", "barcode_scanner", "head_stamper", "thermocycler"}
    | STEP_TYPES
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
    reach: frozenset[str] | None = None     # arms: instruments it can reach (None = all)


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
    _groups: dict[str, frozenset[str]] = field(default_factory=dict, init=False, repr=False,
                                               compare=False)

    def nests(self) -> dict[str, Nest]:
        out: dict[str, Nest] = {}
        for inst in self.instruments.values():
            if inst.type in ("storage", "hotel"):
                slots = inst.slots
                if slots is None:
                    slots = UNLIMITED_HOTEL_SLOTS if inst.type == "hotel" else 1
                out[inst.id] = Nest(inst.id, inst.id, slots)
            elif inst.type == "handoff":
                out[inst.id] = Nest(inst.id, inst.id, inst.slots or 1)
            elif inst.type in TWO_POSITION_TYPES:
                for suffix in ("src", "dst"):
                    nid = f"{inst.id}.{suffix}"
                    out[nid] = Nest(nid, inst.id, 1)
            elif inst.type != "arm":
                out[inst.id] = Nest(inst.id, inst.id, 1)
        return out

    def nest_owner(self, nest_id: str) -> str:
        return nest_id.split(".", 1)[0]

    def arms_reaching(self, instrument: str) -> list[str]:
        """Arms that can reach `instrument` (an arm without a reach list reaches everything)."""
        return sorted(a for a in self.of_type("arm")
                      if self.instruments[a].reach is None or instrument in self.instruments[a].reach)

    def group(self, instrument: str) -> frozenset[str]:
        """The instruments connected to `instrument` through arms and handoff spots (all of
        them when no arm has a reach list). Only handoffs are reached by several arms, so
        groups meet nowhere else."""
        if instrument not in self._groups:
            seen, todo = {instrument}, [instrument]
            while todo:
                i = todo.pop()
                links = (self.instruments[i].reach or self.instruments
                         if self.instruments[i].type == "arm" else self.arms_reaching(i))
                for j in links:
                    if j not in seen:
                        seen.add(j)
                        todo.append(j)
            comp = frozenset(seen)
            self._groups.update({i: comp for i in comp})
        return self._groups[instrument]

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
        reach = raw.get("reach")
        instruments[iid] = Instrument(iid, itype, raw.get("slots"),
                                      frozenset(reach) if reach is not None else None)

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
    wc = Workcell(instruments, plates, durations, failures)
    _check_reach(wc)
    return wc


def _check_reach(wc: Workcell) -> None:
    """Arms only overlap at handoff spots: every other instrument is reached by exactly one
    arm."""
    arms = wc.of_type("arm")
    if all(wc.instruments[a].reach is None for a in arms):
        return
    for a in arms:
        for name in sorted(wc.instruments[a].reach or ()):
            if name not in wc.instruments:
                raise ConfigError(f"arm {a!r} reach names unknown instrument {name!r}")
            if wc.instruments[name].type == "arm":
                raise ConfigError(f"arm {a!r} reach names another arm {name!r}")
    for iid, inst in wc.instruments.items():
        if inst.type == "arm":
            continue
        reachers = wc.arms_reaching(iid)
        if not reachers:
            raise ConfigError(f"instrument {iid!r} is reached by no arm")
        if len(reachers) > 1 and inst.type != "handoff":
            raise ConfigError(f"instrument {iid!r} is reached by more than one arm "
                              f"({', '.join(reachers)}); only handoff spots may be shared")
    # arms need not all connect: a workcell may hold separate groups (each workflow then
    # stays in its plates' group; see Workcell.group)


def load_workcell(path: str | Path) -> Workcell:
    path = Path(path)
    if not path.is_file():
        raise ConfigError(f"workcell file not found: {path}")
    try:
        data = yaml.safe_load(path.read_text())
    except yaml.YAMLError as e:
        raise ConfigError(f"{path}: malformed YAML: {e}") from None
    return parse_workcell(data)
