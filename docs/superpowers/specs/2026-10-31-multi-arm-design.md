# Multiple arms with handoff spots

## Intent

Scale a workcell past one arm without collisions. Each arm can reach a fixed set of instruments;
the lab is laid out so arms only overlap at **handoff spots** (passive plate positions both arms
reach). Zones are not a concept in the code: they are implicit in what each arm reaches. A move
between instruments of different arms is relayed through handoff spots. The planner stays free
to use any instrument; when its choice changes the route, it sees that choice's time and arms.

Decided with Jared (2026-10-31): planner free to choose across zones; zones implicit from reach;
arms overlap only at handoffs; no duplicating single instruments (sealer, peeler, centrifuge)
just for an arm's sake — plates cross over instead; plate readers are duplicated (one per zone). "Let's go with that and see how
it works out."

## Workcell config

- Arm entries take `reach: [instrument ids]`. An arm without `reach` reaches everything (today's
  single-arm workcells unchanged). Arms never reach arms.
- New instrument type `handoff` (passive positions, `slots` = capacity, default 1).
- Validation (ConfigError): every non-arm instrument is reached by at least one arm; only
  handoff spots may be reached by more than one arm; every arm can reach every other arm
  through handoffs; reach lists name known instruments.

## Routing (my calls)

- Shortest chain of arms from an arm reaching the source to one reaching the destination
  (BFS over arms, linked by shared handoffs; ties by sorted ids).
- A move is **one block**, relayed when needed: arm 1 moves src → handoff, arm 2 moves handoff →
  dst (more hops for longer chains). The block locks every arm and handoff on the route, so a
  plate never sits on a handoff between blocks. Cost: both arms held for the relay (~20–30 s);
  crossings should be rare-ish. The simulator checks an arm only moves within its reach.
- When a source or destination is an instrument variable whose candidates are reached by
  different arms (e.g. `?tc0` = tc1 in arm 1's zone or tc3 in arm 2's), the route depends on the
  binding: the compiled block carries one instruction list per binding (route variants); binding
  picks one. Durations and locked arms differ per variant.
- Liquid handlers no longer stick to one per workflow (numbered variables like other step types,
  keeping the stay-put rule), so prep and capture can use different handlers.

## Planner and live mode

- CP-SAT: a variant block's size and locks depend on the binding (size = the bound variant's
  duration; each variant's instruments locked only when that variant is chosen).
- Idle hold: CP-SAT park options only for removals whose route doesn't depend on a binding (my
  call: keeps the model tractable); lab-tech parks (executor) work on bound blocks and park in
  the parking spot with the shortest route.
- Validator, executor, live executor, report: unchanged in kind (they work on bound blocks); the
  floor plan shows plates on handoff spots during relays.
- v1 greedy planners: single-arm only (not extended).

## Demo

`examples/libprep_2arm.yaml`: arm1 (library prep) reaches storage, hotel1, scanner, stamper,
tc1, tc2, lh1, hs1, sealer, peeler, read1; arm2 (capture) reaches hotel2, dry1, lh2, hs2, tc3,
tc4, centrifuge, read2; one handoff spot `ho1` (2 positions) shared. Three batches of
`examples/libprep_capture.py`, live, report `docs/reports/libprep3_2arm_live.html`; compare with
the single-arm demo (makespan, arm busy time, crossings).

## Non-goals

Arm travel time by distance, arm collision geometry, multiple handoff paths load-balancing,
greedy-planner support for multiple arms, zone-aware parking in CP-SAT for route-variant moves.
