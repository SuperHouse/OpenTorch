# R350 replacement controller: plan

**Status:** plan agreed 2026-10-28. **Phase 0 done (2026-11-03):** `controller/probe.py` through an FTDI FT232R on the oven DE9 read all 16 cards and the VFD for 10 minutes with zero missed replies and zero bad frames (results under "Our own runs: Phase 0 and Phase 1" in `CLAUDE.md`). **Phase 1 mostly done (2026-11-03)** with `controller/phase1.py`: writes and the 6-byte ack confirmed, zone → heater map recorded, zone 1 confirmed as the heater-contactor enable and master cut-off, the 30-minute idle shutdown explained, conveyor run/stop confirmed. Remaining: a reliability run with heating on.
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
   FTDI USB-RS232 ── oven DE9 ── HEXIN RS-232↔RS-485 (in chassis)
                                    └── RS-485 bus (12 zones, 4 sensors, VFD)
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
3. **Communication loss turns heaters off.** If a card stops answering, or the bus fails, the controller commands all heaters off and shows a fault. Single missed replies do happen while heating (1–5 per few minutes on some runs, probably switching noise; 2026-11-03), so retry each read and only fault after several consecutive misses (decide the count and time from a heating reliability run).
4. **Hardware watchdog (Phase 5).** The cards keep heating at their last command if the Pi dies, and software cannot fix that. Planned fix: a relay in the Start-latch or V1 (card/SSR-drive 12 V) circuit, held on by a heartbeat from the Pi, so heater drive power drops if the Pi stops.
5. **Defined conveyor behaviour on fault.** The VFD keeps running if communication stops (factory default, watchdog disabled). Decide whether the controller stops the belt on a fault, or keeps it running to clear boards from the oven. Consider enabling the VFD's own comms watchdog (Pr 9-03).
6. **One master on the bus.** Connect either the vendor PC or the controller, never both.
7. **Never overwrite the per-card PID values.** They are tuned per card and differ widely (see `CLAUDE.md`, Session 8). Read and keep them.
8. **Self-tune (`0x1022 = 0x03`) is not used** until it is understood. The manual warns that losing power during tuning can corrupt a card.
9. **Use zone 1 as the master heater cut-off.** Zone 1's AL2 output (active while zone 1 runs with AL2 = 400) switches the heater contactor (confirmed 2026-11-03). Stop and fault paths send heat-off to zone 1 (and AL2 = 0) first, then to every other zone. Confirmed 2026-11-03: dropping zone 1 cuts heater mains while another zone's card is still commanding heat (its SSR stays driven but the heater gets no mains). PVs keep rising for about a minute after a stop (heat stored in the plates); over-temperature rules must allow for that. Zone 1 must be running for any zone to heat, and it is a single point of failure.

## Phases

| Phase | Work | Runs on | Done when |
|---|---|---|---|
| **0. Probe (read-only)** | `controller/probe.py`: read PV/output/SV from all 16 cards, read the parameters of each zone, read VFD status registers 2100H–2106H. Exercises the 2400 ↔ 9600 7N2 switching. No writes. | MacBook + FTDI USB-RS232 adapter on the oven DE9 | All devices read reliably over a 10-minute run with zero CRC errors, and the switching causes no errors. **Done 2026-11-03.** |
| **1. Controlled writes** | Single-zone tests: write a low SV, heat on/off, confirm the 6-byte ack. **Map zones to physical heaters** (heat one zone at a time and see which column warms). VFD run, speed, stop. | MacBook | Each command is confirmed on the oven, and the zone → heater map is recorded in `CLAUDE.md`. A reliability run with heating on (missed replies seen while heating). |
| **2. Library + simulator** | `bus` and `devices` modules with unit tests, using the recorded captures as test data. A **simulated oven** (fake cards with simple thermal behaviour) so the supervisor and UI can be developed without the oven. | Anywhere | Tests pass. The simulator runs the same code path as the real bus. |
| **3. Controller service** | Supervisor state machine, safety rules, logging, profiles, a command-line interface. | MacBook, then Pi | A full heat-up, hold and stop on the real oven from the CLI, with the safety rules tested (stop is final, comms loss, over-temperature). |
| **4. Web UI** | Zone tiles, controls, live chart, profiles, probe capture, fault display. | Pi + LAN | The operator can run a normal job from the touchscreen. |
| **5. Pi deployment + hardware watchdog** | Kiosk set-up, auto-start, watchdog relay wiring, operator procedure. | Pi at the oven | The vendor PC is no longer needed. The watchdog cuts heater drive when the controller is stopped. |

