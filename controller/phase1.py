#!/usr/bin/env python3
"""Phase 1: controlled writes to the R350 cards and the conveyor VFD.

  phase1.py --port P status                 zone PV/OUT/SV/AL2 (read only)
  phase1.py --port P off [all | N ...]      heat off (0xCC) to zones; default all
  phase1.py --port P heat N --sv 60 --minutes 5
                                            heat one zone to a low SV, then heat off
  phase1.py --port P lamp on|off            AL2 on zone 1 (stack light orange "heating")
  phase1.py --port P watch [--minutes 60]   read only: log all zones until the bus goes silent
  phase1.py --port P vfd status | stop | run --hz 5
  phase1.py --fake ...                      the same against the simulated oven

Heat test safety:
  - The SV is capped at MAX_TEST_SV. It is written and read back before any
    heat-on, so if this script or the Mac dies, the card still only heats to
    that low SV (the cards regulate on their own).
  - All 12 zones get heat-off first, so only the zone under test heats.
  - Heat-on is sent once. Nothing re-sends it (a stop is final).
  - Every exit (time up, abort, Ctrl-C, closing the terminal, an error) sends
    heat-off to zone 1 (drops the heater contactor), then the zone under test,
    then every other zone.
  - Abort if the zone under test overshoots, any zone gets hot, or the zone
    under test stops answering.
  - The test SV is left on the card afterwards; the original is printed.
    Restoring it would raise the SV of a card whose heat-off might have been lost.

Before running: oven mains on, E-stop released, green Start pressed, vendor PC
disconnected. Know where the red Stop / E-stop is.
"""

import argparse
import csv
import signal
import sys
import time
from datetime import datetime
from pathlib import Path

from oven import protocol as pr
from oven.bus import Bus, BusError

MAX_TEST_SV = 100        # °C, hard cap on the heat-test setpoint
OVERSHOOT_ABORT = 15     # °C above the test SV on the zone under test
HOT_ABORT = MAX_TEST_SV + 20   # °C on any zone
NO_REPLY_ABORT = 3       # consecutive failed reads of the zone under test
MAX_HZ = 15.0            # conveyor test limit
AL2_HEAT = 400
ENABLE_SV = 5            # °C, zone 1 SV when run only as the heating enable

LOG_DIR = Path(__file__).parent / "logs"


class Abort(Exception):
    pass


def retry(fn, *args, tries=3):
    for k in range(tries):
        try:
            return fn(*args)
        except BusError:
            if k == tries - 1:
                raise


def zone_addr(n):
    if not 1 <= n <= len(pr.ZONE_ADDRS):
        raise ValueError(f"zone must be 1-{len(pr.ZONE_ADDRS)}, not {n}")
    return pr.ZONE_ADDRS[n - 1]


def read_zones(bus):
    """{addr: (pv, out, sv) or None}"""
    res = {}
    for addr in pr.ZONE_ADDRS:
        try:
            res[addr] = tuple(bus.read_registers(addr, pr.PV, 3))
        except BusError:
            res[addr] = None
    return res


def heat_off(bus, addrs, tries=5):
    """Send 0xCC to each address. Returns the addresses that never acked.
    Ctrl-C is ignored meanwhile, so a second press cannot cut it short."""
    failed = []
    old = signal.signal(signal.SIGINT, signal.SIG_IGN)
    try:
        for addr in addrs:
            try:
                retry(bus.write_register, addr, pr.CMD, pr.CMD_HEAT_OFF, tries=tries)
            except BusError:
                failed.append(addr)
    finally:
        signal.signal(signal.SIGINT, old)
    return failed


class Log:
    def __init__(self, path):
        self.f = open(path, "w", newline="") if path else None
        self.w = csv.writer(self.f) if path else None
        if self.w:
            self.w.writerow(["time", "elapsed_s", "state", "device", "pv", "out", "sv", "note"])
        self.t0 = time.monotonic()

    def _row(self, state, device, vals, note=""):
        if self.w:
            self.w.writerow([datetime.now().strftime("%H:%M:%S.%f")[:-3],
                             f"{time.monotonic() - self.t0:.1f}", state, device,
                             *(vals or ("", "", "")), note])
            self.f.flush()

    def event(self, text, out=print):
        out(f"{datetime.now():%H:%M:%S}  ** {text}")
        self._row("event", "", None, text)

    def readings(self, state, zones):
        for addr, r in zones.items():
            self._row(state, pr.device_name(addr), r, "" if r else "no reply")

    def close(self):
        if self.f:
            self.f.close()


def print_header(target, out=print):
    out("time      t(s)  state  " + " ".join(
        f"{('*' if a == target else '') + pr.device_name(a):>5}" for a in pr.ZONE_ADDRS)
        + "   PID%")   # 0x1002 of the zone under test: calculated, not proof of drive


