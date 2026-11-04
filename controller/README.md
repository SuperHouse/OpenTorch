# R350 controller

Replacement controller for the Torch R350 reflow oven. Plan: `../replacement-plan.md`. Protocol: `../CLAUDE.md`.

Current stage: **Phase 1 done** (2026-11-04). The Phase 2/3 device and supervisor layers and `ovenctl.py` work on the oven: the 30-minute all-zone reliability run at SV 100 had 0 missed replies. Conveyor control (safety rule 10 in `../replacement-plan.md`) passed its oven test (2026-11-04). Next: a full heat-up at reflow temperatures.

## Layout

| Path | Purpose |
|---|---|
| `oven/protocol.py` | Frame building and parsing: card RTU (incl. the 6-byte write ack), VFD ASCII, register constants. No I/O. |
| `oven/bus.py` | `Bus`: the only code that touches the serial port. Switches 2400 8N1 (cards) ↔ 9600 7N2 (VFD) per exchange. **Read-only unless created with `read_only=False`.** |
| `probe.py` | Phase 0 script: snapshot or timed monitor of all cards and the VFD. Read-only. |
| `oven/devices.py` | `ZoneCard`, `SensorCard`, `Vfd`: typed reads and writes (SV and AL2 writes are read back). |
| `oven/supervisor.py` | `Supervisor`: heating state machine (idle → heating → fault) and the safety rules (see its docstring). |
| `oven/runlog.py` | CSV run log, shared by `phase1.py` and `ovenctl.py`. |
| `ovenctl.py` | Phase 2/3 command line on the supervisor: status, off, multi-zone heat with hold and a missed-reply report. |
| `phase1.py` | Phase 1 script: status, heat off, single-zone heat test (with zone 1 as the heater-contactor enable), master cut-off test, stack lamp, VFD run/stop, watch. See "Phase 1" below. |
| `logs/` | CSV log of every heat test and watch run on the oven. |
| `tests/` | Unit tests. `fake_oven.py` simulates the bus (also used by `--fake`), including the heater contactor (no zone heats unless zone 1 runs with AL2 = 400) when `require_contactor` is set. |

## Set-up (MacBook)

```sh
cd controller
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python3 -m unittest            # all tests should pass
python3 probe.py --fake        # dry run against the simulated oven
```

## Running the probe on the oven

1. **Disconnect the vendor PC** (unplug its RS-232 cable from the oven's DE9). Only one master on the bus.
2. Plug the FTDI USB-RS232 adapter into the oven's external DE9 (the vendor PC's socket). The HEXIN converter in the chassis does the RS-485 side. Leave the Saleae tap on the HEXIN terminal if you want a capture of the probe's traffic. See "Bus connection" in [../replacement-plan.md](../replacement-plan.md).
3. Oven: mains on, E-stop released, **press the green Start** (the cards are unpowered until then).
4. Find the port: `python3 probe.py --list-ports`. Look for the FTDI entry (`/dev/cu.usbserial-…`).
5. Snapshot: `python3 probe.py --port /dev/cu.usbserial-XXXX`
   - It first checks whether the adapter echoes its own bytes, then reads all 16 cards, the zone parameters and the VFD status.
   - If every device shows "no reply": check that the green Start was pressed, and that the adapter is a straight-through DE9 (not null-modem). Then check on the Saleae capture whether the requests reach the bus. If they do not, the HEXIN may not be getting power from DTR/RTS.
6. Reliability run (Phase 0 "done" test): `python3 probe.py --port /dev/cu.usbserial-XXXX --monitor 600 --log probe-run.csv`
   - Done when all devices read over 10 minutes with zero bad frames, and the VFD reads (every 2nd round) cause no errors on the card side.

The probe never writes. The `Bus` refuses writes unless created with `read_only=False`, which only Phase 1 code will do.

## Phase 1: controlled writes (`phase1.py`)

Same set-up as the probe (FTDI on the DE9, vendor PC unplugged, green Start pressed). Know where the red Stop and the E-stop are before any heat test.

Run the steps in this order. Stop at any step whose result is not as expected.

1. `python3 phase1.py --port P status`: read only. Shows PV/OUT/SV and AL2 for each zone. (The command register 0x1022 is write-only: the cards do not answer a read of it.)
2. `python3 phase1.py --port P off`: heat-off (0xCC) to all zones. The first write; harmless, because the cards are already off. Checks the 6-byte write ack.
3. `python3 phase1.py --port P heat N --sv 60 --minutes 5`: one zone at a time. Prints all zone PVs every 2 s and logs to `logs/heat-zN-<time>.csv`. The neighbouring zones' PVs rising shows which zones are physically next to each other. Look at the chamber to see which column heats. By default it also runs zone 1 at SV 5 with AL2 = 400: zone 1's AL2 output switches the heater contactor, so no other zone can heat without it (`--no-enable-z1` to leave zone 1 off). Master cut-off test: `--cutoff-after 120` sends heat-off to zone 1 only after 120 s and keeps the zone under test commanded on; the contactor should drop and the heat stop.
4. `python3 phase1.py --port P watch`: read only. Logs all zones every 2 s and stops when the bus goes silent (the oven has left the started state), printing the time of the last reply. Use it to time drop-outs without watching the oven.
5. `python3 phase1.py --port P lamp on` / `lamp off`: AL2 on zone 1; the stack light should go orange / green.
6. `python3 phase1.py --port P vfd run --hz 5`, then `vfd stop`: the belt must be clear. Speed is capped at 15 Hz.

