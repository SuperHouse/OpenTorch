# Torch R350 reflow oven: problems with the vendor control software

**Software:** "TN Series Reflow Control Systems" v1.1.5.7 (Beijing Torch Co., Ltd), running on a Windows 7 32-bit PC.
**Prepared:** 2026-10-28, from bench testing and recorded bus traffic.
**Purpose:** to record the problems found with the vendor software, as justification for replacing it with an in-house control system.

## Summary

The vendor software controls the oven's 12 heater zones and conveyor from a PC. During testing it showed **two safety faults** and **frequent crashes that need a Windows restart**. In the worst safety fault, the software **switched six heater zones back on after the operator had clicked Stop Heating**. Its over-temperature protection **did nothing** when a zone was held more than 30 °C above its setpoint. The software is copy-protected and encrypted, so it cannot be inspected or fixed. It runs only on an obsolete operating system and is tied to a PC built into another machine.

The oven hardware works correctly. Every heater card and the conveyor drive answered every command they received. The problems are in the PC software and its serial connection.

## How the facts were established

- A logic analyser was connected passively to the oven's RS-485 control bus. It recorded every command the software sent and every reply from the oven, with timestamps, while an operator followed a written test plan. The recordings are in `R350 Capture/` (Sessions 3–10).
- The software's own logs (2014–2026), its database and the vendor's operation manual were also examined.
- Items marked **(inferred)** are likely but not proven.

## Safety concerns

### S1. Heater zones switch back on after Stop Heating (confirmed)

When the operator clicked **Stop Heating** with temperature monitoring ("Inspection") still running, the software sent the heat-off command to all 12 zones, and the stack light turned green ("not heating"). Over the next 45 seconds, the monitoring loop then **sent heat-on commands again to zones 2, 4, 5, 8, 9 and 12**. Each one went out as soon as that zone cooled slightly below its setpoint. No further heat-off command was sent.

- **Effect:** the oven showed a green "stopped" light while half of its heaters were still regulating at 150 °C. The operator saw those zones keep heating. They stopped only when power was removed from the heater cards with the oven's own Stop button.
- **Evidence:** Session 10, from about 1694 s (heat-off to all zones) to 1738 s (the last heat-on).
- **Risk:** an operator who trusts the green light or the software's "stopped" state could leave the oven heating unattended, open it while hot, or start maintenance on it.

### S2. Over-temperature protection did not act (confirmed)

The software has an over-temperature feature: a 6 °C alarm margin, an "Alarm Area" zone, and yellow/red screen states. As a test, zone 3's setpoint was lowered to 120 °C while the zone was at 152–154 °C, and held there for about 3 minutes. The software **sent no cut-off, wrote nothing to the alarm output, and the stack light did not change.**

- **Evidence:** Session 10, from about 1496 s to 1694 s.
- **Risk:** it is not known whether any over-temperature condition triggers a cut-off. The PC-side protection cannot be relied on.

### S3. Heaters keep running if the software stops or crashes (confirmed, with S4)

Each heater card runs its own temperature loop. Once told to heat, it keeps heating until it is told to stop or its power is removed. In Session 10 the six re-enabled zones kept heating for about 2 minutes with no further commands from the PC. No card communication timeout was observed. The software crashes often (see R1–R3), and a crash leaves the heaters running with nothing supervising them.

### S4. The conveyor keeps running if the software stops (from the drive manual)

The conveyor drive (Delta VFD-L) is set to its factory default of "keep running on communication loss", with its communication watchdog disabled. If the PC software crashes, the conveyor keeps its last speed. In the speed-change crash (R2), the drive had received a run command but not the new speed.

### S5. "Delayed shutdown" can turn off the PC

