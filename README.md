# OPENTORCH

An Open Source replacement control system for the **Torch R350** reflow oven.

The R350 is typically controlled from a PC by the vendor's software which is copy-protected, runs only on old 32-bit Windows, crashes often, and has safety faults: it can switch heaters back on after the operator clicks the "Stop Heating" button, and its over-temperature cut-off did not work in testing. This project works out how the oven is controlled and builds a replacement with a Python controller running on a Raspberry Pi with a touchscreen at the oven.

## Status

- **Protocol: documented.** Determined from the vendor's logs and live RS-485 bus captures. 12 zone cards and 4 profile-thermocouple cards use Modbus RTU at 2400 baud with some non-standard quirks. The conveyor drive (Delta VFD-L) uses Modbus ASCII at 9600 7N2. All of these share one bus, which requires dynamic speed changes on the bus to address different modules and relies on checksum failures to prevent misdirected messages. Ideally this would be split into two buses, but the current plan is to retain the original oven hardware unchanged and work within its limitations.
- **Replacement: Phase 0.** A read-only probe tool is written and tested against a simulated oven. Next step is to run it on the real oven.

## Documents

| File | Contents |
|---|---|
| [CLAUDE.md](CLAUDE.md) | The main technical notes: hardware survey, protocol and register map, capture results, open questions. Start here for detail. |
| [replacement-plan.md](replacement-plan.md) | Architecture, safety requirements, phases, and hardware for the replacement controller. |
| [vendor-software-issues.md](vendor-software-issues.md) | Problems found with the vendor software, with evidence. |
| [capture-checklist.md](capture-checklist.md) | How to record bus traffic with a logic analyser while the vendor software runs. |
| [controller/README.md](controller/README.md) | Setting up and running the controller code. |

## Layout

```
controller/         Replacement controller (Python): oven/ package, probe.py, tests/
tools/              decode_capture.py: decodes Saleae Logic 2 captures of the bus
R350 Capture/       Logic analyser captures of the vendor software talking to the oven
R350 Screenshots/   Screenshots of the vendor software
Resources/          Manuals, datasheets and photos of the oven internals
```

## Quick start

```sh
cd controller
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python3 -m unittest
python3 probe.py --fake        # simulated oven
```

## Safety

This software drives a 7.8kW oven on 3-phase mains. Only one master may be on the oven's bus at a time: disconnect the vendor PC before connecting a replacement controller. The heater cards keep heating at their last command if the controller stops, so never leave the oven running unattended.

**Know where the e-stop is at all times!**