def watch(bus, log, state, target, sv, seconds, period, out=print, checks=True):
    """Poll all zones for `seconds`. Returns None, or the reason for an abort.
    Without checks, it stops early when no zone answers (the oven has left
    the started state) and logs the time of the last reply."""
    end = time.monotonic() + seconds
    misses = silent = 0
    last_reply = None
    while time.monotonic() < end:
        t = time.monotonic()
        zones = read_zones(bus)
        log.readings(state, zones)
        r = zones[target]
        out(f"{datetime.now():%H:%M:%S} {time.monotonic() - log.t0:5.0f}  {state:<5}  "
            + " ".join(f"{z[0] if z else '--':>5}" for z in zones.values())
            + f"   {r[1] if r else '--':>4}")
        if any(zones.values()):
            last_reply, silent = datetime.now(), 0
        else:
            silent += 1
        if not checks and silent >= NO_REPLY_ABORT:
            when = f"{last_reply:%H:%M:%S}" if last_reply else "none in this phase"
            log.event(f"bus silent: oven no longer started (last reply {when})", out)
            return "bus silent"
        if checks:
            misses = 0 if r else misses + 1
            if misses >= NO_REPLY_ABORT:
                return f"{pr.device_name(target)} stopped answering"
            if r and r[0] >= sv + OVERSHOOT_ABORT:
                return f"{pr.device_name(target)} PV {r[0]} is {OVERSHOOT_ABORT}+ °C over SV {sv}"
            hot = [pr.device_name(a) for a, z in zones.items() if z and z[0] >= HOT_ABORT]
            if hot:
                return f"zone(s) {', '.join(hot)} at or above {HOT_ABORT} °C"
        time.sleep(max(0.0, period - (time.monotonic() - t)))
    return None


def heat_test(bus, zone, sv, seconds, after=60, log=None, period=2.0, out=print,
              enable_z1=False, cutoff_after=None):
    """Heat one zone to a low SV for `seconds`, then heat off and watch for
    `after` seconds. Returns the abort reason or None.

    enable_z1: also run zone 1 (the vendor's "Heating Area" card) with AL2 = 400
    and its SV below room temperature, so its AL2 output is active but it does
    not heat. Zone 1's AL2 output switches the heater contactor (confirmed
    2026-11-03), so without it no zone but zone 1 heats.

    cutoff_after: master cut-off test. After this many seconds, send heat-off
    to zone 1 only and keep the zone under test commanded on for the rest of
    `seconds`. If zone 1 is a master cut-off, the contactor drops and the zone
    under test stops heating although its card is still running."""
    target = zone_addr(zone)
    if not 0 < sv <= MAX_TEST_SV:
        raise ValueError(f"test SV must be 1-{MAX_TEST_SV} °C, not {sv}")
    z1 = pr.ZONE_ADDRS[0]
    enable_z1 = enable_z1 and target != z1
    if cutoff_after is not None and not (enable_z1 and 0 < cutoff_after < seconds):
        raise ValueError("cut-off test needs a zone other than 1, zone 1 enabled, "
                         "and 0 < cutoff-after < test time")
    log = log or Log(None)

    before = read_zones(bus)
    log.readings("before", before)
    if before[target] is None:
        raise Abort(f"{pr.device_name(target)} does not answer")
    pv0, _, sv0 = before[target]
    if pv0 >= sv:
        raise Abort(f"{pr.device_name(target)} PV {pv0} is already at or above the test SV {sv}")
    hot = [pr.device_name(a) for a, z in before.items() if z and z[0] >= HOT_ABORT]
    if hot:
        raise Abort(f"zone(s) {', '.join(hot)} already at or above {HOT_ABORT} °C")
    if enable_z1 and (before[z1] is None or before[z1][0] < ENABLE_SV + 3):
        raise Abort(f"Z1 must answer and read above {ENABLE_SV + 3} °C to act as the enable")

    failed = heat_off(bus, pr.ZONE_ADDRS)
    if failed:
        raise Abort("no ack to heat-off from " + ", ".join(map(pr.device_name, failed)))
    log.event("heat-off sent to all zones", out)

    retry(bus.write_register, target, pr.SV, sv)
    got = retry(bus.read_registers, target, pr.SV, 1)[0]
    if got != sv:
        raise Abort(f"SV read back as {got}, not {sv}; heat-on not sent")
    log.event(f"{pr.device_name(target)} SV {sv0} -> {sv} (read back OK). "
              f"Original SV was {sv0}", out)
    if enable_z1:
        for reg, value in ((pr.SV, ENABLE_SV), (pr.AL2, AL2_HEAT)):
            retry(bus.write_register, z1, reg, value)
            got = retry(bus.read_registers, z1, reg, 1)[0]
            if got != value:
                raise Abort(f"Z1 register {reg:04X} read back as {got}, not {value}")
        log.event(f"Z1 SV {before[z1][2]} -> {ENABLE_SV}, AL2 = {AL2_HEAT} (read back OK)", out)

    reason = None
    print_header(target, out)
    try:
        if enable_z1:
            retry(bus.write_register, z1, pr.CMD, pr.CMD_HEAT_ON)
            log.event(f"Z1 RUN (SV {ENABLE_SV}: heating enable only, should not heat)", out)
        retry(bus.write_register, target, pr.CMD, pr.CMD_HEAT_ON)
        log.event(f"{pr.device_name(target)} HEAT ON", out)
        if cutoff_after is None:
            reason = watch(bus, log, "heat", target, sv, seconds, period, out)
        else:
            reason = watch(bus, log, "heat", target, sv, cutoff_after, period, out)
            if not reason:
                retry(bus.write_register, z1, pr.CMD, pr.CMD_HEAT_OFF, tries=5)
                log.event(f"CUT-OFF TEST: Z1 HEAT OFF only; {pr.device_name(target)} "
                          "still commanded on. Contactor should drop", out)
                reason = watch(bus, log, "cut", target, sv, seconds - cutoff_after,
                               period, out)
        if reason:
            log.event(f"ABORT: {reason}", out)
    finally:
        # zone 1 first: its AL2 output holds the heater contactor in
        order = [z1] + [target] * (target != z1)
        failed = heat_off(bus, order + [a for a in pr.ZONE_ADDRS if a not in order])
        if failed:
            log.event("!!! NO ACK TO HEAT-OFF FROM " + ", ".join(map(pr.device_name, failed))
                      + " !!! PRESS THE OVEN'S RED STOP", out)
        else:
            log.event(f"{pr.device_name(target)} HEAT OFF (acked), all other zones off", out)

    if after > 0:
        try:
            watch(bus, log, "off", target, sv, after, period, out, checks=False)
        except KeyboardInterrupt:
            log.event("after-watch stopped (Ctrl-C); heat was already off", out)
    return reason


