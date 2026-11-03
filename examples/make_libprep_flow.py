"""Generate a flow-line library prep workcell: one station per stage of
examples/libprep_capture.py (its w.stage marks), in process order, each with its own arm, joined
to the next station by a handoff spot. Plates only move forward, one station at a time; every
station is shared by all batches. One arm serves several neighbouring stations (ZONES), and
arm zones meet at handoff spots. Sized for about 105 batches of 96 samples a day (one batch
every 823 s): a station has about (time a batch spends on a type / 823 s) instruments, plus
slack. Trimmed by running the 10k day: one fewer liquid handler at "final" made batches queue
there for about 13 h each.

Each batch's plates start in the storage of the station that first uses them, and `store`
puts a plate in its station's storage (the lib plate is archived at "cleanup", the
consolidated sample at "final").

    .venv/bin/python examples/make_libprep_flow.py --batches 105 > examples/libprep_flow.yaml
"""
import argparse

import yaml

# station -> instrument counts, in process order (pre-amp up to "pcr", post-amp after)
STATIONS = [
    ("start",   {"storage": 1, "barcode_scanner": 1, "head_stamper": 1}),
    ("tag",     {"heated_shaker": 1, "plate_sealer": 1, "thermocycler": 2, "plate_peeler": 1}),
    ("stop",    {"storage": 1, "barcode_scanner": 1, "head_stamper": 1, "heated_shaker": 1,
                 "liquid_handler": 2}),
    ("index",   {"storage": 1, "barcode_scanner": 1, "head_stamper": 1, "heated_shaker": 1,
                 "centrifuge": 1, "plate_sealer": 1}),
    ("pcr",     {"thermocycler": 4, "plate_peeler": 1, "centrifuge": 1}),
    ("cleanup", {"storage": 1, "barcode_scanner": 1, "liquid_handler": 3, "plate_reader": 1}),
    ("pool",    {"storage": 1, "barcode_scanner": 1, "liquid_handler": 2,
                 "vacuum_concentrator": 5}),
    ("hyb",     {"liquid_handler": 1, "plate_sealer": 1, "centrifuge": 1, "thermocycler": 20,
                 "plate_peeler": 1}),
    ("capture", {"liquid_handler": 1, "heated_shaker": 5}),
    ("wash",    {"liquid_handler": 2, "plate_sealer": 1, "thermocycler": 2, "plate_peeler": 1}),
    ("final",   {"storage": 1, "barcode_scanner": 1, "liquid_handler": 3, "plate_reader": 1,
                 "plate_sealer": 1}),
]
SHORT = {"storage": "store", "barcode_scanner": "scan", "head_stamper": "stamp",
         "heated_shaker": "hs", "plate_sealer": "seal", "thermocycler": "tc",
         "plate_peeler": "peel", "liquid_handler": "lh", "centrifuge": "spin",
         "plate_reader": "read", "vacuum_concentrator": "dry"}
# where each batch's plates start: reagents at the station that stamps them, empties at the
# station that first fills them (new_plate("empty") takes empties in plate order: lib, cln,
# cap, seq)
REAGENTS = {"G": ("gdna", "start"), "TAG": ("tagmentation_mix", "start"),
            "STP": ("stop_buffer", "stop"), "PCR": ("pcr_master_mix", "index"),
            "IDX": ("index_adapters", "index")}
EMPTIES = ["start", "cleanup", "pool", "final"]


# zones are joined by one-way conveyors: the upstream arm puts a plate on, the belt carries it
# for BELT_SECONDS without holding either arm, and the downstream arm takes it off
BELT_SECONDS, BELT_SLOTS = 30, 6

# arm zones: the stations each arm serves, in order; zones meet at handoffs. One arm per
# station left each under 15% busy. Storage stations load their arm most (a 60 s retrieve or
# store holds it), so they get fewer neighbours. On the 10k day (seed 0): 11 arms 31 idle-hold
# violations, these 6 arms 57, 5 arms 95, 4 arms 178; latency rose 10 min per arm removed.
ZONES = [["start", "tag"], ["stop"], ["index", "pcr"], ["cleanup", "pool"], ["hyb", "capture"],
         ["wash", "final"]]


def workcell(batches: int, zones: list[list[str]] | None = None,
             belt_seconds: int = BELT_SECONDS) -> dict:
    """`zones` defaults to ZONES; [[s] for s, _ in STATIONS] gives one arm per station. Zones
    are joined by one-way conveyors taking `belt_seconds`, or by handoff spots if 0."""
    zones = zones or ZONES
    link = "belt" if belt_seconds else "ho"
    counts = dict(STATIONS)
    assert [s for z in zones for s in z] == [s for s, _ in STATIONS], "zones must cover STATIONS in order"
    instruments, arms = [], []
    for k, zone in enumerate(zones):
        ids = []
        for stage in zone:
            for itype, n in counts[stage].items():
                for i in range(1, n + 1):
                    iid = f"{SHORT[itype]}_{stage}" + (f"{i}" if n > 1 else "")
                    extra = {"slots": 4000} if itype == "storage" else {}
                    instruments.append({"id": iid, "type": itype, "stage": stage, **extra})
                    ids.append(iid)
        hotel = f"hotel_{zone[0]}"
        # parking for the whole zone (no stage: parking picks the nearest hotel anyway)
        instruments.append({"id": hotel, "type": "hotel", "slots": 10 * len(zone)})
        ids.append(hotel)
        links = [f"{link}{j}" for j in (k, k + 1) if 1 <= j < len(zones)]
        arms.append({"id": f"arm_{zone[0]}", "type": "arm", "reach": ids + links})
    if belt_seconds:
        handoffs = [{"id": f"belt{j}", "type": "conveyor", "slots": BELT_SLOTS,
                     "seconds": belt_seconds, "to": arms[j]["id"]} for j in range(1, len(zones))]
    else:
        handoffs = [{"id": f"ho{j}", "type": "handoff", "slots": 2} for j in range(1, len(zones))]
    plates = []
    for b in range(1, batches + 1):
        plates += [{"barcode": f"{p}{b:04d}", "type": t, "location": f"store_{stage}"}
                   for p, (t, stage) in REAGENTS.items()]
        plates += [{"barcode": f"E{4 * (b - 1) + i:04d}", "type": "empty",
                    "location": f"store_{stage}"} for i, stage in enumerate(EMPTIES, 1)]
    return {"instruments": arms + handoffs + instruments,
            "durations": {"default": 1, "check_status": 0}, "plates": plates}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--batches", type=int, default=105)
    ap.add_argument("--arm-per-station", action="store_true",
                    help="one arm per station instead of the shared-arm ZONES")
    ap.add_argument("--belt-seconds", type=int, default=BELT_SECONDS,
                    help="conveyor travel time between zones; 0 = handoff spots instead")
    a = ap.parse_args()
    zones = [[s] for s, _ in STATIONS] if a.arm_per_station else None
    flags = (" --arm-per-station" if a.arm_per_station else "") + (
        f" --belt-seconds {a.belt_seconds}" if a.belt_seconds != BELT_SECONDS else "")
    print(f"# Generated by examples/make_libprep_flow.py --batches {a.batches}{flags}; see that file.")
    print(yaml.safe_dump(workcell(a.batches, zones, a.belt_seconds), sort_keys=False, width=100))
