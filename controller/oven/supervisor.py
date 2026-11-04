"""Supervisor: heating state machine and safety rules for the R350.

States: IDLE -> HEATING -> IDLE (stop) or FAULT (a safety rule fired).
FAULT is latched: reset() is needed before the next start().

Safety rules (replacement-plan.md, "Safety requirements"):
  - A stop is final. Heat-on is sent only by start(), once per zone. Polling
    never turns a zone on.
  - Zone 1 is the heater-contactor enable (its AL2 output, active while it
    runs with AL2 = 400). It is turned on first and off first, so a stop or
    fault drops heater mains before anything else. After the heat-offs, its
    AL2 goes back to 0.
  - Comms loss: a zone that fails `comms_fail_polls` polls in a row while
    heating (each poll is `read_tries` reads) is a fault. Single misses
    happen while heating (switching noise), so they are only counted.
  - Over-temperature: a heating zone at SV + margin, or any other zone at the
    highest SV + margin, is a fault. PV above OPEN_TC_PV (open thermocouple)
    is a fault too. After a stop PVs rise for about a minute (heat stored in
    the plates); that is only logged.
  - A heat-off that is not acked is retried every poll until it is.

The supervisor is not thread-safe: one caller drives start/stop/step.
"""

import signal
import threading
import time
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum

from . import protocol as pr
from .bus import BusError
from .devices import AL2_HEAT, OPEN_TC_PV, SensorCard, Vfd, ZoneCard
from .runlog import Log

ENABLE_SV = 5   # °C, zone 1 SV when it runs only as the heater-contactor enable


class State(Enum):
    IDLE = "idle"
    HEATING = "heat"
    FAULT = "FAULT"


class Refused(Exception):
    """start() or reset() was refused; nothing was turned on."""


@dataclass
class Limits:
    max_sv: int = 150            # °C, highest SV start() accepts
    overtemp_margin: int = 20    # °C above SV
    comms_fail_polls: int = 3    # failed polls in a row of one zone -> fault
    read_tries: int = 2          # reads per poll before the poll counts as failed


@contextmanager
def _no_interrupt():
    """Ignore Ctrl-C while heat-offs are sent, so a second press cannot cut
    the sequence short (main thread only; signals cannot be set elsewhere)."""
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    old = signal.signal(signal.SIGINT, signal.SIG_IGN)
    try:
        yield
    finally:
        signal.signal(signal.SIGINT, old)


