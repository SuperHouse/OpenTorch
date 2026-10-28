"""Frame formats for the R350 RS-485 bus (no I/O here).

Zone and sensor cards: Modbus RTU, 2400 8N1. Registers are spaced 2 apart and
a read of N "registers" returns N words from reg, reg+2, reg+4, ... A write
(06) is acknowledged with a short 6-byte frame: adr 06 regHi regLo crcLo crcHi.

Conveyor VFD (Delta VFD-L): Modbus ASCII, 9600 7N2, slave 1.

See CLAUDE.md ("Zone control card protocol", "VFD / inverter protocol").
"""

ZONE_ADDRS = tuple(range(51, 63))     # zones 1-12
SENSOR_ADDRS = tuple(range(71, 75))   # profile thermocouples T1-T4
VFD_SLAVE = 1

# Card registers
PV, OUT, SV, AL1, AL2, SC, P, I, D, XL, ADDR, R1016, CMD = (
    0x1000, 0x1002, 0x1004, 0x1006, 0x1008, 0x100A, 0x100C, 0x100E,
    0x1010, 0x1012, 0x1014, 0x1016, 0x1022)
CARD_PARAMS = {"OUT": OUT, "SV": SV, "AL1": AL1, "AL2": AL2, "SC": SC, "P": P,
               "I": I, "D": D, "XL": XL, "ADDR": ADDR, "R1016": R1016}
CMD_HEAT_ON, CMD_HEAT_OFF = 0x99, 0xCC

# VFD registers
VFD_CMD, VFD_FREQ, VFD_ERROR, VFD_STATUS = 0x2000, 0x2001, 0x2100, 0x2101
VFD_STATUS_NAMES = ("error", "status", "Fcmd", "Fout", "Iout", "DCbus", "Vout")
VFD_RUN, VFD_STOP = 0x0002, 0x0001


def device_name(addr):
    if addr in ZONE_ADDRS:
        return f"Z{addr - 50}"
    if addr in SENSOR_ADDRS:
        return f"T{addr - 70}"
    return f"a{addr}"


# ---------------------------------------------------------------- RTU

def crc16(data):
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def rtu_frame(addr, func, payload):
    body = bytes([addr, func]) + bytes(payload)
    crc = crc16(body)
    return body + bytes([crc & 0xFF, crc >> 8])


def crc_ok(frame):
    return len(frame) >= 4 and crc16(frame) == 0


def read_request(addr, reg, count):
    return rtu_frame(addr, 3, [reg >> 8, reg & 0xFF, count >> 8, count & 0xFF])


def write_request(addr, reg, value):
    return rtu_frame(addr, 6, [reg >> 8, reg & 0xFF, value >> 8, value & 0xFF])


def words(data):
    return [data[k] << 8 | data[k + 1] for k in range(0, len(data) - 1, 2)]


# ---------------------------------------------------------------- ASCII

def lrc(data):
    return -sum(data) & 0xFF


def ascii_frame(slave, func, payload):
    body = bytes([slave, func]) + bytes(payload)
    return b":" + (body + bytes([lrc(body)])).hex().upper().encode() + b"\r\n"


def parse_ascii(line):
    """Return the frame bytes (slave, func, data) without the LRC."""
    text = line.strip()
    if not text.startswith(b":"):
        raise ValueError(f"no ':' in {line!r}")
    raw = bytes.fromhex(text[1:].decode("ascii"))
    if len(raw) < 3 or sum(raw) & 0xFF:
        raise ValueError(f"bad LRC in {line!r}")
    return raw[:-1]


def vfd_read_request(reg, count, slave=VFD_SLAVE):
    return ascii_frame(slave, 3, [reg >> 8, reg & 0xFF, count >> 8, count & 0xFF])


def vfd_write_request(reg, value, slave=VFD_SLAVE):
    return ascii_frame(slave, 6, [reg >> 8, reg & 0xFF, value >> 8, value & 0xFF])