The software's delayed-shutdown feature calls the Windows shutdown function. That PC is built into the pick-and-place machine and also runs it, so using this feature could shut down the pick-and-place machine's controller mid-job **(inferred from the software's code and UI; not tested)**.

### S6. Safe operation depends on operator order and inconsistent interlocks

- Setpoints cannot be changed while heating or monitoring is running.
- Stop Heating is sometimes disabled until monitoring is stopped, and sometimes it is not (it was allowed in S1).
- The safe order to stop the oven (Stop Inspection first, then Stop Heating) is not stated anywhere. The order the UI allows leads to S1.

## Reliability problems

### R1. Using two parts of the software together crashes it

If the operator opens the serial port on the main console and then clicks **Online** in the card parameter window (or does it the other way round), the software reports that the port is in use, throws an unhandled exception and crashes. **Windows must be restarted** to recover. So parameter work and normal operation have to be done in separate software sessions.

### R2. Changing the conveyor speed crashes the software

Changing the conveyor speed repeatedly produced the Windows error "A device attached to the system is not functioning", then an unhandled exception and a crash, which again needed a **Windows restart**. The recording (Session 9) shows the failure happens inside the PC, before any command reaches the oven. Likely causes are the software's practice of reconfiguring the serial port between commands, combined with the PC's USB-serial adapter driver **(inferred)**.

### R3. Every crash needs a Windows restart

After a crash the serial port stays locked, and the software cannot reopen it until Windows restarts. On a PC shared with the pick-and-place machine, that interrupts the other machine too.

### R4. Confusing and error-prone user interface

- The large ON/OFF "switches" on each zone are only indicators and cannot be clicked. The small checkbox next to them actually switches the zone.
- The temperature-profile screen throws errors such as "mismatch in number of colours and channels" unless several separate settings are made to agree.
- The conveyor and monitoring buttons have undocumented dependencies (for example, Start Inspection stays disabled until Start Transport has been clicked).

## Maintainability and support problems

| Problem | Detail |
|---|---|
| Obsolete platform | Needs 32-bit Windows (it uses the 32-bit-only Jet 4.0 database engine and .NET Framework 2.0). It will not run unmodified on 64-bit Windows 10/11. Windows 7 has been unsupported since 2020. |
| Cannot be inspected or fixed | The program code is encrypted and locked to a hardware dongle (Sentinel HASP). Without the vendor, nobody can correct S1, S2 or the crashes. |
| Dongle dependency | Losing or damaging the dongle stops the oven from being used. |
| Tied to another machine | It is installed on the PC built into the pick-and-place machine, so the oven cannot be relocated without migrating the software (see the platform problem above). |
| Data handling | User passwords are stored in plain text in the database. The working database copy found in the install folder was corrupt. |
| Undocumented behaviour | The manual gives no communication protocol, no stop procedure, and no description of the over-temperature logic. |

## What a replacement must do

These requirements follow directly from the problems above:

1. **A stop must be final.** Nothing may re-enable a heater after a stop until the operator explicitly starts again (S1).
2. **Its own over-temperature cut-off**, tested and independent of the operator interface (S2).
3. **Fail safe:** a watchdog that turns all heaters off if the controller stops communicating, plus a defined conveyor behaviour on fault (S3, S4).
4. **One owner of the serial port**, with no crashes on port errors and recovery without a reboot (R1–R3).
5. **Reliable baud-rate switching** between the heater cards (2400 baud) and the conveyor drive (9600 baud, 7-bit), tested on the chosen serial adapter (R2).
6. **A modern, supportable platform** (a current OS or a Raspberry Pi) with source code the team controls, and no dongle.
7. **A clear operator interface** whose controls do what they appear to do (R4, S6).

The communication protocol needed to build it has been worked out from the recordings and is documented in `CLAUDE.md`.

## Evidence index

| Recording | Content |
|---|---|
| `R350 Capture/Session 3.csv` | Conveyor start; cards unpowered (oven Start not pressed) |
| `R350 Capture/Session 5.csv` | Normal temperature polling, all 12 zones |
| `R350 Capture/Session 8.csv` | Card parameter reads and address scan |
| `R350 Capture/Session 9.csv` | Two of the conveyor-speed crashes (R2) |
| `R350 Capture/Session 10.csv` | Heating run, over-temperature test (S2), re-enable after stop (S1) |

Decoded with `tools/decode_capture.py`. Times are seconds from the start of each recording.
