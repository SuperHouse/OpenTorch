#!/usr/bin/env python3
"""Command-line control of the R350 through the supervisor (Phase 2/3).

  ovenctl.py --port P status                  zone PV/OUT/SV, sensors, VFD (read only)
  ovenctl.py --port P off                     heat off to every zone, zone 1 first
  ovenctl.py --port P heat --sv 100 [--zones 1-12] [--minutes 30] [--belt-hz 5]
                                              heat zones, hold, stop; report missed replies
  ovenctl.py --fake ...                       the same against the simulated oven

The safety rules are in oven/supervisor.py. Every exit (time up, fault,
Ctrl-C, closing the terminal, an error) sends heat-off to zone 1 first, then
every other zone. The SVs stay on the cards afterwards.

The conveyor runs from before heat-on until every zone is below the cool-down
temperature (--belt-cool); after heat-off the program keeps polling until
then. If it is stopped earlier (Ctrl-C), the belt is left running: the drive
keeps running without comms, and the oven's idle shutdown stops it.

Before running: oven mains on, E-stop released, green Start pressed, vendor PC
disconnected, belt clear. Know where the red Stop / E-stop is.
"""

import argparse
import signal
import sys
import time
import tomllib
from datetime import datetime
from pathlib import Path

from oven import protocol as pr
from oven.bus import Bus, BusError
from oven.runlog import Log
from oven.supervisor import Limits, Refused, State, Supervisor

LOG_DIR = Path(__file__).parent / "logs"
PERIOD = 2.0     # s between poll rounds
BELT_HZ = 5.0    # conveyor frequency while hot (tested 2026-11-03)


def parse_zones(text):
    """'all', '1-12', '2,4,6-8' -> sorted zone numbers."""
    if text == "all":
        return list(range(1, len(pr.ZONE_ADDRS) + 1))
    zones = set()
    for part in text.split(","):
        lo, _, hi = part.partition("-")
        zones.update(range(int(lo), int(hi or lo) + 1))
    return sorted(zones)


def load_profile(path):
    """A profile TOML file -> (name, {zone: SV}, belt Hz or None).
    `setpoints` lists all 12 zones in order; 0 leaves a zone off. The SV
    limits are checked by Supervisor.start()."""
    with open(path, "rb") as f:
        p = tomllib.load(f)
    svs = p.get("setpoints")
    if not isinstance(svs, list) or len(svs) != len(pr.ZONE_ADDRS) \
            or not all(isinstance(sv, int) and sv >= 0 for sv in svs):
        raise ValueError(f"{path}: setpoints must list {len(pr.ZONE_ADDRS)} whole "
                         "numbers, zones 1-12 in order (0 = zone off)")
    hz = p.get("belt_hz")
    if hz is not None and not isinstance(hz, (int, float)):
        raise ValueError(f"{path}: belt_hz must be a number")
    return (p.get("name", Path(path).stem),
            {z: sv for z, sv in enumerate(svs, 1) if sv}, hz)


def print_header(sup, out=print):
    names = [z.name for z in sup.zones] + [s.name for s in sup.sensors]
    out("time      t(s)  state  " + " ".join(f"{n:>5}" for n in names) + "   belt")


def print_row(sup, readings, t0, out=print):
    names = [z.name for z in sup.zones] + [s.name for s in sup.sensors]
    cells = []
    for n in names:
        r = readings.get(n)
        cells.append("--" if r is None else "open" if getattr(r, "open", False) else r.pv)
    v = readings.get(sup.vfd.name, "")
    belt = "--" if v is None else f"{v['Fout']:.2f}" if v else ""
    out(f"{datetime.now():%H:%M:%S} {time.monotonic() - t0:5.0f}  {sup.state.value:<5}  "
        + " ".join(f"{c:>5}" for c in cells) + f"  {belt:>5}")


def run(sup, seconds, until=None, period=PERIOD, out=print):
    """Poll every `period` s for `seconds`, or until `until(sup)` is true."""
    t0 = sup.log.t0
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        t = time.monotonic()
        print_row(sup, sup.step(), t0, out)
        if until and until(sup):
            return
        time.sleep(max(0.0, period - (time.monotonic() - t)))


