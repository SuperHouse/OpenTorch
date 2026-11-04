"""Typed access to the devices on the R350 bus: the zone cards, the profile
sensor cards and the conveyor VFD. All I/O goes through a Bus.

Reads are tried once unless the caller asks for more tries; the supervisor
counts misses itself. Writes are retried, and SV and AL2 writes are read
back.
"""

from dataclasses import dataclass

from . import protocol as pr
from .bus import BusError

AL2_HEAT = 400      # zone 1 AL2 value that holds the heater contactor in
OPEN_TC_PV = 400    # a card with an open thermocouple reads PV ~1360
VFD_MAX_HZ = 60.0


class ReadbackError(BusError):
    """A written value did not read back."""


def retry(fn, *args, tries=3):
    for k in range(tries):
        try:
            return fn(*args)
        except BusError:
            if k == tries - 1:
                raise


@dataclass(frozen=True)
class ZoneStatus:
    pv: int
    out: int     # calculated PID output %, not proof that the card heats
    sv: int


@dataclass(frozen=True)
class SensorReading:
    pv: int
    out: int

    @property
    def open(self):
        """Open thermocouple: OUT drops to 0 at once, PV climbs to ~1360."""
        return self.out == 0 or self.pv > OPEN_TC_PV


class ZoneCard:
    def __init__(self, bus, zone):
        if not 1 <= zone <= len(pr.ZONE_ADDRS):
            raise ValueError(f"zone must be 1-{len(pr.ZONE_ADDRS)}, not {zone}")
        self.bus = bus
        self.zone = zone
        self.addr = pr.ZONE_ADDRS[zone - 1]
        self.name = pr.device_name(self.addr)

    def read_status(self, tries=1):
        return ZoneStatus(*retry(self.bus.read_registers, self.addr, pr.PV, 3, tries=tries))

    def read(self, reg, tries=3):
        return retry(self.bus.read_registers, self.addr, reg, 1, tries=tries)[0]

    def read_params(self):
        return {name: self.read(reg) for name, reg in pr.CARD_PARAMS.items()}

    def write_checked(self, reg, value):
        retry(self.bus.write_register, self.addr, reg, value)
        got = self.read(reg)
        if got != value:
            raise ReadbackError(f"{self.name} register {reg:04X} read back as {got}, "
                                f"not {value}")

    def set_sv(self, sv):
        self.write_checked(pr.SV, sv)

    def set_al2(self, value):
        self.write_checked(pr.AL2, value)

    def heat_on(self):
        # 0x1022 is write-only: the ack is the only confirmation
        retry(self.bus.write_register, self.addr, pr.CMD, pr.CMD_HEAT_ON)

    def heat_off(self, tries=5):
        retry(self.bus.write_register, self.addr, pr.CMD, pr.CMD_HEAT_OFF, tries=tries)


class SensorCard:
    """Profile thermocouple T1-T4. Read only."""

    def __init__(self, bus, n):
        if not 1 <= n <= len(pr.SENSOR_ADDRS):
            raise ValueError(f"sensor must be 1-{len(pr.SENSOR_ADDRS)}, not {n}")
        self.bus = bus
        self.addr = pr.SENSOR_ADDRS[n - 1]
        self.name = pr.device_name(self.addr)

    def read(self, tries=1):
        pv, out, _ = retry(self.bus.read_registers, self.addr, pr.PV, 3, tries=tries)
        return SensorReading(pv, out)


class Vfd:
    """The conveyor drive (Delta VFD-L, Modbus ASCII slave 1)."""

    RUN_STATES = ("stopped", "decelerating", "standby", "running")

    def __init__(self, bus):
        self.bus = bus
        self.name = "VFD"

    def status(self, tries=1):
        """{error, status, Fcmd, Fout, Iout, DCbus, Vout, state}. Frequencies in Hz."""
        w = retry(self.bus.vfd_read, pr.VFD_ERROR, len(pr.VFD_STATUS_NAMES), tries=tries)
        s = dict(zip(pr.VFD_STATUS_NAMES, w))
        s["Fcmd"] /= 100
        s["Fout"] /= 100
        s["state"] = self.RUN_STATES[s["status"] & 3]
        return s

    def set_frequency(self, hz):
        if not 0 < hz <= VFD_MAX_HZ:
            raise ValueError(f"frequency must be 0-{VFD_MAX_HZ} Hz, not {hz}")
        retry(self.bus.vfd_write, pr.VFD_FREQ, round(hz * 100))

    def run(self, hz):
        self.set_frequency(hz)
        retry(self.bus.vfd_write, pr.VFD_CMD, pr.VFD_RUN)

    def stop(self):
        retry(self.bus.vfd_write, pr.VFD_CMD, pr.VFD_STOP)
