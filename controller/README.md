# R350 controller

Replacement controller for the Torch R350 reflow oven. Plan: `../replacement-plan.md`. Protocol: `../CLAUDE.md`.

Current stage: **Phase 1 done except a reliability run with heating on** (2026-11-03). The Phase 2/3 device and supervisor layers and `ovenctl.py` are written and tested on the simulator, not yet on the oven. Next: the reliability run with `ovenctl.py heat` (below).

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
- SV is capped at 150 °C for now.

Commands:

1. `python3 ovenctl.py --port P status`: read only. Zones, T1–T4, Z1 AL2 and the VFD.
2. `python3 ovenctl.py --port P off`: heat-off to all zones, zone 1 first, then Z1 AL2 = 0.
3. `python3 ovenctl.py --port P heat --sv 100 --zones all --minutes 30`: heats, holds, stops, then watches for 2 minutes (`--after`). It logs to `logs/run-<time>.csv`, polls T1–T4 (`--no-sensors` to skip) and reads the VFD every 5th poll (`--vfd-every`), then prints the missed replies per device. Exit status 2 on a fault.

**The reliability run** (the last Phase 1 item): `heat --sv 100 --zones all --minutes 30`. Keep the lid closed and the belt clear. Record the "Missed replies" table: the `missed` column is single misses (retried), `failed` is polls where both tries failed, and `max run` is the longest run of failed polls. These numbers size the comms-loss rule. If a comms-loss fault stops the run early, that is a result too: note it and rerun with a larger `--fail-polls` to finish the measurement.
