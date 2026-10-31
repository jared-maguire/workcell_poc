"""Simulated instruments: a World that applies instructions and rejects illegal ones."""
from __future__ import annotations

from workcell.config import Workcell
from workcell.ir import Instruction


class InstrumentError(Exception):
    def __init__(self, instrument: str, op: str, message: str):
        super().__init__(f"{instrument}.{op}: {message}")
        self.instrument = instrument
        self.op = op
        self.message = message


class World:
    def __init__(self, workcell: Workcell, locations: dict[str, str]):
        self.workcell = workcell
        self._nests = workcell.nests()
        self._loc = dict(locations)
        self._doors = {i: False for i in workcell.of_type("thermocycler")}  # False = closed
        self._trays: dict[str, str | None] = {i: None for i in workcell.of_type("storage")}
        self._dispatch = {
            "storage": self._storage,
            "hotel": self._noop,
            "arm": self._arm,
            "barcode_scanner": self._scanner,
            "head_stamper": self._stamper,
            "thermocycler": self._thermocycler,
        }

    # --- queries ---
    def location(self, plate: str) -> str:
        return self._loc[plate]

    def occupants(self, nest: str) -> list[str]:
        return [p for p, loc in self._loc.items() if loc == nest]

    def door_open(self, tc_id: str) -> bool:
        return self._doors[tc_id]

    def presented(self, storage_id: str) -> str | None:
        return self._trays[storage_id]

    def snapshot(self) -> dict:
        return {"locations": dict(self._loc), "doors": dict(self._doors),
                "presented": dict(self._trays)}

    # --- apply ---
    def apply(self, instr: Instruction) -> None:
        inst = self.workcell.instruments.get(instr.instrument)
        if inst is None:
            raise InstrumentError(instr.instrument, instr.op, "unknown instrument")
        if self._failure(instr, "fault"):
            raise InstrumentError(instr.instrument, instr.op, "instrument faulted")
        if instr.op == "check_status":
            return
        handler = self._dispatch[inst.type]
        handler(instr)

    def _failure(self, instr: Instruction, kind: str) -> bool:
        strings = {v for v in instr.args.values() if isinstance(v, str)}
        return any(
            f.kind == kind and f.op == instr.op
            and f.instrument in (None, instr.instrument)
            and (f.plate is None or f.plate in strings)
            for f in self.workcell.failures
        )

    @staticmethod
    def _err(instr: Instruction, message: str) -> InstrumentError:
        return InstrumentError(instr.instrument, instr.op, message)

    def _noop(self, instr: Instruction) -> None:
        raise self._err(instr, f"unsupported op {instr.op!r}")

    # --- storage ---
    def _storage(self, instr: Instruction) -> None:
        sid, plate = instr.instrument, instr.args.get("plate")
        if instr.op == "retrieve":
            if self._loc.get(plate) != sid:
                raise self._err(instr, f"plate {plate} not in {sid}")
            if self._trays[sid] is not None:
                raise self._err(instr, f"tray of {sid} occupied by {self._trays[sid]}")
            self._trays[sid] = plate
        elif instr.op == "store":
            if self._trays[sid] != plate or plate is None:
                raise self._err(instr, f"plate {plate} not on tray of {sid}")
            self._trays[sid] = None
        else:
            raise self._err(instr, f"unsupported op {instr.op!r}")

    # --- arm ---
    def _arm(self, instr: Instruction) -> None:
        if instr.op != "move":
            raise self._err(instr, f"unsupported op {instr.op!r}")
        plate, src, dst = (instr.args[k] for k in ("plate", "src", "dst"))
        if self._loc.get(plate) != src:
            raise self._err(instr, f"plate {plate} not at {src}")
        for nest in (src, dst):
            if nest not in self._nests:
                raise self._err(instr, f"unknown nest {nest}")
            owner = self.workcell.nest_owner(nest)
            if owner in self._doors and not self._doors[owner]:
                raise self._err(instr, f"door of {owner} is closed")
        if src in self._trays and self._trays[src] != plate:
            raise self._err(instr, f"plate {plate} not on tray of {src}")
        if len(self.occupants(dst)) >= self._nests[dst].capacity:
            raise self._err(instr, f"nest {dst} full")
        if dst in self._trays and self._trays[dst] is not None:
            raise self._err(instr, f"tray of {dst} occupied by {self._trays[dst]}")
        if src in self._trays:
            self._trays[src] = None
        if dst in self._trays:
            self._trays[dst] = plate
        self._loc[plate] = dst

    # --- scanner ---
    def _scanner(self, instr: Instruction) -> None:
        if instr.op != "scan":
            raise self._err(instr, f"unsupported op {instr.op!r}")
        found = self.occupants(instr.instrument)
        if not found:
            raise self._err(instr, "no plate in scanner")
        if self._failure(instr, "barcode_mismatch"):
            raise self._err(instr, f"barcode misread, expected {instr.args['expect']}")
        if found != [instr.args["expect"]]:
            raise self._err(instr, f"barcode mismatch: expected {instr.args['expect']}, found {found[0]}")

    # --- stamper ---
    def _stamper(self, instr: Instruction) -> None:
        if instr.op != "stamp":
            raise self._err(instr, f"unsupported op {instr.op!r}")
        for key in ("src", "dst"):
            nest = f"{instr.instrument}.{key}"
            if self._loc.get(instr.args[key]) != nest:
                raise self._err(instr, f"plate {instr.args[key]} not in {nest}")

    # --- thermocycler ---
    def _thermocycler(self, instr: Instruction) -> None:
        tc = instr.instrument
        if instr.op in ("open_door", "close_door"):
            want = instr.op == "open_door"
            if self._doors[tc] == want:
                raise self._err(instr, f"door already {'open' if want else 'closed'}")
            self._doors[tc] = want
        elif instr.op == "run_program":
            if self._doors[tc]:
                raise self._err(instr, "door is open")
            n = len(self.occupants(tc))
            if n != 1:
                raise self._err(instr, "no plate in thermocycler" if n == 0
                                else f"expected exactly one plate, found {n}")
        else:
            raise self._err(instr, f"unsupported op {instr.op!r}")
