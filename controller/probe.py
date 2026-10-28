#!/usr/bin/env python3
"""Phase 0 probe: read-only check of the R350 bus from our own host.

Reads every zone card, profile sensor card and the conveyor VFD. It never
writes: the Bus is opened read-only, so any write raises an error.

  probe.py --list-ports
  probe.py --port /dev/cu.usbserial-XXXX              one snapshot
  probe.py --port /dev/cu.usbserial-XXXX --monitor 600 --log run.csv
  probe.py --fake                                     dry run, simulated oven

Before running: oven mains on, E-stop released, green Start pressed (the cards
are unpowered until then), and the vendor PC disconnected from the bus.
"""

import argparse
import csv
import sys
import time
from datetime import datetime

from serial.tools import list_ports

from oven import protocol as pr
from oven.bus import Bus, BusError

DEVICES = pr.ZONE_ADDRS + pr.SENSOR_ADDRS


def list_serial_ports():
    for p in list_ports.comports():
        chip = {0x0403: "FTDI", 0x067B: "Prolific", 0x1A86: "CH340/WCH",
                0x10C4: "CP210x"}.get(p.vid, "")
        ids = f"{p.vid:04X}:{p.pid:04X}" if p.vid else "-"
        print(f"{p.device:<36} {ids:<10} {chip:<10} {p.description}")


def try_read(fn, *args):
    try:
        return fn(*args), None
    except BusError as e:
        return None, str(e)


def snapshot(bus):
    echo, raw = bus.detect_echo(pr.ZONE_ADDRS[0])
    print(f"Adapter echo: {'yes' if echo else 'no'}   "
          f"(test read of Z1 returned {len(raw)} bytes: {raw.hex(' ') or 'nothing'})\n")

    print("Status (03 1000 x3)")
    print(f"  {'dev':<4} {'addr':>4} {'PV':>5} {'OUT%':>5} {'SV':>5}")
    for addr in DEVICES:
        w, err = try_read(bus.read_registers, addr, pr.PV, 3)
        if w:
            print(f"  {pr.device_name(addr):<4} {addr:>4} {w[0]:>5} {w[1]:>5} {w[2]:>5}")
        else:
            print(f"  {pr.device_name(addr):<4} {addr:>4}  -- {err}")

    names = list(pr.CARD_PARAMS)
    print("\nZone parameters (each read as 03 <reg> x2, like the vendor software)")
    print("  dev  " + " ".join(f"{n:>6}" for n in names))
    for addr in pr.ZONE_ADDRS:
        vals = []
        for reg in pr.CARD_PARAMS.values():
            w, err = try_read(bus.read_registers, addr, reg, 2)
            vals.append(f"{w[0]:>6}" if w else f"{'ERR':>6}")
        print(f"  {pr.device_name(addr):<4} " + " ".join(vals))

    print("\nVFD status (ASCII slave 1, 03 2100 x7)")
    w, err = try_read(bus.vfd_read, pr.VFD_ERROR, 7)
    if w:
        for name, v in zip(pr.VFD_STATUS_NAMES, w):
            print(f"  {name:<7} {v:>6}  (0x{v:04X})")
        print(f"  -> output {w[3] / 100:.2f} Hz, command {w[2] / 100:.2f} Hz, error code {w[0]}")
    else:
        print(f"  -- {err}")
    print(f"\nPort back in {bus.mode} mode (2400 8N1)")


def monitor(bus, seconds, vfd_every, log_path):
    bus.detect_echo(pr.ZONE_ADDRS[0])
    log = csv.writer(open(log_path, "w", newline="")) if log_path else None
    if log:
        log.writerow(["time", "device", "addr", "pv", "out", "sv", "error"])
    fails = {a: 0 for a in DEVICES}
    vfd_fails = rounds = 0
    end = time.monotonic() + seconds
    print("time     " + " ".join(f"{pr.device_name(a):>4}" for a in DEVICES) + "   VFD Hz")
    while time.monotonic() < end:
        rounds += 1
        row = []
        for addr in DEVICES:
            w, err = try_read(bus.read_registers, addr, pr.PV, 3)
            now = datetime.now().strftime("%H:%M:%S.%f")[:-3]
            if w:
                row.append(f"{w[0]:>4}")
            else:
                fails[addr] += 1
                row.append(f"{'--':>4}")
            if log:
                log.writerow([now, pr.device_name(addr), addr, *(w or ["", "", ""]), err or ""])
        vfd = ""
        if vfd_every and rounds % vfd_every == 0:
            w, err = try_read(bus.vfd_read, pr.VFD_ERROR, 7)
            if w:
                vfd = f"{w[3] / 100:6.2f}"
            else:
                vfd_fails += 1
                vfd = "    --"
            if log:
                log.writerow([datetime.now().strftime("%H:%M:%S.%f")[:-3], "VFD", pr.VFD_SLAVE,
                              *(w[3:4] if w else [""]), "", "", err or ""])
        print(f"{datetime.now():%H:%M:%S} " + " ".join(row) + f"   {vfd}", flush=True)

    lat = bus.latency_ms
    print(f"\n{rounds} rounds, {bus.stats['requests']} requests, {bus.stats['ok']} ok, "
          f"{bus.stats['no_reply']} no reply, {bus.stats['bad_frame']} bad frames")
    if lat:
        print(f"latency ms min/avg/max {min(lat):.1f}/{sum(lat) / len(lat):.1f}/{max(lat):.1f}")
    bad = {pr.device_name(a): n for a, n in fails.items() if n}
    print("failures per device:", bad or "none", f" VFD: {vfd_fails}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--port", help="serial device, e.g. /dev/cu.usbserial-XXXX")
    ap.add_argument("--list-ports", action="store_true")
    ap.add_argument("--fake", action="store_true", help="use a simulated oven")
    ap.add_argument("--monitor", type=float, metavar="SECONDS",
                    help="poll all devices for this long instead of one snapshot")
    ap.add_argument("--vfd-every", type=int, default=2,
                    help="in monitor mode, read the VFD every N rounds (0 = never)")
    ap.add_argument("--log", help="monitor mode: write every reading to this CSV")
    ap.add_argument("--gap", type=float, default=0.05,
                    help="minimum idle time between transactions, s (vendor: about 0.4)")
    ap.add_argument("--timeout", type=float, default=0.3, help="reply timeout, s")
    args = ap.parse_args()

    if args.list_ports:
        list_serial_ports()
        return
    if args.fake:
        from tests.fake_oven import FakeOven
        factory, port = FakeOven, "fake"
    elif args.port:
        import serial
        factory, port = serial.Serial, args.port
    else:
        ap.error("give --port (see --list-ports) or --fake")

    bus = Bus(port, read_only=True, timeout=args.timeout, gap=args.gap,
              serial_factory=factory)
    try:
        if args.monitor:
            monitor(bus, args.monitor, args.vfd_every, args.log)
        else:
            snapshot(bus)
    except KeyboardInterrupt:
        print("\nstopped", file=sys.stderr)
    finally:
        bus.close()


if __name__ == "__main__":
    main()