def heat(sup, setpoints, seconds, after, period=PERIOD, out=print, belt_hz=None):
    """Start, hold for `seconds`, stop, then watch for at least `after`
    seconds and until the belt has been stopped after cool-down.
    Returns the fault, or None."""
    print_header(sup, out)
    sup.step()
    try:
        sup.start(setpoints, belt_hz)
        if sup.state is State.HEATING:
            run(sup, seconds, lambda s: s.state is not State.HEATING, period, out)
    finally:
        sup.stop("end of run" if sup.state is State.HEATING else "exit")
        if sup.belt is not None and sys.exc_info()[0] is not None:
            belt_left_running(sup)
    if after > 0 or sup.belt is not None:
        end = time.monotonic() + after
        silent = []
        full = []        # zone readings of the last poll that every zone answered

        def stop_watch(s):
            silent.append(s.bus_silent)
            zones = [s.last.get(z.name) for z in s.zones]
            if all(zones):
                full[:] = zones
            if len(silent) >= 3 and all(silent[-3:]):
                return True
            return s.belt is None and time.monotonic() >= end
        try:
            run(sup, float("inf"), stop_watch, period, out)
            if len(silent) >= 3 and all(silent[-3:]):
                sup.event("bus silent: oven no longer started")
                if sup.belt is not None:
                    pvs = [r.pv for r in full]
                    sup.event("cool-down ended by the oven's idle shutdown, which stops "
                              "the belt" + (f" (hottest zone {max(pvs)} °C at the last "
                                            "full poll)" if pvs else ""))
        except KeyboardInterrupt:
            sup.event("after-watch stopped (Ctrl-C); heat was already off")
            if sup.belt is not None:
                belt_left_running(sup)
    return sup.fault


def belt_left_running(sup):
    sup.event("CONVEYOR LEFT RUNNING (cool-down not finished). It stops at the oven's "
              "idle shutdown, or stop it with: phase1.py --port P vfd stop")


