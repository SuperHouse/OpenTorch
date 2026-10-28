# R350 replacement controller: plan

**Status:** plan agreed 2026-10-28. Phase 0 code written and unit-tested (`controller/`), not yet run on the oven; waiting for the FTDI USB-RS485 adapter.
**Goal:** replace the vendor "TN Series Reflow Control Systems" software (see `vendor-software-issues.md` for why) with an in-house controller on a Raspberry Pi with a 10" touchscreen mounted at the oven.
**Protocol reference:** `CLAUDE.md` (register map, frame formats, capture results).

## Architecture

```
┌──────────────── Raspberry Pi ─────────────────┐
│  Chromium (kiosk) on 10" touchscreen          │
│        │  HTTP + WebSocket (localhost/LAN)    │
│  ┌─────▼──────────────────────────────────┐   │
│  │ Controller service (Python, systemd)   │   │
│  │  • API: FastAPI (REST + live updates)  │   │
│  │  • Supervisor: state machine, safety,  │   │
│  │    profiles, logging (SQLite)          │   │
│  │  • Devices: ZoneCard, SensorCard, VFD  │   │
│  │  • Bus: owns the serial port, 2400 ↔   │   │
│  │    9600 7N2 switching, CRC/LRC, quirks │   │
│  └─────┬──────────────────────────────────┘   │
└────────┼──────────────────────────────────────┘
   FTDI USB-RS485 ── oven RS-485 bus (12 zones, 4 sensors, VFD)
```

### Layers

1. **Bus:** the only code that touches the serial port.
   - Card side: Modbus RTU at 2400 8N1. The cards' 6-byte write acknowledge is non-standard (`adr 06 regHi regLo crc`), so the bus layer does its own framing. Do not use a generic Modbus library for the cards.
   - VFD side: Modbus ASCII at 9600 7N2. Switch the port settings, do one exchange, switch back.
   - Timeouts, retries, CRC/LRC checks, statistics.
   - Bus access is serialised (a single thread or asyncio task), so there are no port conflicts.
2. **Devices:** `ZoneCard` (addresses 51–62), `SensorCard` (71–74, read only) and `Vfd` (ASCII slave 1). Typed methods such as `read_status()` → PV / output % / SV, `set_sv()`, `heat_on()`, `heat_off()`, `read_params()`, `run()`, `stop()` and `set_frequency()`.
3. **Supervisor:**
   - State machine: Idle → Heating → Running profile → Stopping → Fault.
   - Polling loop, safety rules (below), profiles/recipes (SV per zone + conveyor speed), and the stack light (AL2 on zone 1 = orange "heating").
   - Logs every reading and command to SQLite.
4. **API:** FastAPI. REST for commands and configuration, WebSocket for live readings. It runs in the same process as the supervisor, but a UI fault must never stop the supervisor.
5. **UI:** a browser page served by the controller.
   - Zone tiles (PV, SV, output %, state), start/stop, conveyor speed, profile selection, live chart, profile capture with the T1–T4 probes, fault display.
   - Shown in Chromium kiosk mode on the touchscreen, and viewable from other PCs on the LAN.

### Why a browser UI

- **Safety separation:** a UI crash or hang does not stop the control loop and safety logic.
- **Remote viewing:** the same page works on the touchscreen, a desktop or a phone.
- **Charts:** live temperature charts are easier to do well in a browser.
- A Qt/PySide app would tie the display to the control process and give no remote view.

### Stack

- Python 3.11+, pyserial, FastAPI + uvicorn (WebSockets), SQLite.
- Front end: plain HTML/JS plus a lightweight chart library (e.g. uPlot or Chart.js). No build step at first.
- Deployment: a systemd service for the controller, and Chromium in kiosk mode for the touchscreen.

## Safety requirements

These come from `vendor-software-issues.md`.