def status(bus, out=print):
    # 0x1022 is write-only: the cards do not answer a read of it
    out(f"{'dev':<4} {'addr':>4} {'PV':>5} {'OUT%':>5} {'SV':>5} {'AL2':>5}")
    for addr in pr.ZONE_ADDRS:
        try:
            pv, o, sv = bus.read_registers(addr, pr.PV, 3)
            al2 = bus.read_registers(addr, pr.AL2, 1)[0]
        except BusError as e:
            out(f"{pr.device_name(addr):<4} {addr:>4}  -- {e}")
            continue
        out(f"{pr.device_name(addr):<4} {addr:>4} {pv:>5} {o:>5} {sv:>5} {al2:>5}")


def lamp(bus, on, out=print):
    addr = pr.ZONE_ADDRS[0]
    value = AL2_HEAT if on else 0
    retry(bus.write_register, addr, pr.AL2, value)
    got = retry(bus.read_registers, addr, pr.AL2, 1)[0]
    out(f"Z1 AL2 = {got} (wrote {value}). Stack light should now be "
        f"{'ORANGE' if on else 'GREEN'}.")


def vfd_status(bus, out=print):
    w = retry(bus.vfd_read, pr.VFD_ERROR, 7)
    s = w[1]
    run = ("stopped", "decelerating", "standby", "running")[s & 3]
    out(f"VFD: {run} (status 0x{s:04X}), command {w[2] / 100:.2f} Hz, "
        f"output {w[3] / 100:.2f} Hz, {w[4] / 10:.1f} A, DC bus {w[5]} V, error {w[0]}")
    return w


def vfd_run(bus, hz, out=print):
    if not 0 < hz <= MAX_HZ:
        raise ValueError(f"test speed must be 0-{MAX_HZ} Hz, not {hz}")
    retry(bus.vfd_write, pr.VFD_FREQ, round(hz * 100))
    retry(bus.vfd_write, pr.VFD_CMD, pr.VFD_RUN)
    out(f"VFD: frequency {hz:.2f} Hz and RUN sent (acked)")


def vfd_stop(bus, out=print):
    retry(bus.vfd_write, pr.VFD_CMD, pr.VFD_STOP)
    out("VFD: STOP sent (acked)")


def confirm(text, assume_yes):
    if assume_yes:
        return True
    return input(f"{text}\nType 'yes' to go ahead: ").strip().lower() == "yes"


