# Raspberry Pi comms check

**Purpose:** confirm that a Raspberry Pi can run the controller code and talk to the oven reliably through the same chain as the Mac: FTDI USB-RS232 adapter → oven DE9 → HEXIN RS-232/RS-485 converter → cards and VFD. Only the host changes, so this is a check of the Linux side: the FTDI driver, the 2400 8N1 ↔ 9600 7N2 switching, and the port set-up.

**Time:** about 1 hour, including a 10-minute unattended run.

**This is a one-off check.** Development stays on the Macs until Phase 4 (see `replacement-plan.md`). Nothing here changes the oven or its wiring.

## What you need

- A Raspberry Pi 4 or 5 with Raspberry Pi OS (Bookworm or later, 64-bit), network access, and a keyboard/screen or SSH.
- The FTDI FT232R USB-RS232 adapter (serial `BH0109DI`) and its cable to the oven's DE9.
- Access to the project's git remote from the Pi. Dropbox has no Pi client, so the code goes over by git.

## Part 1: set up the Pi (no oven needed)

1. Install the tools:
   ```sh
   sudo apt update
   sudo apt install -y git python3-venv
   python3 --version        # 3.11 or later
   ```
2. Clone the repository and go to the controller folder:
   ```sh
   git clone <your remote URL> opentorch
   cd opentorch/controller
   ```
3. Create the venv and install the one dependency (pyserial):
   ```sh
   python3 -m venv .venv
   . .venv/bin/activate
   pip install -r requirements.txt
   ```
4. Run the unit tests. **Expected:** every test passes (`OK`).
   ```sh
   python3 -m unittest
   ```
5. Dry runs against the simulated oven. **Expected:** a table of 16 devices and a VFD status, then a short heat test ending with `Done.`
   ```sh
   python3 probe.py --fake
   python3 phase1.py --fake --yes heat 2 --minutes 0.1 --after 0 --log /tmp/fake-heat.csv
   ```
   (The `--log` keeps this simulated run out of `logs/`, which holds only real oven runs.)
6. Give your user access to serial ports, then log out and back in (or reboot):
   ```sh
   sudo usermod -aG dialout $USER
   ```
   After logging in again, check: `groups` should list `dialout`.

## Part 2: check the adapter on the Pi (no oven needed)

1. Plug the FTDI adapter into the Pi.
2. Check that Linux sees it:
   ```sh
   lsusb | grep -i 0403:6001        # expect: Future Technology Devices ... FT232 ...
   sudo dmesg | grep -i ftdi | tail  # expect: "FTDI USB Serial Device converter now attached to ttyUSB0"
   ls -l /dev/serial/by-id/          # expect: usb-FTDI_FT232R_USB_UART_BH0109DI-if00-port0 -> ../../ttyUSB0
   ```
3. **Use the `/dev/serial/by-id/...` path as the port from now on.** It stays the same when other USB devices are plugged in, while `ttyUSB0` can change. Put it in a variable for the rest of this check:
   ```sh
   P=/dev/serial/by-id/usb-FTDI_FT232R_USB_UART_BH0109DI-if00-port0
   ```
4. Check that nothing else has the port open. **Expected:** no output from the first command; the second should say ModemManager is not found or inactive. If ModemManager is running, stop it (`sudo systemctl disable --now ModemManager`), because it can probe serial ports and send its own bytes onto the bus.
   ```sh
   sudo lsof $P
   systemctl status ModemManager
   ```
5. With the venv active, list the ports. **Expected:** a line with `0403:6001  FTDI  FT232R USB UART`.
   ```sh
   python3 probe.py --list-ports
   ```

## Part 3: run on the oven

**Only one master on the bus.** Unplug the vendor PC's cable from the oven's DE9, and do not run anything from a Mac at the same time.

1. Plug the FTDI adapter's DE9 into the oven's DE9 (the same socket the Mac used).
2. Oven: mains on, E-stop released, **press the green Start**. The cards and the VFD have no power until then.
3. Snapshot (read only):
   ```sh
   python3 probe.py --port $P
   ```
   **Expected:** `Adapter echo: no`, all 12 zones and T1–T4 with sensible values (room temperature when cold; an empty thermocouple socket reads about 1360), the zone parameter table, and the VFD status with error code 0.
   **If every device shows "no reply":** check that the Start lamp is lit (press Start again if the oven had been idle for 30 minutes) and that the DE9 is fully seated.
4. Reliability run (read only, 10 minutes, can be left unattended):
   ```sh
   python3 probe.py --port $P --monitor 600 --log logs/pi-probe-run.csv
   ```
   **Pass:** the summary at the end shows **0 no reply, 0 bad frames**, `failures per device: none`, `VFD: 0`. Note the latency line. For comparison, the Mac run on 2026-11-03 gave 230 rounds, 3796 requests, latency 37–85 ms (average 63 ms).
5. **Optional, a short write test.** It heats zone 2 (bottom column B1) to at most 60 °C for 2 minutes, with zone 1 as the heater-contactor enable. Ctrl-C at any time is safe: it sends heat-off to all zones.
   ```sh
   python3 phase1.py --port $P heat 2 --sv 60 --minutes 2 --after 0
   ```
   Type `yes`. **Expected:** stack light orange and the contactor clunks in at `Z1 RUN`, zone 2's PV rises, and at the end `Z2 HEAT OFF (acked), all other zones off`, contactor out, stack light green.
6. **Optional, the conveyor.** Check the belt is clear first.
   ```sh
   python3 phase1.py --port $P vfd run --hz 5
   python3 phase1.py --port $P vfd stop
   ```
   **Expected:** both say `(acked)`; the belt moves entry → exit and then stops.

## Part 4: record the result

1. Note down: Pi model, OS version (`cat /etc/os-release | head -2`), Python version, the by-id port name, the summary lines of the reliability run, and the results of steps 5 and 6 if done.
2. Bring the logs back to the project. Either commit them from the Pi:
   ```sh
   git add logs/
   git commit -m "Pi comms check: probe and heat logs"
   git push
   ```
   or copy them to a Mac (`scp` the `controller/logs/` files into the Dropbox folder).
3. Ask Claude to add the result to `CLAUDE.md` ("Our own runs") and `replacement-plan.md`.

**If the reliability run shows errors** that the Mac run did not: keep the log, and note the errors and when they happened. The first things to try are a powered USB hub (in case the Pi's USB supply is weak) and a different USB port.
