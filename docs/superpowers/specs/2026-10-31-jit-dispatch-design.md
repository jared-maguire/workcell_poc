# Just-in-time dispatch as the live engine

## Intent

Experiments (2026-10-31) showed a plain dispatcher matching or beating live CP-SAT:
- With noise, on the same seeds:
  - 10 PCRs: 5:50–6:59 against CP-SAT's 7:18–7:57.
  - 3 library preps: 11:19–12:11 against 11:55–12:03.
- It runs in about 0.1 s, against 30–144 s.
- Without noise it lands within 1.4% of CP-SAT's proven optimum on library prep.

Decided with Jared: make just-in-time dispatch the default, remove the live CP-SAT machinery
(re-planning, grid, window, the CP-SAT idle-hold model), and extend dispatch to two arms.

## Dispatcher (`workcell/planners/dispatch.py`, `plan_dispatch`)

- **Event-driven.** It reuses the greedy planner's lock/nest/borrow rules and its rule that a
  stamper or liquid handler serves one workflow at a time. At each event it starts every block
  that can start.
- **Priority:** the most remaining work first (longest path of planned durations to the
  workflow's end), ties in batch order. Binding choices are tried shortest route first, so a
  plate goes to a free instrument in its own zone before one that needs a relay.
- **Parking when blocked:** a plate whose next move is blocked only by a full or reserved
  destination goes to the nearest parking spot with room (hotel, else storage), as in
  greedy_hotel.
- **Idle rule (lab tech):** with `max_idle_hold` L, a plate idle in an instrument nest for
  L − 60 s whose next block (a single move out) hasn't started goes to the nearest parking
  spot with room.
- **Parked moves:** a parked plate's next move is rewritten to start from the spot only once
  its destination is bound. That makes routes concrete on two arms.
- **Noisy times (`actual`):** blocks may run with actual instruction durations. Decisions never
  read a running block's actual duration, only completed blocks' end times, so running the
  dispatcher with actual durations is a live run.

## Live mode

`run_live` does four things:
1. Forecasts by dispatching with planned durations.
2. Runs by dispatching with seeded noisy durations.
3. Replays the run in the simulator.
4. Validates the as-run plan (`check_durations=False`).

Stats are planned versus actual makespan, total delay, parks, idle violations and dispatch
time. There are no re-plans.

## v1 `run`

New mode `jit` (dispatch, planned durations, no idle limit), added to the default modes and
allowed on multi-arm workcells. The greedy, greedy_hotel and cpsat modes stay as baselines.
CP-SAT keeps:
- route variants
- the persistent crash-isolated solver process
- greedy and serial warm starts

## Removed

- **Live executor:** `live/executor.py`, `live/order.py`.
- **From `cpsat.py`:** `ReplanState`, `replan_cpsat`, `plan_idle_hold`, the grid, the window,
  park options and gaps in the model, churn.
- **Elsewhere:**
  - `Plan.partial`
  - `idle.hold_chains`
  - the park helpers only CP-SAT used
  - `tools/bench_mini.py`
  - the live CLI flags for re-planning, grid and window
  - the tests of all of the above

## Non-goals

Timing limits between steps, forecasting during a run, and multi-arm greedy/greedy_hotel.
