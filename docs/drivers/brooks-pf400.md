# Brooks PreciseFlex 400 (arm)

Four-axis benchtop SCARA sample handler with a servo gripper and an optional linear rail. Researched 2026-11-03 from the vendor's TCP Command Server docs, before any hookup; nothing is verified on our hardware yet. This is the safety-critical device: Jared runs anything that moves, and watches it the first time (CLAUDE.md).

Sources (from the [TCS archive](https://manuals.brooks.com/brooks-preciseflex-support/archive/tcp-command-server-archive/), release TCS API 3.0 D4, 2024-10-25):
- *TCS User's Guide* 3.0C2 (July 2023): transport, standard commands, error codes
- *PARobot Plug-In for TCS* 3.0D4: plate pick/place, gripper, stations
- also in the zip: Force_Compliance, Height_Detect, IntelliGuide plug-in docs (not read yet)

## How it's controlled

- **TCS (TCP Command Server)** is a program written in Brooks' GPL that runs *on the arm's controller*. It is open source and shipped as a project you copy to `/flash/projects/` (FTP or the Guidance tools); it can auto-start at boot. Use the `Tcp_cmd_server_pa` variant, which adds the PARobot plug-in (plate commands). Requires GPL OS 3.2+.
- **Ports:** 10100 for robot 1 (motion commands), 10000 for a status thread that never blocks on motion. More robots use 10n00.
- **Format:** ASCII, one command per line (LF), case-insensitive, space-separated arguments. In PC mode (the default; `mode 1` is the human Telnet mode) each reply is `<code> <data>` + CRLF: `0` is success, a negative number is a GPL or TCS error code followed by a message.
- **TCP is a stream:** a command or reply can be split across packets or share one. The driver must frame on line endings.
- No events, no request ids: one connection per channel, one reply per command, in order.

## Motion semantics (the important part)

- **Motion commands return as soon as the motion starts.** A second motion command blocks (no reply) until the first finishes and the second starts; that is how moves blend. To know a motion has ended: `waitForEom` (replies only when the robot stops) or poll `state`.
- **Asynchronous faults are reported late:** if a robot exception happens between commands, the *next* command is not run and gets the error code as its reply. A driver that waits on a long `waitForEom` must watch the status port (`sysState`) in parallel to see faults and e-stops promptly.
- `halt` stops immediately with power on. `movetosafe` goes to the taught safe position.
- **TCS does no collision checking** inside the work volume (PARobot doc, on `ChangeConfig` and `movetosafe`). Safe transit between stations is our responsibility: taught safe/park positions, and approach heights per station.
- Global speed: `mspeed <1-100>`. Run the first sessions slow (10–20 %).

## Startup sequence

1. Connect to 10000 (status) and 10100 (robot 1); `version` to log firmware, TCS and plug-in versions.
2. `hp 1 <timeout>`: high power on (fails if the e-stop is engaged or power doesn't come up in time).
3. `attach 1`: take control of the robot; motion requires it.
4. Check the gripper first: `HomeAll_IfNoPlate` homes only if no plate is held, and reports whether one is. **A plate in the gripper at startup is an inventory fact and a manual-recovery case.** Homing moves the arm.
5. `LoadFile` the station/profile file if it isn't loaded at boot.

## Stations, i.e. taught positions

- A **station** is a taught robot location plus access data: horizontal (hotel-style) or vertical access, `ZClearance` (approach/withdraw distance, default 50 mm), `ZAbove` (default 5 mm), grasp offset, rail position, optional pallet (1–3D grid, e.g. a hotel's slots). **Profiles** are speed/accel/straight-line settings, referenced by index.
- Default limits: 20 stations, 20 profiles (`N_LOC`, `N_PROF` in `Globals.gpl`; raise them for our workcell). Indices start at 1.
- Teaching: put the arm in free mode (`FreeMode 0`), hand-guide it into the nest holding a plate, `TeachPlate <station> [zclearance]`. Saved on the controller with `StoreFile` (to `Tcs.gpo`).
- **Mapping:** each of our nest ids (`tc1`, `stamp1.src`, `hotel` slots) maps to a station index (or a pallet station + index). That map belongs in the workcell YAML, next to the arm's `reach`.

## Commands we need

| our op | TCS command | notes |
|---|---|---|
| `check_status` | `nop` (robot port), `sysState` (status port) | `nop` surfaces any pending async exception |
| `move` (pick half) | `PickPlate <station> [compliance] [torque%]` | approaches, grips, withdraws to the approach position. **Returns -1 if a plate was grasped, 0 if the force gripper found none**: our grip check |
| `move` (place half) | `PlacePlate <station> [compliance] [torque%]` | places and withdraws; doc doesn't say how release is confirmed |
| hotel slot | `PalletIndex <station> x y z` then pick/place | pallet stations for hotels and stackers |
| rail | `MoveRail <station> [mode]` | can move during the next pick/place |
| stop | `halt`; `movetosafe` | |
| readings | `where` / `wherec` / `wherej`, `state`, `IsFullyClosed` | gripper fully closed = holding nothing |
| gripper | `GraspPlate width speed force`, `ReleasePlate`, `Gripper 1/2` | `GraspData` sets force grip used by PickPlate |

One `move` (src → dst) is therefore `PickPlate src`, `waitForEom`, `PlacePlate dst`, `waitForEom`, with a pallet index set first where needed. Moves between distant stations may need via-points (a safe position) that we teach and insert.

## Errors

- Negative reply codes: GPL error codes (documented in the Brooks library; not collected yet) plus TCS's own, e.g. -2805 unknown command, -2806 command exception, -2814 invalid location index, -2816 undefined location, -2817 undefined profile, -2820 invalid station, -2821 undefined station.
- Severity mapping (draft): undefined station/profile and bad arguments are **errors** (our config is wrong); grip failure (`PickPlate` returns 0) is an **error** with the plate's location unknown → stop and ask a human; any motion fault, power loss or e-stop is a **fault**: the arm is out of service until a person clears it.
- After any fault, assume nothing about where the plate is. Recovery is manual the first time.

## Timing

Our model uses 10 s per `arm.move`. Real times depend on distance, profile speed and `mspeed`; measure per station pair at the hookup and feed them back into `durations` (they may need to become per-pair, not one number).

## Prior art

PyLabRobot `pylabrobot/brooks/precise_flex/` (MIT) is substantial and **hardware-validated**: it keeps a list of confirmed firmware/TCS/module stacks, a recording of a real PF400 (extended reach, 400 mm Z, vision gripper, no rail), a simulator, and its own PF400 kinematics. It drives the arm by Cartesian moves computed client-side. We'd rather use taught TCS stations and `PickPlate`/`PlacePlate` (the controller owns approach paths), but their transport, version checks and recordings are worth reusing or depending on.

## To find out at the hookup

- Controller IP; GPL and TCS versions; is TCS installed and set to auto-start? Which project (`Tcp_cmd_server` or `_pa`)?
- Gripper type (servo, force-controlled, dual?) and whether `USE_FORCE_GRIPPER` is on, so `PickPlate`'s grip check means something.
- Rail? Reach variant? Z travel?
- E-stop wiring and what TCS replies while it's engaged; how to recover (power, `attach`, home).
- Collaborative/force-limited behavior: what happens on contact, at what speed.
- Which licenses are installed (compliance, height detection).
- Real move times between our stations; how many stations and via-points the workcell needs.
