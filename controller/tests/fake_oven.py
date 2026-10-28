"""A stand-in for serial.Serial that answers like the R350 bus.

Cards answer only when the port is at 2400 8N1, the VFD only at 9600 7N2,
the same as on the real bus where the other baud rate reads as garbage.
"""

from oven import protocol as pr


class FakeOven:
    def __init__(self, port=None, timeout=0.02, echo=False, **settings):
        self.settings = dict(settings)
        self.timeout = timeout
        self.echo = echo
        self.out = bytearray()
        self.silent = set()      # addresses that do not answer
        self.corrupt = set()     # addresses whose replies get a bad CRC
        self.writes = []
        self.cards = {}
        for addr in pr.ZONE_ADDRS + pr.SENSOR_ADDRS:
            regs = {r: 0 for r in range(0x1000, 0x1024, 2)}
            regs.update({pr.PV: 20, pr.OUT: 100, pr.SV: 150, pr.AL1: 400,
                         pr.P: 30, pr.I: 250, pr.D: 50, pr.XL: 1000,
                         pr.ADDR: addr, pr.R1016: 100})
            self.cards[addr] = regs
        self.vfd = {r: 0 for r in range(0x2000, 0x2003)}
        self.vfd.update({r: 0 for r in range(0x2100, 0x2107)})
        self.vfd[pr.VFD_STATUS] = 0x0500

    # --- serial.Serial interface used by Bus
    def apply_settings(self, d):
        self.settings.update(d)

    def reset_input_buffer(self):
        self.out.clear()

    def flush(self):
        pass

    def close(self):
        pass

    def read(self, n):
        data = bytes(self.out[:n])
        del self.out[:n]
        return data

    def write(self, data):
        data = bytes(data)
        self.writes.append((self.settings.get("baudrate"), data))
        if self.echo:
            self.out += data
        if self.settings.get("baudrate") == 2400 and self.settings.get("bytesize") == 8:
            self._card(data)
        elif self.settings.get("baudrate") == 9600 and self.settings.get("bytesize") == 7:
            self._vfd(data)
        return len(data)

    # --- device behaviour
    def _card(self, frame):
        if not pr.crc_ok(frame) or frame[0] not in self.cards or frame[0] in self.silent:
            return
        addr, func = frame[0], frame[1]
        reg = frame[2] << 8 | frame[3]
        regs = self.cards[addr]
        if func == 3:
            count = frame[4] << 8 | frame[5]
            data = b"".join(regs.get(reg + 2 * k, 0).to_bytes(2, "big") for k in range(count))
            reply = pr.rtu_frame(addr, 3, bytes([len(data)]) + data)
        elif func == 6:
            regs[reg] = frame[4] << 8 | frame[5]
            reply = pr.rtu_frame(addr, 6, frame[2:4])   # the cards' short ack
        else:
            reply = pr.rtu_frame(addr, func | 0x80, b"\x01")
        if addr in self.corrupt:
            reply = reply[:-1] + bytes([reply[-1] ^ 0xFF])
        self.out += reply

    def _vfd(self, line):
        try:
            raw = pr.parse_ascii(line)
        except ValueError:
            return
        if raw[0] != pr.VFD_SLAVE:
            return
        func, reg, val = raw[1], raw[2] << 8 | raw[3], raw[4] << 8 | raw[5]
        if func == 3:
            data = b"".join(self.vfd.get(reg + k, 0).to_bytes(2, "big") for k in range(val))
            self.out += pr.ascii_frame(pr.VFD_SLAVE, 3, bytes([len(data)]) + data)
        elif func == 6:
            self.vfd[reg] = val
            self.out += line                         # Delta echoes a write
