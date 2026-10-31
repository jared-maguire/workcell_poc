"""Library prep (Illumina DNA Prep) + hybrid capture (IDT xGen) + consolidation into one well,
for one 96-sample gDNA plate per batch. Steps and times follow
docs/superpowers/specs/2026-10-31-libprep-capture-design.md (times in seconds)."""
from workcell.dsl import Workflow, workflow

TIMES = {"arm.move": 10, "open_door": 5, "close_door": 5, "scan": 4,
         "retrieve": 60, "store": 60, "check_status": 0}


def cycles(n, steps):
    """A thermocycler program segment repeated n times: steps are (°C, minutes)."""
    return [s for _ in range(n) for s in steps]


TAG = [(55, 15)]
BLT_PCR = [(68, 3), (98, 3), *cycles(9, [(98, 0.75), (62, 0.5), (68, 2)]), (68, 1)]
HYB = [(95, 0.5), (65, 240)]
CAPTURE_PCR = [(98, 0.75), *cycles(12, [(98, 0.25), (60, 0.5), (72, 0.5)]), (72, 1)]

LH, HS = "liquid_handler", "heated_shaker"


@workflow(times=TIMES)
def libprep_capture(w: Workflow, batch: int):
    g = w.own(f"G{batch:04d}")
    tag, stp, pcr, idx = (w.own(f"{p}{batch:04d}") for p in ("TAG", "STP", "PCR", "IDX"))
    lib, cln, cap, seq = (w.own(w.new_plate("empty")) for _ in range(4))

    def seal(p):
        w.step("plate_sealer", "seal", p, seconds=10)

    def peel(p):
        w.step("plate_peeler", "peel", p, seconds=10)

    def spin(p):
        w.step("centrifuge", "spin", p, seconds=30)

    # --- library prep (Illumina DNA Prep) ---
    w.stamp(g, lib, seconds=60)                              # 30 µl gDNA
    w.store(g)
    w.stamp(tag, lib, seconds=60)                            # eBLT + TB1
    w.store(tag)
    w.step(HS, "shake", lib, seconds=60)
    seal(lib)
    w.thermocycle(lib, program=TAG)
    peel(lib)
    w.stamp(stp, lib, seconds=60)                            # stop buffer
    w.store(stp)
    w.step(HS, "shake_incubate", lib, seconds=360)           # mix 1 min, RT 5 min
    w.step(LH, "magnet_wash", lib, seconds=720)              # post-tagmentation cleanup, 2x TWB
    w.stamp(pcr, lib, seconds=60)                            # EPM master mix
    w.store(pcr)
    w.stamp(idx, lib, seconds=60)                            # index adapters
    w.store(idx)
    w.step(HS, "shake", lib, seconds=60)
    spin(lib)
    seal(lib)
    w.thermocycle(lib, program=BLT_PCR)
    peel(lib)
    spin(lib)
    w.step(LH, "bead_cleanup", lib, cln, seconds=1800)       # double-sided cleanup to new plate
    w.store(lib)
    w.step("plate_reader", "quantify", cln, seconds=300)

    # --- hybrid capture (IDT xGen) ---
    w.step(LH, "pool", cln, cap, seconds=900)                # 8 x 500 ng -> 12 pools + blockers
    w.store(cln)                                             # library archive
    w.step("vacuum_concentrator", "dry", cap, seconds=2700)
    w.step(LH, "resuspend", cap, seconds=600)                # hyb buffer + probes
    seal(cap)
    spin(cap)
    w.thermocycle(cap, program=HYB)                          # 95 °C 30 s, 65 °C 4 h
    peel(cap)
    w.step(LH, "add_beads", cap, seconds=600)                # washed streptavidin beads
    w.step(HS, "capture", cap, seconds=2700)                 # 65 °C 45 min, mixing
    w.step(HS, "heated_wash", cap, seconds=600)              # 2 x 5 min at 65 °C
    w.step(LH, "rt_washes", cap, seconds=900)                # washes I-III on the magnet
    w.step(LH, "add_pcr_mix", cap, seconds=180)
    seal(cap)
    w.thermocycle(cap, program=CAPTURE_PCR)
    peel(cap)
    w.step(LH, "bead_cleanup", cap, seconds=1200)            # 1.5x beads in place
    w.step("plate_reader", "quantify", cap, seconds=300)

    # --- consolidation ---
    w.step(LH, "consolidate", cap, seq, seconds=600)         # 12 pools -> 1 well
    w.store(cap)
    seal(seq)
    w.store(seq)                                             # ready for the sequencer


def batch():
    return [libprep_capture(batch=b) for b in (1, 2, 3)]
