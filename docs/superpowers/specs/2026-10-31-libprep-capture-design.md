# Library prep + hybrid capture demo (v2 live mode)

## Intent

A realistic demo workflow: take a 96-well plate of extracted gDNA through Illumina library prep
(Illumina DNA Prep), IDT xGen hybridization capture, and consolidation into a single well ready
for the sequencer. Three batches (three plates, 288 samples) run in parallel on one workcell,
scheduled live with re-planning and the idle-hold rule. It should look and time like a real lab
run, at plate level.

Confirmed with Jared (2026-10-31): a batch is one 96-sample plate; each batch ends in its own
single well (three batches → three sequencer-ready wells, not one combined pool).

## Goals / non-goals

- Goal: a workflow file a lab person would recognize step by step, with real durations, running
  three batches in parallel to completion in live mode, as-run schedule valid, report readable.
- Goal: general enough language/instrument additions that other protocols can be written the
  same way (a generic instrument step, two-plate liquid-handler transfers/pools).
- Non-goals: per-well volumes, reagent consumption accounting, liquid classes, seal state
  tracking, sequencer instrument control, QC-driven branching (QC is a timed read), time-window
  constraints (e.g. "beads must not sit more than N minutes") beyond the existing idle-hold rule.
- Anti-goal: a toy that only looks like the protocol by name. Steps and times must follow the
  sources below; where we merge sub-steps into one instrument step, its time is the sum.

## Sources

- Illumina DNA Prep with Enrichment checklist, doc 1000000048601 v05 (step list, shake/magnet/
  incubation times, bead cleanups, pooling 500 ng per library, 12-plex).
- Illumina DNA Prep reference guide, doc 1000000025416 (TAG and BLT PCR programs; program
  temperatures/times below are from that guide as remembered — approximate).
- IDT xGen hybridization capture protocol and protocol tips (hybridize 65 °C 4 h; streptavidin
  bead capture 45 min at 65 °C with mixing every 10–12 min; heated washes at 65 °C; post-capture
  PCR ~12 cycles; Dynabeads M-270).

## The workcell (`examples/libprep_workcell.yaml`)

New instrument types (my call on names): `liquid_handler` (8-channel pipettor with an on-deck
magnet; two plate positions `.src` / `.dst`, like the head stamper), `heated_shaker`
(thermomixer), `plate_sealer`, `plate_peeler`, `centrifuge`, `plate_reader` (fluorometric
quant), `vacuum_concentrator`. Existing: `arm`, `storage`, `hotel`, `barcode_scanner`,
`head_stamper`, `thermocycler`.

Counts for three parallel batches (my call, sized so no single instrument is absurdly
oversubscribed): 1 arm, 1 storage, 1 hotel (20 positions), 1 scanner, 1 head stamper,
2 liquid handlers, 3 thermocyclers, 2 heated shakers, 1 sealer, 1 peeler, 1 centrifuge,
1 plate reader, 1 vacuum concentrator.

Plates in storage per batch b ∈ {1,2,3}: gDNA plate `G000b`; reagent plates (owned by the
batch, distinct barcodes per batch from config pools, returned to storage after their last use) `RTAG` (BLT + TB1 master mix), `RSTP` (stop buffer), `RPCR` (EPM master mix),
`RIDX` (index adapters, unique per batch); empty plates for LIB, CLN, CAP, SEQ. Liquid-handler
reagents (beads, washes, ethanol, elution buffer, probes, hyb mix) live on the liquid handler's
deck and are not modeled as plates.

## Language additions (`workcell/dsl.py`)

- `w.step(instrument_type, op, *plates, seconds, after=None)`: a generic timed step on an
  instrument of that type. One plate: the plate goes to the instrument's nest (`.dst` for
  two-position instruments). Two plates (`src`, `dst`): only for two-position types
  (`liquid_handler`, `head_stamper`): `src` → `.src`, `dst` → `.dst`. `seconds` is required
  (timed workflows). This covers shake, seal, peel, spin, read, dry, magnet washes, bead
  cleanups, transfers and pools.
- Pooling and consolidation are two-plate liquid-handler steps (`pool` 96 libraries → 12 pools;
  `consolidate` 12 pools → 1 well). Wells are not modeled; the step's time is what matters.
- Existing `stamp`, `thermocycle`, `store` unchanged.

## The workflow (`examples/libprep_capture.py`)