1. **A stop is final.** After a stop, nothing turns a heater back on until the operator explicitly starts again. This is the opposite of the vendor bug, where the patrol loop re-enabled zones after Stop Heating.
2. **Controller over-temperature cut-off**, independent of the UI and tested. The cards also regulate themselves, but the vendor's PC-side cut-off did not work.
3. **Communication loss turns heaters off.** If a card stops answering, or the bus fails, the controller commands all heaters off and shows a fault.
4. **Hardware watchdog (Phase 5).** The cards keep heating at their last command if the Pi dies, and software cannot fix that. Planned fix: a relay in the Start-latch or V1 (card/SSR-drive 12 V) circuit, held on by a heartbeat from the Pi, so heater drive power drops if the Pi stops.
5. **Defined conveyor behaviour on fault.** The VFD keeps running if communication stops (factory default, watchdog disabled). Decide whether the controller stops the belt on a fault, or keeps it running to clear boards from the oven. Consider enabling the VFD's own comms watchdog (Pr 9-03).
6. **One master on the bus.** Connect either the vendor PC or the controller, never both.
7. **Never overwrite the per-card PID values.** They are tuned per card and differ widely (see `CLAUDE.md`, Session 8). Read and keep them.
8. **Self-tune (`0x1022 = 0x03`) is not used** until it is understood. The manual warns that losing power during tuning can corrupt a card.

## Phases

| Phase | Work | Runs on | Done when |
|---|---|---|---|
| **0. Probe (read-only)** | `controller/probe.py`: read PV/output/SV from all 16 cards, read the parameters of each zone, read VFD status registers 2100H–2106H. Exercises the 2400 ↔ 9600 7N2 switching. No writes. | MacBook + USB-RS485 adapter | All devices read reliably over a 10-minute run with zero CRC errors, and the switching causes no errors. |
| **1. Controlled writes** | Single-zone tests: write a low SV, heat on/off, confirm the 6-byte ack. **Map zones to physical heaters** (heat one zone at a time and see which column warms). VFD run, speed, stop. | MacBook | Each command is confirmed on the oven, and the zone → heater map is recorded in `CLAUDE.md`. |
| **2. Library + simulator** | `bus` and `devices` modules with unit tests, using the recorded captures as test data. A **simulated oven** (fake cards with simple thermal behaviour) so the supervisor and UI can be developed without the oven. | Anywhere | Tests pass. The simulator runs the same code path as the real bus. |
| **3. Controller service** | Supervisor state machine, safety rules, logging, profiles, a command-line interface. | MacBook, then Pi | A full heat-up, hold and stop on the real oven from the CLI, with the safety rules tested (stop is final, comms loss, over-temperature). |
| **4. Web UI** | Zone tiles, controls, live chart, profiles, probe capture, fault display. | Pi + LAN | The operator can run a normal job from the touchscreen. |
| **5. Pi deployment + hardware watchdog** | Kiosk set-up, auto-start, watchdog relay wiring, operator procedure. | Pi at the oven | The vendor PC is no longer needed. The watchdog cuts heater drive when the controller is stopped. |

## Hardware to get

- A **USB-RS485 adapter with a genuine FTDI chip** and automatic direction control. The vendor PC's adapter failed on the 7N2 baud switching (R2 in `vendor-software-issues.md`), so avoid cheap clones.
- A Raspberry Pi 4 or 5, and a 10" HDMI or DSI touchscreen.
- Later: a relay module (or a dedicated watchdog relay) for the hardware watchdog.

**Connection:** the adapter's A/B/GND goes to the HEXIN converter's 3-way terminal, the same place the Saleae tap is connected now. Disconnect the vendor PC's RS-232 cable whenever the controller is connected. The HEXIN converter is port-powered, so with no PC attached it should be inactive. Check that it does not load the bus.

## Repository layout (proposed)

```
controller/
  probe.py            Phase 0 script
  oven/bus.py         serial port, framing, baud switching
  oven/devices.py     ZoneCard, SensorCard, Vfd
  oven/supervisor.py  state machine, safety, profiles
  oven/sim.py         simulated oven
  oven/api.py         FastAPI app
  web/                UI (HTML/JS)
  tests/
tools/decode_capture.py   bus capture decoder (exists)
```

The project is a git repository (since 2026-10-28).

## Open items that affect the design

- **Zone → physical heater map** (Phase 1). Zones 11 and 12 may both be bottom zones, and one top zone has no visible column.
- **Conveyor speed calibration:** T (minutes) ↔ F (Hz). Known points: T 20 → 5.51 Hz in the UI, and the DB `TimeP` table (4 Hz → 1815 s … 10 Hz → 360 s). Confirm against the belt speed.
- **How a broken thermocouple reports** (PV value or fault code). Test by unplugging a profile probe during Phase 0.
- **Profile sensor third word = 375:** meaning unknown.
- **Zone 1 reads low when cold** (11 °C against 16–22 °C) but tracked normally when hot.
- **What drives the red lamp / buzzer** (the "Alarm Area" card, zone 2, AL2?). Needed if the controller should sound an alarm.
