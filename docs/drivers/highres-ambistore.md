# HighRes Biosolutions AmbiStore (plate storage)

Ambient, random-access plate storage carousel: a rotating set of stackers ("hotels") and a spatula that moves plates between a stacker slot and one of several transfer nests, where our arm picks them up. Researched 2026-11-03. **HighRes publishes no command manual**; the protocol below comes from PyLabRobot's driver, which cites a HighRes *User Manual* ("Message Formatting" section) we should ask HighRes for.

Sources:
- PyLabRobot `pylabrobot/high_res/sample_storage/` (MIT): one driver for AmbiStore, SteriStore and TundraStore, which share the protocol. **SteriStore is hardware-verified there; AmbiStore is marked "work in progress; not hardware-verified".** Its notes cite firmware 3.0.0.119.
- [HighRes sample storage brochure](https://7157e75ac0509b6a8f5c-5b19c577d01b9ccfe75d2f9e4b17ab55.ssl.cf1.rackcdn.com/RAXZFZSW-PDF-2-613050-4526550137.pdf) and reseller listings, for the hardware numbers.

## Hardware (brochure)

- **AmbiStore D:** 14 hotels, 336 96/384-well plates (140 deep-well, 462 1536-well). AmbiStore M: 28 hotels, double that.
- 1041 × 737 × 838 mm, 147 kg. 120/240 VAC. **Compressed air** (80 psi in the brochure, 70 psi in one listing).
- Ethernet. Up to six transfer nests. Delivery in about 12 s.

## Transport

- **Plain text over TCP, port 1000.** Factory IP `192.168.127.60`; every HighRes device also answers on `10.253.253.253`. Put the controller's interface on both subnets, with no gateway.
- Commands are case-sensitive, space-separated, ended by CRLF. Each reply is:
  1. `ACK! <command> <id>`
  2. zero or more data lines
  3. exactly one of `OK! <command> <id>`, `ABORTED! <command> <id>`, `ERROR! <command> <id>`; on an error, `Error <n>: ...` lines come before it
- **Commands block until done** (a pick replies `OK!` when the plate is on the nest). One at a time. PyLabRobot uses 30 s timeouts for queries and 240 s for motion.
- If a reply is cut off or malformed, the stream can't be trusted: close and reconnect.

## Commands (as PyLabRobot uses them)

| our op | command | notes |
|---|---|---|
| startup | `version`, `homedstatus`, `home`, `getstackerdimensions`, `settings` | dimensions give each stacker's slot count and pitch |
| `check_status` | `status`, `neststatus`, `doorstatus`, `platestatus` | `platestatus` says whether the spatula holds a plate |
| `retrieve` | `pick <stacker> <slot> <nest>` | stacker slot → transfer nest, for the arm to collect |
| `store` | `place <stacker> <slot> <nest>` | transfer nest → stacker slot |
| (none yet) | `nesttransfer <a> <b>` | nest to nest |
| inventory | `barcode <stacker> [slot]` | needs every transfer nest clear; `EMPTY` means *no read*, not an empty slot |
| recovery | `clearabort`, `enable`, `spatulaout`, `openalldoors`, `closealldoors` | |

Our simulator's storage already has this shape: `retrieve` presents a plate on the storage's tray, the arm takes it from there, `store` puts it back. The differences: the real one has several transfer nests (our model has one tray per storage), and we choose the stacker and slot. The workcell has to track the plate → (stacker, slot) map.

## Inventory: what it can and can't tell us

- **Transfer nests** have presence sensors (`neststatus`), safe to read any time.
- **Stacker slots have no presence sensor.** An empty slot is discovered only by trying to pick it, and an empty *top* slot faults. The barcode scan doesn't prove emptiness either. So **our controller owns the slot inventory**: it must persist, and startup reconciles it against barcode reads.
- `platestatus` tells us whether a plate is stuck on the spatula.

## Quirks (from PyLabRobot, firmware 3.0.0.119)

- `barcode` with a plate on any transfer nest waits for an automation door and eventually times out: clear the nests first.
- `openalldoors` can return `ERROR!` after actually completing; check `doorstatus`.
- An interrupted pick/place leaves the plate's location unknown. PyLabRobot blocks all further motion until a human says where it ended up (source, destination, or neither). We should do the same: the error carries "plate location unknown".
- Recovery (homing) must not run while the spatula holds a plate.

## Errors (draft mapping)

- `ERROR!` with `Error <n>` lines: **error** by default. Pick from an empty slot is "plate not found", our inventory being wrong.
- `ABORTED!`: an operator or interlock stopped it. Treat it as an **error** with the plate location unknown.
- Timeouts, protocol mismatches, or a cut-off stream: **fault** until reconnected and reconciled.
- We don't have the code list yet; it's in the HighRes manual.

## Timing

Our `pcr_live.py` assumes 60 s per `retrieve`/`store`; the brochure says about 12 s per delivery. Measure both directions, including carousel rotation, at the hookup.

## To find out at the hookup

- **Get the HighRes User Manual** (command reference, error codes) and the integrator's kit.
- Model (D or M), firmware version, how many transfer nests and where they sit relative to the arm.
- Stacker layout and pitch; which plate types; portrait or landscape.
- Doors: does the arm reach the nests through an automation door, and who opens it?
- Can it be driven directly, or only through HighRes Cellario? Does a Cellario license or lock get in the way?
- Real pick/place times; compressed air supply.