Heat-test safety (details in the `phase1.py` docstring): the SV is capped at 100 °C and read back before heat-on, so the card itself limits the heat even if the Mac dies. All other zones get heat-off first. Every exit, including Ctrl-C and closing the terminal, sends heat-off to all zones. It aborts on overshoot (SV + 15 °C), on any zone at 120 °C, or when the zone under test stops answering. The test SV stays on the card afterwards.

If heat-off ever reports NO ACK: press the oven's red Stop.

## Multi-zone heat and the reliability run (`ovenctl.py`)

Same set-up as above. `ovenctl.py` drives the oven through the supervisor, which applies the safety rules on every poll:

- **Stop is final:** heat-on is sent only once per zone, at start. Nothing turns a zone back on.
- **Zone 1 is on first and off first** (it holds the heater contactor in). If zone 1 is not in `--zones`, it runs at SV 5 as the enable only. On every stop, Z1 AL2 is set back to 0.
- **Comms loss:** each poll tries a read twice. A zone that fails `--fail-polls` polls in a row (default 3) while heating is a fault. Single misses are only counted.
- **Over-temperature:** a heating zone at SV + `--margin` (default 20 °C), any other zone at the highest SV + margin, or any PV above 400 (open thermocouple) is a fault.
- **Any fault or exit** (time up, Ctrl-C, closing the terminal) sends heat-off to zone 1, then every other zone. A heat-off that is not acked is retried every poll, and the run prints "PRESS THE OVEN'S RED STOP".
- **Conveyor:** runs at `--belt-hz` (default 5 Hz, max 15) from after the first heat-offs and before any card set-up. If the VFD does not then report running at that frequency with no error, the start is refused and nothing heats. While heating, a stopped belt, a VFD error or VFD comms loss (same `--fail-polls` rule) is a fault. After heat-off or a fault the belt keeps running until every zone is below `--belt-cool` (default 80 °C), then it is stopped; `heat` keeps polling until then. With the lid closed the oven cools about 1 °C per minute, so usually the oven's 30-minute idle shutdown stops the belt first; `heat` then logs that and exits. An open zone thermocouple keeps the belt running. Every missed read is logged as a `miss: <error>` row in the CSV. Ctrl-C during that cool-down leaves the belt running (the drive keeps going without comms; the oven's idle shutdown stops it, or `phase1.py vfd stop`). `--no-belt` leaves the conveyor alone and is only accepted up to SV 100.
- SV is capped at 280 °C (raised from 150 on 2026-11-04 for reflow runs).
- **Profiles:** `heat --profile profiles/NAME.toml` instead of `--sv` sets each zone's SV and the belt speed from a file (`setpoints` = 12 values, zones 1–12 in order, 0 = off; `belt_hz`; `--belt-hz` overrides it). `profiles/loctite-gc10.toml` holds the production project found on the cards on 2026-10-28, **not yet confirmed** as the GC10 settings.

**Reflow profile run** (next; Phase 3 done test): lid closed, belt clear, no boards, the oven attended for the whole run. `heat --profile profiles/loctite-gc10.toml --minutes 20`. It heats to the profile SVs (expect 10–20 min to settle; the 255/260 zones take longest), holds, stops, and cools with the belt running until the idle shutdown. Record the heat-up time, overshoot and hold band per zone, and any `miss:` rows. Optional: run a probe through the oven on T1 during the hold to compare with `Loctite GC10 profile.PNG`.

Commands:

1. `python3 ovenctl.py --port P status`: read only. Zones, T1–T4, Z1 AL2 and the VFD.
2. `python3 ovenctl.py --port P off`: heat-off to all zones, zone 1 first, then Z1 AL2 = 0.
3. `python3 ovenctl.py --port P heat --sv 100 --zones all --minutes 30`: heats, holds, stops, then watches for 2 minutes (`--after`). It logs to `logs/run-<time>.csv`, polls T1–T4 (`--no-sensors` to skip) and reads the VFD every 5th poll (`--vfd-every`), then prints the missed replies per device. Exit status 2 on a fault.

**Conveyor oven test** (done 2026-11-04; the lid had to be opened to reach 60 °C before the idle shutdown): belt clear, lid closed. `heat --sv 100 --zones all --minutes 10 --belt-cool 60`. Check: the belt starts before the contactor clunks in, the `belt` column shows the output frequency, the belt keeps running after heat-off and stops by itself at "CONVEYOR STOP". Optional fault test: the drive's STOP/RUN switch does nothing in REMOTE mode, so it cannot stop the belt. To test the VFD comms-loss path instead: during the hold, unplug the RS-485 RJ-11 from the drive. Expect "comms loss: VFD" after 3 polls (about 7 s), heat-off, and the belt still running (the drive keeps its last command). Plug it back in; the cool-down then stops the belt as usual.

**The reliability run** (done 2026-11-04): `heat --sv 100 --zones all --minutes 30`. Keep the lid closed and the belt clear. Record the "Missed replies" table: the `missed` column is single misses (retried), `failed` is polls where both tries failed, and `max run` is the longest run of failed polls. These numbers size the comms-loss rule. If a comms-loss fault stops the run early, that is a result too: note it and rerun with a larger `--fail-polls` to finish the measurement.