Thermocycler times come from the program (existing rule: the program's sum), so `program` is
authoritative and the table's TC times are those sums; cycles are written out with a small
helper (e.g. `cycles(n, [(98, 0.75), (62, 0.5), (68, 2)])`) in the example file.

`@workflow(times=...)` with the live-PCR transport times (arm move 10 s, doors 5 s, scan 4 s,
storage retrieve/store 60 s, check_status 0). Steps (seconds; LH = liquid handler, HS = heated
shaker, TC = thermocycler):

| # | Step | Instrument | Time |
|---|------|------------|------|
| **Library prep (Illumina DNA Prep)** ||||
| 1 | gDNA → LIB plate (30 µl) | stamper G→LIB | 60 |
| 2 | add tagmentation mix (eBLT + TB1) | stamper RTAG→LIB | 60 |
| 3 | mix 1600 rpm 1 min | HS shake | 60 |
| 4 | seal | sealer | 10 |
| 5 | TAG program: 55 °C 15 min (the 10 °C hold is not timed) | TC | 900 |
| 6 | peel | peeler | 10 |
| 7 | add stop buffer (ST2), mix 1 min | stamper RSTP→LIB, HS shake | 60, 60 |
| 8 | incubate RT 5 min | HS incubate | 300 |
| 9 | post-tagmentation cleanup: magnet, discard, 2× TWB wash | LH magnet_wash | 720 |
| 10 | add EPM master mix; add index adapters | stamper RPCR→LIB; stamper RIDX→LIB | 60, 60 |
| 11 | mix 1 min; spin 30 s; seal | HS, centrifuge, sealer | 60, 30, 10 |
| 12 | BLT PCR: 68 °C 3 min, 98 °C 3 min, 9 × (98 °C 45 s, 62 °C 30 s, 68 °C 2 min), 68 °C 1 min | TC | 2175 |
| 13 | peel; spin | peeler, centrifuge | 10, 30 |
| 14 | library cleanup: double-sided bead cleanup into new plate (supernatant transfer, beads, 5 min, magnet, transfer, beads, 5 min, magnet, 2× EtOH, air-dry 5 min, elute, 2 min, magnet, transfer) | LH LIB→CLN cleanup | 1800 |
| 15 | quantify libraries | plate reader | 300 |
| **Hybrid capture (IDT xGen)** ||||
| 16 | pool 8 libraries × 500 ng → 12 pools, add blockers + Cot DNA | LH CLN→CAP pool | 900 |
| 17 | dry down pools | vacuum concentrator | 2700 |
| 18 | resuspend in hyb buffer + probes | LH resuspend | 600 |
| 19 | seal; spin | sealer, centrifuge | 10, 30 |
| 20 | hybridize: 95 °C 30 s, 65 °C 4 h | TC | 14430 |
| 21 | peel; add washed streptavidin beads | peeler, LH add_beads | 10, 600 |
| 22 | capture 65 °C 45 min, mixing | HS capture | 2700 |
| 23 | stringent washes 2 × 5 min at 65 °C | HS heated_wash | 600 |
| 24 | room-temperature washes I–III on magnet | LH rt_washes | 900 |
| 25 | add post-capture PCR mix (on beads); seal | LH add_pcr_mix, sealer | 180, 10 |
| 26 | post-capture PCR: 98 °C 45 s, 12 × (98 °C 15 s, 60 °C 30 s, 72 °C 30 s), 72 °C 1 min | TC | 1005 |
| 27 | peel; 1.5× bead cleanup in place | peeler, LH bead_cleanup | 10, 1200 |
| 28 | quantify captured pools | plate reader | 300 |
| **Consolidation** ||||
| 29 | normalize and combine 12 pools → 1 well | LH CAP→SEQ consolidate | 600 |
| 30 | seal SEQ; store SEQ (ready for the sequencer) | sealer, storage | 10 |

Plates return to storage when the batch is done with them: G after step 1, reagent plates after
their stamp, LIB after step 14, CLN after step 16 (library archive), CAP after step 29, SEQ last.

Hands-on instrument time per batch ≈ 10.5 h, of which 4 h is hybridization on a thermocycler.
The busiest shared instruments: liquid handlers (~2.1 h per batch), thermocyclers (~5.1 h per
batch), heated shakers (~1.1 h per batch).

Between steps a plate stays in its nest until its next move, as today; the idle-hold rule parks
it in the hotel when the wait would be long. The hotel is sized so it never fills (my call: 30
positions); the workcell is tuned until the demo runs cleanly.

One-plate liquid-handler steps (magnet washes, cleanups in place) use `.dst`; `.src` stays free
for two-plate steps.

## Simulator and planner

- `config.py`: accept the new types; nests: `liquid_handler` gets `.src`/`.dst` (capacity 1
  each); the others one nest each.
- `world.py`: a generic step on a new type checks that each named plate sits in one of that
  instrument's nests (otherwise an alarm, like other instruments); no other state.
- `compiler.py`: `step` ops compile to an op block like `stamp`/`thermocycle` (route plates to
  the nests, then `check_status` + the step instruction); numbered instrument variables
  (`?lh0`, `?hs0`, …) when a type has several instruments.
- CP-SAT, idle hold, executor, validator: no changes expected; they work on blocks. A plate
  sitting on, e.g., a heated shaker waiting for the sealer is covered by the idle-hold rule.
- Report: the floor plan and Gantt must show the new instrument types with readable labels.

## Demo

`python -m workcell live examples/libprep_workcell.yaml examples/libprep_capture.py --seed 0
--out docs/reports/libprep3_live.html` runs three batches. Acceptance: all three complete, as-
run schedule valid, report renders (floor plan shows all instruments, no console errors),
README gets a short section with the numbers and a link. Initial planning budget may need to be
raised (`--time-limit`); if the 3-batch model is too large for CP-SAT to find a plan in a few
minutes, that is a finding to report.

## Fallbacks and tests (my calls)

- If CP-SAT struggles with three batches, the builder may merge consecutive one-plate steps on
  the same instrument into one step (times summed, label names both) and raise the initial
  budget. If three batches still cannot be planned in a few minutes, stop and report to Jared
  rather than shrinking the demo.
- Tests stay small: compile one batch and check block shapes/durations; a serial plan of one
  batch validates and executes cleanly; world/config/DSL unit tests for the new pieces. The
  three-batch run is the demo command (checked by hand, report regenerated), with at most one
  slow-tier test of a one-batch live run.

## Left to the builder

Exact op names, the reagent plate pools' config, whether repeated one-plate steps on the same
instrument are merged, report colors/labels for new types, test design.
