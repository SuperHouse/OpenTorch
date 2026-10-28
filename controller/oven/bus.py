"""The only code that touches the serial port.

One port serves both sides of the bus: the cards at 2400 8N1 (Modbus RTU) and
the conveyor VFD at 9600 7N2 (Modbus ASCII). The port is switched to VFD
settings for one exchange and straight back to card settings.

Writes are refused unless the Bus is created with read_only=False.
"""

import time
from collections import Counter

import serial

from . import protocol as pr

CARD_SETTINGS = dict(baudrate=2400, bytesize=8, parity="N", stopbits=1)
VFD_SETTINGS = dict(baudrate=9600, bytesize=7, parity="N", stopbits=2)


class BusError(Exception):
    pass


class NoReply(BusError):
    pass


class BadFrame(BusError):
    pass


class DeviceException(BusError):
    """The device answered with a Modbus exception."""


class Bus:
    def __init__(self, port, *, read_only=True, timeout=0.3, gap=0.05,
                 echo=False, serial_factory=serial.Serial):
        """timeout: seconds to wait for a reply. gap: minimum idle time
        between transactions. echo: the adapter returns our own transmitted
        bytes before the reply (see detect_echo)."""
        self.read_only = read_only
        self.timeout = timeout
        self.gap = gap
        self.echo = echo
        self.ser = serial_factory(port, timeout=0.02, **CARD_SETTINGS)
        self.mode = "card"
        self.stats = Counter()
        self.latency_ms = []
        self._last = 0.0
        self._pending_echo = b""

    def close(self):
        self.ser.close()

    # ------------------------------------------------------------ cards

    def read_registers(self, addr, reg, count):
        """Read `count` words starting at card register `reg` (word k comes
        from reg + 2k)."""
        req = pr.read_request(addr, reg, count)
        reply = self._card_exchange(req, addr, 3)
        if reply[2] != 2 * count:
            raise BadFrame(f"{pr.device_name(addr)}: {reply[2]} data bytes, expected {2 * count}")
        return pr.words(reply[3:-2])

    def write_register(self, addr, reg, value):
        if self.read_only:
            raise PermissionError("bus is read-only")
        req = pr.write_request(addr, reg, value)
        reply = self._card_exchange(req, addr, 6)
        if reply[2:4] != req[2:4]:
            raise BadFrame(f"{pr.device_name(addr)}: ack for wrong register {reply.hex(' ')}")

    def _card_exchange(self, req, addr, func):
        try:
            return self._card_exchange_inner(req, addr, func)
        except BadFrame:
            self.stats["bad_frame"] += 1
            self._last = time.monotonic()
            raise

    def _card_exchange_inner(self, req, addr, func):
        self._set_mode("card")
        self._send(req)
        t0 = time.monotonic()
        deadline = t0 + self.timeout
        head = self._read_exact(2, deadline, req)
        if head[0] != addr:
            raise BadFrame(f"reply from address {head[0]}, expected {addr}")
        if head[1] == func | 0x80:
            rest = self._read_exact(3, deadline, req)
            frame = head + rest
            if pr.crc_ok(frame):
                raise DeviceException(f"{pr.device_name(addr)}: exception code {rest[0]:02X}")
            raise BadFrame(f"bad exception frame {frame.hex(' ')}")
        if head[1] != func:
            raise BadFrame(f"reply function {head[1]:02X}, expected {func:02X}")
        if func == 3:
            count = self._read_exact(1, deadline, req)
            frame = head + count + self._read_exact(count[0] + 2, deadline, req)
        else:
            # cards ack a write with 6 bytes; accept a standard 8-byte echo too
            frame = head + self._read_exact(4, deadline, req)
            if not pr.crc_ok(frame):
                frame += self._read_exact(2, deadline, req)
        if not pr.crc_ok(frame):
            raise BadFrame(f"CRC error {frame.hex(' ')}")
        self._done(t0)
        return frame

    # ------------------------------------------------------------ VFD

    def vfd_read(self, reg, count, slave=pr.VFD_SLAVE):
        reply = self._vfd_exchange(pr.vfd_read_request(reg, count, slave), slave, 3)
        if reply[2] != 2 * count:
            raise BadFrame(f"VFD: {reply[2]} data bytes, expected {2 * count}")
        return pr.words(reply[3:])

    def vfd_write(self, reg, value, slave=pr.VFD_SLAVE):
        if self.read_only:
            raise PermissionError("bus is read-only")
        self._vfd_exchange(pr.vfd_write_request(reg, value, slave), slave, 6)

    def _vfd_exchange(self, req, slave, func):
        self._set_mode("vfd")
        try:
            return self._vfd_exchange_inner(req, slave, func)
        except BadFrame:
            self.stats["bad_frame"] += 1
            self._last = time.monotonic()
            raise
        finally:
            self._set_mode("card")

    def _vfd_exchange_inner(self, req, slave, func):
        self._send(req)
        skip_echo, self._pending_echo = bool(self._pending_echo), b""
        t0 = time.monotonic()
        deadline = t0 + self.timeout
        line = b""
        while not line.endswith(b"\n"):
            if time.monotonic() > deadline:
                self.stats["no_reply"] += 1
                raise NoReply(f"VFD: no reply ({line!r})")
            line += self.ser.read(1)
            # a write reply is identical to the request, so drop only one copy
            if skip_echo and line == req:
                line, skip_echo = b"", False
        try:
            reply = pr.parse_ascii(line)
        except ValueError as e:
            raise BadFrame(f"VFD: {e}") from None
        if reply[0] != slave:
            raise BadFrame(f"VFD: reply from slave {reply[0]}")
        if reply[1] == func | 0x80:
            raise DeviceException(f"VFD: exception code {reply[2]:02X}")
        self._done(t0)
        return reply

    # ------------------------------------------------------------ helpers

    def detect_echo(self, addr):
        """Send one harmless read and report whether the adapter echoes our
        own bytes back. Sets self.echo accordingly."""
        self._set_mode("card")
        req = pr.read_request(addr, pr.PV, 3)
        self._send(req)
        deadline = time.monotonic() + self.timeout
        data = b""
        while time.monotonic() < deadline:
            data += self.ser.read(64)
        self._last = time.monotonic()
        self.echo = data.startswith(req)
        return self.echo, data

    def _set_mode(self, mode):
        if mode != self.mode:
            self.ser.apply_settings(CARD_SETTINGS if mode == "card" else VFD_SETTINGS)
            self.mode = mode
            time.sleep(0.005)

    def _send(self, req):
        wait = self._last + self.gap - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self.ser.reset_input_buffer()
        self.ser.write(req)
        self.ser.flush()
        self.stats["requests"] += 1
        if self.echo:
            self._pending_echo = req
        else:
            self._pending_echo = b""

    def _read_exact(self, n, deadline, req):
        if self._pending_echo:
            echo, self._pending_echo = self._pending_echo, b""
            got = self._read_exact(len(echo), deadline, req)
            if got != echo:
                raise BadFrame(f"echo mismatch {got.hex(' ')}")
        data = b""
        while len(data) < n:
            if time.monotonic() > deadline:
                self._last = time.monotonic()
                if data:
                    raise BadFrame(f"short reply {data.hex(' ')} to {req.hex(' ')}")
                self.stats["no_reply"] += 1
                raise NoReply(f"no reply to {req.hex(' ')}")
            data += self.ser.read(n - len(data))
        return data

    def _done(self, t0):
        now = time.monotonic()
        self._last = now
        self.stats["ok"] += 1
        self.latency_ms.append((now - t0) * 1000)