class Supervisor:
    def __init__(self, bus, limits=None, log=None, out=print, sensors=False, vfd_every=0):
        """sensors: also poll T1-T4. vfd_every: read the VFD status every N
        polls (0 = never). Neither can raise a fault."""
        self.bus = bus
        self.limits = limits or Limits()
        self.log = log or Log(None)
        self.out = out
        self.zones = [ZoneCard(bus, n) for n in range(1, len(pr.ZONE_ADDRS) + 1)]
        self.z1 = self.zones[0]
        self.sensors = [SensorCard(bus, n) for n in range(1, 5)] if sensors else []
        self.vfd = Vfd(bus) if vfd_every else None
        self.vfd_every = vfd_every
        self.state = State.IDLE
        self.fault = None
        self.setpoints = {}          # zone number -> SV of the current/last run
        self.pending_off = []        # ZoneCards whose heat-off was not acked
        self.last = {}               # device name -> last reading or None
        self.stats = {}              # device name -> Counter (see _poll)
        self.polls = 0
        self.bus_silent = False      # no device answered the last poll
        self._warned = set()

    # ------------------------------------------------------------ commands

    def start(self, setpoints):
        """setpoints: {zone number: SV}. Zone 1 always runs (it holds the
        heater contactor in); if it is not in setpoints it gets SV ENABLE_SV
        so it does not heat."""
        if self.state is not State.IDLE:
            raise Refused(f"state is {self.state.value}"
                          + (f" ({self.fault}); reset first" if self.fault else ""))
        if not setpoints:
            raise Refused("no zones given")
        for zone, sv in setpoints.items():
            if not 1 <= zone <= len(self.zones):
                raise Refused(f"no zone {zone}")
            if not 0 < sv <= self.limits.max_sv:
                raise Refused(f"zone {zone} SV {sv} is outside 1-{self.limits.max_sv} °C")

        before = {}
        for z in self.zones:
            try:
                before[z.zone] = z.read_status(tries=self.limits.read_tries)
            except BusError:
                raise Refused(f"{z.name} does not answer (oven not started?)") from None
        hot = self._over_limit(before, setpoints)
        if hot:
            raise Refused("; ".join(hot))
        enable_only = 1 not in setpoints
        if enable_only and before[1].pv < ENABLE_SV + 3:
            raise Refused(f"Z1 reads {before[1].pv} °C; it must read above {ENABLE_SV + 3} °C "
                          "to act as the enable without heating")

        self.setpoints = dict(setpoints)
        self._all_off("before start")
        if self.pending_off:
            raise Refused("no ack to heat-off from "
                          + ", ".join(z.name for z in self.pending_off))
        try:
            for zone, sv in sorted(setpoints.items()):
                self.zones[zone - 1].set_sv(sv)
            if enable_only:
                self.z1.set_sv(ENABLE_SV)
            self.z1.set_al2(AL2_HEAT)
        except BusError as e:
            raise Refused(f"setting up the cards failed: {e}") from None
        self.event("SV written and read back: " + ", ".join(
            f"Z{n} {before[n].sv}->{sv}" for n, sv in sorted(setpoints.items()))
            + (f"; Z1 {before[1].sv}->{ENABLE_SV} (enable only)" if enable_only else "")
            + f"; Z1 AL2 = {AL2_HEAT}")

        self.state = State.HEATING
        self._warned.clear()
        try:
            self.z1.heat_on()
            for zone in sorted(setpoints):
                if zone != 1:
                    self.zones[zone - 1].heat_on()
        except BusError as e:
            self._trip(f"heat-on failed: {e}")
            return
        self.event("HEAT ON: Z1" + (" (enable)" if enable_only else "")
                   + "".join(f", Z{n}" for n in sorted(setpoints) if n != 1))

    def stop(self, reason="stop"):
        """Heat-off to every zone, zone 1 first. Safe in any state. A fault
        stays latched."""
        if self.state is State.HEATING:
            self.state = State.IDLE
        self._all_off(reason)

    def reset(self):
        if self.state is not State.FAULT:
            return
        if self.pending_off:
            raise Refused("heat-off still not acked by "
                          + ", ".join(z.name for z in self.pending_off))
        self.event(f"fault reset ({self.fault})")
        self.state, self.fault = State.IDLE, None

    # ------------------------------------------------------------ polling

    def step(self):
        """One poll round: read every device, apply the safety rules.
        Returns {device name: reading or None}."""
        self.polls += 1
        self._retry_pending()
        zones = {z.zone: self._poll(z, z.read_status) for z in self.zones}
        readings = {z.name: zones[z.zone] for z in self.zones}
        for s in self.sensors:
            readings[s.name] = self._poll(s, s.read)
        if self.vfd and self.polls % self.vfd_every == 1 % self.vfd_every:
            readings[self.vfd.name] = self._poll(self.vfd, self.vfd.status)
        self.last.update(readings)
        self.bus_silent = not any(readings.values())

        state = self.state.value
        self.log.readings(state, {z.addr: (r.pv, r.out, r.sv) if r else None
                                  for z, r in zip(self.zones, zones.values())})
        for s in self.sensors:
            r = readings[s.name]
            self.log.row(state, s.name, (r.pv, r.out) if r else None,
                          ("open" if r.open else "") if r else "no reply")
        if self.vfd and self.vfd.name in readings:
            r = readings[self.vfd.name]
            self.log.row(state, self.vfd.name, None, (
                f"{r['state']} status 0x{r['status']:04X} Fcmd {r['Fcmd']:.2f} "
                f"Fout {r['Fout']:.2f} error {r['error']}") if r else "no reply")

        if self.state is State.HEATING:
            lost = [z.name for z in self.zones
                    if self.stats[z.name]["fail_run"] >= self.limits.comms_fail_polls]
            if lost:
                self._trip(f"comms loss: {', '.join(lost)} failed "
                           f"{self.limits.comms_fail_polls} polls in a row")
        hot = self._over_limit(zones)
        if hot and self.state is State.HEATING:
            self._trip("over-temperature: " + "; ".join(hot))
        elif hot:
            new = [h for h in hot if h.split()[0] not in self._warned]
            self._warned.update(h.split()[0] for h in hot)
            if new:
                self.event("over limit while not heating (heat already off): "
                           + "; ".join(new))
        return readings

    def _poll(self, dev, read):
        st = self.stats.setdefault(dev.name, Counter())
        st["polls"] += 1
        for k in range(self.limits.read_tries):
            try:
                r = read()
            except BusError:
                st["missed"] += 1
                continue
            st["retried"] += k > 0
            st["fail_run"] = 0
            return r
        st["failed"] += 1
        st["fail_run"] += 1
        st["max_fail_run"] = max(st["max_fail_run"], st["fail_run"])
        return None

    def _over_limit(self, zones, setpoints=None):
        """zones: {zone number: ZoneStatus or None}. Returns the messages."""
        setpoints = self.setpoints if setpoints is None else setpoints
        top = max(setpoints.values(), default=0)
        heating = self.state is State.HEATING
        msgs = []
        for n, r in zones.items():
            if r is None:
                continue
            name = f"Z{n}"
            if r.pv > OPEN_TC_PV:
                msgs.append(f"{name} PV {r.pv} (open thermocouple?)")
                continue
            if not setpoints:
                continue
            own = heating and n in setpoints
            limit = (setpoints[n] if own else top) + self.limits.overtemp_margin
            if r.pv >= limit:
                msgs.append(f"{name} PV {r.pv} >= {limit} "
                            f"({'its SV' if own else 'highest SV'} + "
                            f"{self.limits.overtemp_margin})")
        return msgs

    # ------------------------------------------------------------ shutdown

    def _trip(self, reason):
        self.state, self.fault = State.FAULT, reason
        self.event(f"FAULT: {reason}")
        self._all_off("fault")

    def _all_off(self, why):
        # zone 1 first: dropping it drops the heater contactor
        order = [self.z1] + [self.zones[n - 1] for n in sorted(self.setpoints) if n != 1]
        order += [z for z in self.zones if z not in order]
        failed = []
        with _no_interrupt():
            for z in order:
                try:
                    z.heat_off()
                except BusError:
                    failed.append(z)
            try:
                self.z1.set_al2(0)
            except BusError:
                self.event("Z1 AL2 = 0 not confirmed (heat-off was sent)")
        self.pending_off = failed
        if failed:
            self.event(f"HEAT OFF ({why}): NO ACK FROM "
                       + ", ".join(z.name for z in failed)
                       + ". PRESS THE OVEN'S RED STOP. Retrying every poll.")
        else:
            self.event(f"HEAT OFF ({why}): all zones acked, Z1 first")

    def _retry_pending(self):
        if not self.pending_off:
            return
        still = []
        with _no_interrupt():
            for z in self.pending_off:
                try:
                    z.heat_off(tries=1)
                    self.event(f"{z.name} heat-off acked on retry")
                except BusError:
                    still.append(z)
        self.pending_off = still

    def event(self, text):
        self.log.event(text, self.out)

    # ------------------------------------------------------------ reports

    def summary(self):
        """Lines describing the missed replies per device."""
        lines = [f"{'dev':<4} {'polls':>6} {'missed':>7} {'retried':>8} {'failed':>7} "
                 f"{'max run':>8}"]
        for name, st in self.stats.items():
            lines.append(f"{name:<4} {st['polls']:>6} {st['missed']:>7} {st['retried']:>8} "
                         f"{st['failed']:>7} {st['max_fail_run']:>8}")
        b = self.bus.stats
        lat = self.bus.latency_ms
        lines.append(f"bus: {b['requests']} requests, {b['ok']} ok, {b['no_reply']} no reply, "
                     f"{b['bad_frame']} bad frames"
                     + (f"; latency {min(lat):.0f}-{max(lat):.0f} ms, "
                        f"mean {sum(lat) / len(lat):.0f} ms" if lat else ""))
        return lines
