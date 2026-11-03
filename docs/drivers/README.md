# Driver notes

Working notes for the instrument drivers, started 2026-11-03 ahead of the hardware hookup. Nothing here is built yet. The sequence and the hookup checklist are in CLAUDE.md ("Next: real hardware"); these notes collect what we learn per device and the decisions that cut across devices.

One file per device: [inheco-odtc.md](inheco-odtc.md) (thermocycler).

## The interface (draft)

One small interface per instrument type, and the simulator (`sim/world.py`) becomes one implementation of each. Draft shape, to be settled once two real devices are in hand:

- **connect / startup:** find the device, reset it, bring it to a known state (door closed, programs uploaded), and report what it holds where it can tell (inventory).
- **start(instruction):** begin one of our ops (`open_door`, `run_program`, ...). It returns at once, with a handle.
- **progress:** optional readings while it runs (temperatures, elapsed time).
- **completion or a structured error:** see "Errors" below.
- **state:** idle, busy, in error, offline; plus whatever device state the scheduler cares about (door open?) that the device may not be able to report.
- **stop / abort,** where the device allows it.

Our ops map onto device commands inside the driver. The compiler and planners keep speaking ops; nothing above the driver learns vendor command names.

## Cross-cutting rules

- **Every command has a timeout.** Default: twice the device's own duration estimate, or our planned duration, plus a margin. A timeout is an error like any other.
- **Every request, reply and event goes to the persistent event log** before it is acted on, so a restarted controller can tell what was in flight.
- **Drivers serialize their own commands.** Many devices have no command queue and reject a second command while busy; the executor should never see that.
- **Device state we cannot query, the driver tracks,** and resets to a known value at startup (e.g. close the door).
- **Simulated twin first.** Each driver ships with a simulated version that behaves the same (including the quirks below), and every change is rehearsed against it. Where a device has its own simulation mode, use it for the first run on real hardware.
- **Real step times go back into the workcell `durations`** as we measure them.

## Errors

One structured error type for all drivers, so the executor can decide without knowing the device. Draft fields:

- `instrument`, `op`, the vendor code and message (kept verbatim)
- `severity`:
  - **warning:** the op finished; log and continue
  - **error:** the op failed, the device is usable; retry or replan
  - **fault:** the device is out of service until a human acts (often a power cycle)
- `plate_state`: whether a plate is still inside, if known; this drives "park plates stranded on a down instrument"

## Transport notes

- **SiLA 1.2** (SOAP over HTTP): the device posts its completion events back to a URL we register, so the driver runs a small HTTP server and the controller's firewall must let the device reach it. INHECO devices use this.
- **SiLA 2** (gRPC): a client library per device; observable commands stream progress.
- **Vendor SDK or serial:** wrapped by our own driver.

## Prior art

- **PyLabRobot** (MIT): backends for some devices, including an experimental ODTC backend and a SiLA 1 interface. Useful as a reference for message formats; thin on timeouts, logging and recovery.
- **UniteLabs** SDK: SiLA 2 connectors, some commercial.
