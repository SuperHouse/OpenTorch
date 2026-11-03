# R350 controller

Replacement controller for the Torch R350 reflow oven. Plan: `../replacement-plan.md`. Protocol: `../CLAUDE.md`.

Current stage: **Phase 0, read-only probe.**

## Layout

| Path | Purpose |
|---|---|
| `oven/protocol.py` | Frame building and parsing: card RTU (incl. the 6-byte write ack), VFD ASCII, register constants. No I/O. |
| `oven/bus.py` | `Bus`: the only code that touches the serial port. Switches 2400 8N1 (cards) ↔ 9600 7N2 (VFD) per exchange. **Read-only unless created with `read_only=False`.** |
| `probe.py` | Phase 0 script: snapshot or timed monitor of all cards and the VFD. Read-only. |
| `tests/` | Unit tests. `fake_oven.py` simulates the bus (also used by `probe.py --fake`). |

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
3. `python3 phase1.py --port P heat N --sv 60 --minutes 5`: one zone at a time. Prints all zone PVs every 2 s and logs to `logs/heat-zN-<time>.csv`. The neighbouring zones' PVs rising shows which zones are physically next to each other. Look at the chamber to see which column heats. By default it also runs zone 1 at SV 5 with AL2 = 400: zone 1's AL2 output switches the heater contactor, so no other zone can heat without it (`--no-enable-z1` to leave zone 1 off).
4. `python3 phase1.py --port P watch`: read only. Logs all zones every 2 s and stops when the bus goes silent (the oven has left the started state), printing the time of the last reply. Use it to time drop-outs without watching the oven.
5. `python3 phase1.py --port P lamp on` / `lamp off`: AL2 on zone 1; the stack light should go orange / green.
6. `python3 phase1.py --port P vfd run --hz 5`, then `vfd stop`: the belt must be clear. Speed is capped at 15 Hz.

Heat-test safety (details in the `phase1.py` docstring): the SV is capped at 100 °C and read back before heat-on, so the card itself limits the heat even if the Mac dies. All other zones get heat-off first. Every exit, including Ctrl-C and closing the terminal, sends heat-off to all zones. It aborts on overshoot (SV + 15 °C), on any zone at 120 °C, or when the zone under test stops answering. The test SV stays on the card afterwards.

If heat-off ever reports NO ACK: press the oven's red Stop.