def status(sup, out=print):
    readings = sup.step()
    out(f"{'dev':<4} {'PV':>5} {'OUT%':>5} {'SV':>5}")
    for z in sup.zones:
        r = readings[z.name]
        out(f"{z.name:<4} " + (f"{r.pv:>5} {r.out:>5} {r.sv:>5}" if r else "   -- no reply"))
    for s in sup.sensors:
        r = readings[s.name]
        out(f"{s.name:<4} " + (f"{r.pv:>5} {r.out:>5}" + ("  open thermocouple" if r.open else "")
                               if r else "   -- no reply"))
    try:
        al2 = sup.z1.read(pr.AL2)
        out(f"Z1 AL2 = {al2} ({'heater contactor enabled' if al2 else 'off'})")
    except BusError:
        out("Z1 AL2: no reply")
    v = readings.get("VFD")
    out(f"VFD: {v['state']} (status 0x{v['status']:04X}), command {v['Fcmd']:.2f} Hz, "
        f"output {v['Fout']:.2f} Hz, DC bus {v['DCbus']} V, error {v['error']}"
        if v else "VFD: no reply")


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
    sub.add_parser("off")
    p = sub.add_parser("heat")
    which = p.add_mutually_exclusive_group(required=True)
    which.add_argument("--sv", type=int,
                       help=f"setpoint for every zone in --zones, °C (max {Limits.max_sv})")
    which.add_argument("--profile", help="TOML file with per-zone setpoints and belt speed "
                                         "(see profiles/)")
    p.add_argument("--zones", default="all",
                   help="with --sv: e.g. all, 1-12, 2,4,6 (default all)")
    p.add_argument("--minutes", type=float, default=30, help="hold time (default 30)")
    p.add_argument("--after", type=float, default=120,
                   help="seconds to watch after heat off, at least (default 120)")
    p.add_argument("--belt-hz", type=float,
                   help=f"conveyor frequency while hot (default: the profile's, else "
                        f"{BELT_HZ}; max {Limits.max_belt_hz})")
    p.add_argument("--no-belt", action="store_true",
                   help=f"leave the conveyor alone (SV up to {Limits.max_sv_no_belt} °C only)")
    p.add_argument("--belt-cool", type=int, default=Limits.belt_cool_pv,
                   help="after heat off, stop the belt once every zone is below this, °C "
                        f"(default {Limits.belt_cool_pv})")
    p.add_argument("--margin", type=int, default=Limits.overtemp_margin,
                   help="over-temperature margin above SV, °C")
    p.add_argument("--fail-polls", type=int, default=Limits.comms_fail_polls,
                   help="failed polls in a row of one zone that count as comms loss")
    p.add_argument("--no-sensors", dest="sensors", action="store_false",
                   help="do not poll the profile sensors T1-T4")
    p.add_argument("--vfd-every", type=int, default=5,
                   help="read the VFD status every N polls, 0 = never (default 5)")
    p.add_argument("--period", type=float, default=PERIOD, help="s between polls")
    p.add_argument("--log", help="CSV path (default: logs/run-<time>.csv)")
    args = ap.parse_args()

    if args.fake:
        from tests.fake_oven import FakeOven

        def factory(port, **kw):
            oven = FakeOven(port, **kw)
            oven.require_contactor, oven.cool_step = True, 1
            return oven
        port = "fake"
    elif args.port:
        import serial
        factory, port = serial.Serial, args.port
    else:
        ap.error("give --port or --fake")

    signal.signal(signal.SIGTERM, _raise_interrupt)
    signal.signal(signal.SIGHUP, _raise_interrupt)
    bus = Bus(port, read_only=False, timeout=args.timeout, gap=args.gap,
              serial_factory=factory)
    log = None
    sup = None
    try:
        if bus.detect_echo(pr.ZONE_ADDRS[0])[0]:
            print("(adapter echo detected and handled)")
        if args.cmd == "status":
            status(Supervisor(bus, sensors=True, vfd_every=1))
        elif args.cmd == "off":
            Supervisor(bus).stop("off command")
        elif args.cmd == "heat":
            if args.profile:
                name, setpoints, profile_hz = load_profile(args.profile)
                what = f"Run profile '{name}': " + ", ".join(
                    f"Z{z} {sv}" for z, sv in setpoints.items()) + " °C,"
            else:
                setpoints = {z: args.sv for z in parse_zones(args.zones)}
                profile_hz = None
                what = f"Heat zone(s) {args.zones} to {args.sv} °C"
            belt_hz = None if args.no_belt else args.belt_hz or profile_hz or BELT_HZ
            belt = ("Conveyor: NOT RUN." if belt_hz is None else
                    f"Conveyor runs at {belt_hz} Hz from before heat-on until every "
                    f"zone is below {args.belt_cool} °C: the belt must be clear.")
            if not confirm(f"{what} and hold for {args.minutes} min"
                           + ("" if 1 in setpoints else " (zone 1 runs as the enable only)")
                           + f".\n{belt}", args.yes):
                return
            LOG_DIR.mkdir(exist_ok=True)
            path = args.log or LOG_DIR / f"run-{datetime.now():%Y%m%d-%H%M}.csv"
            log = Log(path)
            limits = Limits(overtemp_margin=args.margin, comms_fail_polls=args.fail_polls,
                            belt_cool_pv=args.belt_cool)
            sup = Supervisor(bus, limits, log, sensors=args.sensors, vfd_every=args.vfd_every)
            try:
                fault = heat(sup, setpoints, args.minutes * 60, args.after, args.period,
                             belt_hz=belt_hz)
            except KeyboardInterrupt:
                print("\nstopped; heat-off was sent (see above)", file=sys.stderr)
                fault = None
            print("\nMissed replies:")
            for line in sup.summary():
                print("  " + line)
            print(f"\n{'FAULT: ' + fault if fault else 'Done.'}  Log: {path}")
            if fault:
                sys.exit(2)
    except (Refused, ValueError) as e:
        sys.exit(f"Refused: {e}")
    except BusError as e:
        sys.exit(f"Bus error: {e}")
    except KeyboardInterrupt:
        print("\nstopped", file=sys.stderr)
    finally:
        if log:
            log.close()
        bus.close()


if __name__ == "__main__":
    main()