def _raise_interrupt(signum, frame):
    raise KeyboardInterrupt(f"signal {signum}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--port", help="serial device, e.g. /dev/cu.usbserial-XXXX")
    ap.add_argument("--fake", action="store_true", help="use a simulated oven")
    ap.add_argument("--timeout", type=float, default=0.3, help="reply timeout, s")
    ap.add_argument("--gap", type=float, default=0.05, help="idle time between transactions, s")
    ap.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    p = sub.add_parser("off")
    p.add_argument("zones", nargs="*", default=["all"])
    p = sub.add_parser("heat")
    p.add_argument("zone", type=int)
    p.add_argument("--sv", type=int, default=60, help=f"test SV, °C (max {MAX_TEST_SV})")
    p.add_argument("--minutes", type=float, default=5)
    p.add_argument("--after", type=float, default=60, help="seconds to watch after heat off")
    p.add_argument("--log", help="CSV path (default: logs/heat-zN-<time>.csv)")
    p.add_argument("--cutoff-after", type=float, metavar="SECONDS",
                   help="master cut-off test: after this long, heat-off to zone 1 only")
    p.add_argument("--no-enable-z1", dest="enable_z1", action="store_false",
                   help=f"do not run zone 1 at SV {ENABLE_SV} with AL2 = {AL2_HEAT}; without it "
                        "the heater contactor stays off and only zone 1 can heat")
    p = sub.add_parser("watch")
    p.add_argument("--minutes", type=float, default=60)
    p.add_argument("--log", help="CSV path (default: logs/watch-<time>.csv)")
    p = sub.add_parser("lamp")
    p.add_argument("state", choices=["on", "off"])
    p = sub.add_parser("vfd")
    p.add_argument("action", choices=["status", "run", "stop"])
    p.add_argument("--hz", type=float, default=5.0)
    args = ap.parse_args()

    if args.fake:
        from tests.fake_oven import FakeOven
        factory, port = FakeOven, "fake"
    elif args.port:
        import serial
        factory, port = serial.Serial, args.port
    else:
        ap.error("give --port or --fake")

    signal.signal(signal.SIGTERM, _raise_interrupt)
    signal.signal(signal.SIGHUP, _raise_interrupt)
    bus = Bus(port, read_only=False, timeout=args.timeout, gap=args.gap,
              serial_factory=factory)
    try:
        if bus.detect_echo(pr.ZONE_ADDRS[0])[0]:
            print("(adapter echo detected and handled)")
        if args.cmd == "status":
            status(bus)
        elif args.cmd == "off":
            addrs = (pr.ZONE_ADDRS if args.zones == ["all"]
                     else [zone_addr(int(z)) for z in args.zones])
            failed = heat_off(bus, addrs)
            for a in addrs:
                print(f"{pr.device_name(a)}: {'NO ACK' if a in failed else 'heat off, acked'}")
        elif args.cmd == "heat":
            if not confirm(f"Heat zone {args.zone} to {args.sv} °C for {args.minutes} min "
                           f"(all other zones off).", args.yes):
                return
            LOG_DIR.mkdir(exist_ok=True)
            path = args.log or LOG_DIR / f"heat-z{args.zone}-{datetime.now():%Y%m%d-%H%M}.csv"
            log = Log(path)
            try:
                reason = heat_test(bus, args.zone, args.sv, args.minutes * 60, args.after, log,
                                   enable_z1=args.enable_z1, cutoff_after=args.cutoff_after)
            except KeyboardInterrupt:
                print("\nstopped; heat-off was sent (see above)", file=sys.stderr)
                reason = "interrupted"
            finally:
                log.close()
            print(f"\n{'ABORTED: ' + reason if reason else 'Done.'}  Log: {path}")
        elif args.cmd == "watch":
            LOG_DIR.mkdir(exist_ok=True)
            path = args.log or LOG_DIR / f"watch-{datetime.now():%Y%m%d-%H%M}.csv"
            log = Log(path)
            try:
                print_header(None)
                watch(bus, log, "watch", pr.ZONE_ADDRS[0], 0, args.minutes * 60, 2.0,
                      checks=False)
            finally:
                log.close()
            print(f"Log: {path}")
        elif args.cmd == "lamp":
            lamp(bus, args.state == "on")
        elif args.cmd == "vfd":
            if args.action == "run":
                if not confirm(f"Run the conveyor at {args.hz} Hz. Is the belt clear?", args.yes):
                    return
                vfd_run(bus, args.hz)
            elif args.action == "stop":
                vfd_stop(bus)
            vfd_status(bus)
    except (Abort, ValueError) as e:
        sys.exit(f"Refused: {e}")
    except BusError as e:
        sys.exit(f"Bus error: {e}")
    except KeyboardInterrupt:
        print("\nstopped", file=sys.stderr)
    finally:
        bus.close()


if __name__ == "__main__":
    main()
