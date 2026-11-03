# INHECO ODTC (thermocycler)

On-deck thermocycler with a motorized drawer an arm can load. Researched 2026-11-03 from the vendor manual, before any hookup; nothing is verified on hardware yet. **We don't yet know that this is our thermocycler**; replace this note's assumptions once we know the model.

Source: [ODTC Firmware Command Set v1.09](https://www.inheco.com/data/pdf/odtc-firmware-command-set-1018-1331-23-0520-1419-00.pdf) (2021, firmware build 7783). Section numbers below refer to it.

## Transport

- **SiLA 1.2**, SOAP over HTTP, not SiLA 2. Commands are POSTs to `http://<device>:8080/` with `SOAPAction: "http://sila.coop/<Command>"` (example in §7.1.4.4).
- **Asynchronous commands:** the reply carries returnCode 2 ("accepted") and an estimated `duration` (e.g. `PT1S`). The device later POSTs a `ResponseEvent` (returnCode 3 = finished) to the `eventReceiverURI` given to `Reset`. It also sends `StatusEvent`, `ErrorEvent` and, during a run, `DataEvent`. We must answer each event.
- **Discovery** (§11.2): UDP broadcast of `SiLA_Finder_Request` to port 4711; the 134-byte reply includes the device name, firmware version, SOAP port, DHCP flag and MAC. DHCP by default; a static IP can be set with `SetConfiguration` (power cycle to apply).
- **Every command takes `requestId`** (ours, unique) and `lockId` (nullable; `LockDevice` reserves the device).

## States and concurrency

- SiLA states: startup → standby → idle ⇄ busy, plus inError. `Reset` works anywhere; `Initialize`, `LockDevice` and `SetConfiguration` only in standby; the rest in idle/busy (§4).
- **No command queue** (§3). The parallel/sequential table says: `ReadActualTemperature` and `StopMethod` can run alongside most things; door commands and `ExecuteMethod` are sequential with each other, **so the door cannot open during a run**, which matches our simulator.

## Commands we need

| our op | ODTC command | estimate | notes |
|---|---|---|---|
| startup | `Reset` (with eventReceiverURI), `Initialize` | | `simulationMode=true` makes the device fake runs (10 s each): use it for the first hookup |
| `check_status` | `GetStatus` | | synchronous |
| `open_door` / `close_door` | `OpenDoor` / `CloseDoor` | 14 s each | not interruptible. Our model assumes 5 s |
| `run_program` | `ExecuteMethod(PreMethod)`, then `ExecuteMethod(Method)` | preheat ~10 min; program as scripted | see "Programs" |
| abort | `StopMethod` | 1 s | also stops a held temperature |
| startup (programs) | `SetParameters(MethodsXML)` | 60 s | idle only |
| readings | `ReadActualTemperature` | 1 s | works during a run; values in 1/100 °C (mount, lid, ambient, heatsink, ...) |
| after a run / recovery | `GetLastData` | | temperature trace of the last run, CSV inside XML |

**No command reports the door position.** The driver tracks it and closes the door at startup.

## Programs (§6)

- A **Method** is an XML list of steps: `PlateauTemperature`, `PlateauTime` (s), `Slope` (°C/s), overshoot settings, `LidTemp`, and loops via `GotoNumber`/`LoopNumber` (backward only). It also has a `Variant` that must match the hardware (`960000` for the 96-well).
- Limits: 100 steps, 100 loops per step, 16 h. Our longest program (capture PCR) is 38 steps flattened, so no loop compression is needed; hybridization (4 h) is fine.
- **A PreMethod must run first**, bringing block and lid to the Method's `StartBlockTemperature` (and lid, if `MatchLidTemperatures` is on), or the device refuses the Method. It then holds that temperature, and the door still works. It takes 10 min unless `DynamicPreMethodDuration` (on by default) shortens it. A Method with `PostHeating` holds its last step instead, so programs can be chained without a PreMethod.
- The device stores up to 255 Methods and 255 PreMethods by unique name, kept across power cycles. Upload is a merge.
- **Plan:** at startup, upload every program the batch uses, named by a hash of its content, plus one PreMethod per start temperature. `run_program` runs the PreMethod then the Method.
- **Scheduling consequence:** our model has no preheat. In v1 the preheat is part of `run_program` (longer than planned). Later the dispatcher could preheat while the plate is still on its way, since a preheated block holds and the door still opens.

## Errors (§5)

| code | meaning | class | our severity |
|---|---|---|---|
| 4 | busy with another command | | driver bug (we serialize) |
| 9 | not allowed in this state | | error |
| 11 | invalid parameter | | error |
| 12 | finished with warning | | warning |
| 2002 | door error: drawer blocked | Error | error; plate state unknown |
| 2003 | MethodSet error | Error | error (bad program XML) |
| 2004 | command not accepted | Error | fault (manual says power cycle) |
| 2005 | temperature out of spec | Warning | warning; worth surfacing on the run |
| 2008 | drawer warning: disposable missing or misplaced | Warning | warning; **may tell us whether a plate is inside**, check at hookup |
| 2009 / 2010 | ambient too hot / SD card | Warning | warning |
| 1000, 2000, 2001, 2007 | software, hardware, cable, mains | DeviceError | fault: inError, power cycle (only a comms drop recovers with `Reset`) |

`StatusEvent` extensions carry internal codes; the manual says not to rely on them across firmware versions.

## Prior art

PyLabRobot `ExperimentalODTCBackend` (`pylabrobot/legacy/thermocycling/inheco/odtc_backend.py`, MIT) plus its SiLA 1 interface (`legacy/storage/inheco/scila/inheco_sila_interface.py`): runs an HTTP server on a random port for events, one command at a time, generates Method XML from a protocol. Its author says it was written quickly and tested on one 96-well unit; door state, cycle progress and recovery are unimplemented, and there are no timeouts. Use it as a reference for envelopes and event handling; write our own driver for timeouts, logging and recovery.

## To find out at the hookup

- Which variant (96 or 384) and firmware build; is our manual's version (build 7783) close?
- DHCP or static IP; can the controller and device reach each other both ways (events)?
- Does `CloseDoor` on a closed door fail or succeed?
- Real door times, ramp rates and preheat times; how long `SetParameters` really takes.
- Does warning 2008 fire with no plate in the drawer?
- What a mid-run controller disconnect looks like: does the run continue, and what do `GetStatus` and `GetLastData` say afterwards?
