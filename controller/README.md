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
2. Connect the USB-RS485 adapter: A, B and GND to the HEXIN converter's 3-way terminal (where the Saleae tap goes). Leave the Saleae tap connected if you want a capture of the probe's traffic.
3. Oven: mains on, E-stop released, **press the green Start** (the cards are unpowered until then).
4. Find the port: `python3 probe.py --list-ports`. Look for the FTDI entry (`/dev/cu.usbserial-…`).
5. Snapshot: `python3 probe.py --port /dev/cu.usbserial-XXXX`
   - It first checks whether the adapter echoes its own bytes, then reads all 16 cards, the zone parameters and the VFD status.
   - If every device shows "no reply", swap A and B.
6. Reliability run (Phase 0 "done" test): `python3 probe.py --port /dev/cu.usbserial-XXXX --monitor 600 --log probe-run.csv`
   - Done when all devices read over 10 minutes with zero bad frames, and the VFD reads (every 2nd round) cause no errors on the card side.

The probe never writes. The `Bus` refuses writes unless created with `read_only=False`, which only Phase 1 code will do.
