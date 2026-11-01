# 3D lab view in the report

## Intent

Jared wants a WebGL rendering of the lab in operation inside the HTML report. It is "pure fun",
eye candy, but it must be accurate: what moves on screen is what the run did, at the time it did
it. Start with the two-arm library prep (`examples/libprep_capture.py` on
`examples/libprep_2arm.yaml`, 3 batches, `live`); the full 10k day comes later.

Look chosen from mockups (https://claude.ai/artifact/9xdSu4XC16sM7kAwk1ocAt): **control room**,
in both a light and a dark theme.

## Goals

- A new card, "Lab 3D", in the report next to the existing 2D "Floor plan" card. Both stay.
- Driven by the report's existing playback: play/pause, speed, and the time scrubber. Scrubbing
  or jumping shows the exact state at that time (state is a pure function of t, not accumulated).
- Accurate to the trace:
  - Each arm `move` instruction animates its arm carrying that plate from `src` to `dst`, starting
    at the instruction's start and taking its duration. Only the path shape is invented.
  - A plate rests in the nest it is in at time t (from `moves` and `initial_locations`, the same
    data the 2D floor plan uses). Plates always sit on top of their instrument, closed ones included
    (Jared, 2026-11-01: clearer); plates on storage/hotels are shown stacked, with a count.
  - An instrument glows (outline and status strip in the run colour) exactly while one of its
    non-move instructions runs.
- Plates coloured like the 2D floor plan: owned plates in their workflow's colour (`wfColor`),
  borrowed/shared plates in the neutral borrowed style.
- Orbit (drag), zoom (wheel/pinch). Works on a phone.

## Look

Control room, as in the mockup:
- grid floor
- instruments as low boxes with crisp outlines, sized by type
- SCARA-style arms in the accent colour
- plates in workflow colours, leaving a fading trail while carried
- light fog

**Themes.** The report follows `prefers-color-scheme` (see `report.css`); the 3D view does too,
and switches live when the theme changes.
- Light: pale grid ground and ink-blue outlines.
- Dark: night ground and glowing outlines.

The accents keep the same hues in both.

## Layout (automatic; positions are not in the workcell file)

- **Arms:** each arm stands at the centre of an arc of the instruments in its `reach` list.
  - The arc opens toward the arm's handoffs.
  - Handoffs sit between the arms that share them.
- **Separate groups** (`Workcell.group`) are laid out side by side as separate lines.
- **One arm without a reach list** (v1 PCR workcells): that arm reaches everything, so all
  instruments go in one ring around it.
- **Arm length:** an arm's links must reach every instrument on its ring. Scale the links, or
  the ring radius, to fit.
- **Report data:** `report_data` adds `reach` (list or null) to each instrument's entry. Nothing
  else about the data contract changes.

## Self-contained

- The report must keep working offline: no network URLs, and a test enforces this.
- Vendor a pinned three.js build (MIT) into `workcell/report/vendor/`, with its licence. Inline
  it at build time, like the CSS and JS.
  - Every report gets it (about 0.6 MB).
  - The 3D scene is built lazily, the first time the card scrolls into view.
  - It stops rendering while the card is off screen or playback is paused and nothing changes.

## Non-goals

- Hand-placed positions in the workcell YAML (maybe later).
- The 10k-day scale: it should not break, but frame rate there is not a goal yet.
- Clicking 3D objects to select blocks.
- Realistic instrument models.

## Anti-goals

- Motion that contradicts the trace, such as an arm moving when no move instruction runs, or
  a plate appearing somewhere it never went.
- A report that needs the network, or breaks the existing views.

## Move timing (Jared's call)

- Moves keep their exact timing. A 10 s move at the default 60 s/s lasts 1/6 s on screen.
- The live speed menu gains 1, 2 and 5 s/s, so you can slow down and watch an arm work. The
  default stays 60.
- Plate trails keep fast moves visible at high speed.
- Nothing is stretched past its real end.

## Settled after the builder check (Claude's calls)

- **Instruction timing.** It is rebuilt in JS from each block's `start` plus the cumulative
  durations of its instructions, which run in order. A Python test checks that this matches
  `trace.events` for the demo run, so the data contract does not grow.
- **Alarm runs.** A run that ended in an alarm renders up to the alarm time; nothing moves after
  it.
- **Where a carried plate is.** During a move `[start, start + duration)` the plate is in the
  gripper. Before the move it is at `src`, and after it is at `dst`. This is a drawing rule only;
  occupancy counting stays as it is.
- **Relays.** A cross-zone relay is shown as each arm's move in turn, with the plate resting on
  the handoff in between, exactly as the instructions run.
- **Glow.** It is one run colour, the accent, used while a non-move instruction with duration
  over 0 runs. Handoffs, storage and hotels never glow. Zero-length ops (`check_status`) don't
  glow.
- **Camera.** It fits the whole workcell when first shown, then the viewer can orbit and zoom.
- **Labels.** Instrument ids appear as small labels that fade out when zoomed far away. There
  is no minimap.

## Left to the builder

- Module split, e.g. `report/lab3d.js` inlined beside `report.js`.
- Instrument sizes per type.
- The arm's inverse kinematics.
- How storage stacks are shown.
- Trail length.
- Camera defaults.
- Test strategy. Python tests for the data and inlining. Pure JS functions for "state at t",
  checked from Python via a small harness or by a browser check, as the report is checked today.