## Hardware to get

- A **USB-RS232 adapter with a genuine FTDI chip** (FT232R or similar) and a real RS-232 transceiver (MAX3232 class, ±5 V or more), with DTR and RTS wired to the DE9. The vendor PC's adapter failed on the 7N2 baud switching (R2 in `vendor-software-issues.md`), so avoid PL2303 and cheap clones.
- Spare / fallback: a **genuine FTDI USB-RS485 adapter** with hardware direction control (TXDEN). See "Bus connection".
- A Raspberry Pi 4 or 5, and a 10" HDMI or DSI touchscreen.
- Later: a relay module (or a dedicated watchdog relay) for the hardware watchdog.

## Bus connection (decided 2026-10-29)

**Chosen: FTDI USB-RS232 adapter into the oven's existing external DE9.** The HEXIN RS-232↔RS-485 converter in the chassis stays as it is. The controller (MacBook for Phases 0–3, then the Pi) uses the same cable.

Why:

- **No changes to the oven wiring.** To go back to the vendor PC, move the cable.
- **The HEXIN path is proven.** It has handled the 2400 ↔ 9600 7N2 switching for about 12 years. The Session 9 crashes happened inside the vendor PC (adapter and driver), not on the bus.
- **The FTDI Linux/macOS driver is reliable** with 7-bit/2-stop framing and fast baud changes. On the Pi, use the `/dev/serial/by-id/usb-FTDI_…` name so the device name does not change.
- **Echo is handled.** If the HEXIN returns our own bytes on the RS-232 side, `Bus.detect_echo()` finds this at start-up and strips the echo.

Points to check in Phase 0:

- **HEXIN power (inferred port-powered).** It probably takes power from DTR/RTS. pyserial asserts both when it opens the port, so the code must not drop them. A weak-output adapter could starve the converter. Symptoms would be missed or corrupt replies.
- **Turnaround.** The fastest card reply seen was 1.6 ms after the request. The vendor software received every reply through the HEXIN, so its automatic direction control releases the bus fast enough at 2400 baud. Confirm that there are no missing replies over the 10-minute run.
- **Only one master.** Unplug the vendor PC's cable from the DE9 before you connect the controller.

**Fallback (only if the HEXIN gives trouble):** connect a genuine FTDI USB-RS485 adapter (A/B/GND) to the HEXIN's 3-way terminal, where the Saleae tap goes now, and disconnect the HEXIN's RS-485 side. The FTDI chip switches the transmitter itself, so there is no echo and the turnaround is exact. The cost is one wiring change inside the chassis.

**Not chosen: Pi UART + RS485-TTL module.** Cheap auto-direction modules set the turnaround with an RC time constant that is often marginal at 2400 baud, and GPIO direction control from Linux has unreliable timing. It also ties the Pi ground directly to the bus with no isolation, and needs the PL011 UART (the mini-UART cannot do 2 stop bits) freed from the console and Bluetooth.

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

- ~~**Zone → physical heater map**~~ **Done 2026-11-03:** top T1–T4 = zones 1, 3, 5, 7, zone 9 = top convection zone over B5–B7; bottom B1–B7 = zones 2, 4, 6, 8, 10, 11, 12 (table in `CLAUDE.md`, "Heater layout").
- **Conveyor speed calibration:** T (minutes) ↔ F (Hz). Known points: T 20 → 5.51 Hz in the UI, and the DB `TimeP` table (4 Hz → 1815 s … 10 Hz → 360 s). Confirm against the belt speed.
- ~~**How a broken thermocouple reports**~~ **Answered for sensor cards (2026-11-03):** OUT drops to 0 at once and PV climbs to about 1360 over about 10 s, so detect an open input from OUT = 0 or PV > 400, not from PV alone. Not yet seen on a zone card (check in Phase 1, with heat off). The card's PV is filtered (a few seconds' lag), which the over-temperature rules must allow for.
- **Profile sensor third word = 375:** meaning unknown.
- **Zone 1 reads low when cold** (11 °C against 16–22 °C) but tracked normally when hot.
- **What drives the red lamp / buzzer** (the "Alarm Area" card, zone 2, AL2?). Needed if the controller should sound an alarm.
