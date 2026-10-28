#!/usr/bin/env python3
"""Decode a Saleae Logic 2 capture of the R350 RS-485 bus.

Input is a Logic 2 "Export Data" CSV of the digital channel that is wired to
the RS-485 receiver output (RO). The time column can be in seconds or ISO8601
(tick "Use ISO8601 timestamps" in the export to get wall-clock times).

The same signal is decoded three ways, and only frames that pass their
checksum are kept:

  RTU2400  2400 8N1 Modbus RTU    zone cards (51-62), profile sensors (71-74)
  ASC9600  9600 7N2 Modbus ASCII  Delta VFD-L (factory default, slave 1)
  RTU9600  9600 8N1 Modbus RTU    older VFD setup (slaves 0x20-0x22)

Usage:
  decode_capture.py capture.csv [--column "Channel 0"] [--start 14:02:10]
                    [--csv out.csv] [--no-unparsed]
"""

import argparse
import bisect
import csv
import re
import sys
from array import array
from collections import Counter, defaultdict
from datetime import datetime, timedelta

# ---------------------------------------------------------------- device map

CARD_REGS = {
    0x00: "PV", 0x02: "OUT", 0x04: "SV", 0x06: "AL1", 0x08: "AL2",
    0x0A: "SC", 0x0C: "P", 0x0E: "I", 0x10: "D", 0x12: "XL",
    0x14: "ADDR", 0x22: "CMD",
}
CARD_CMDS = {0x99: "heat ON", 0xCC: "heat OFF", 0x03: "self-tune?"}

VFD_REGS = {
    0x2000: "CMD", 0x2001: "FREQ", 0x2002: "FAULT/RESET", 0x2100: "ERR",
    0x2101: "STATUS", 0x2102: "Fcmd", 0x2103: "Fout", 0x2104: "Iout",
    0x2105: "DCbus", 0x2106: "Vout",
}


def device_name(link, addr):
    if link == "ASC9600":
        return f"VFD{addr}"
    if 51 <= addr <= 66:
        return f"Z{addr - 50}"
    if 71 <= addr <= 74:
        return f"T{addr - 70}"
    if link == "RTU9600" and 0x20 <= addr <= 0x22:
        return ("VFDup", "VFDlo", "VFDconv")[addr - 0x20]
    return f"a{addr}"


def is_vfd(link, addr):
    return link != "RTU2400" or 0x20 <= addr <= 0x22


def reg_name(link, addr, reg):
    if is_vfd(link, addr):
        return f"{reg:04X} {VFD_REGS.get(reg, '?')}"
    off = reg - 0x1000 if reg >= 0x1000 else reg
    return f"{reg:04X} {CARD_REGS.get(off, '?')}"


def value_text(link, addr, reg, val):
    if is_vfd(link, addr):
        if reg == 0x2001 or reg in (0x2102, 0x2103):
            return f"{val / 100:.2f} Hz"
        if reg == 0x2000:
            run = {1: "stop", 2: "run", 3: "jog+run"}.get(val & 3, "-")
            dirn = {1: "FWD", 2: "REV", 3: "toggle dir"}.get((val >> 4) & 3, "")
            return f"0x{val:04X} {run} {dirn}".rstrip()
        return f"0x{val:04X} ({val})"
    off = reg - 0x1000 if reg >= 0x1000 else reg
    if off == 0x22:
        return f"0x{val:02X} {CARD_CMDS.get(val, '?')}"
    signed = val - 0x10000 if val & 0x8000 else val
    return f"{val} (0x{val:04X}{', ' + str(signed) if signed < 0 else ''})"


# ---------------------------------------------------------------- input

def parse_iso(s):
    m = re.match(r"^(.*T\d\d:\d\d:\d\d)(?:\.(\d+))?(.*)$", s.strip())
    if not m:
        raise ValueError(f"bad timestamp: {s!r}")
    base, frac, tz = m.groups()
    tz = "+00:00" if tz == "Z" else tz
    return datetime.fromisoformat(f"{base}.{(frac or '0')[:6].ljust(6, '0')}{tz}")


def load(path, column):
    """Return transition times (s), levels after each transition, abs start."""
    times, levels = array("d"), bytearray()
    t0_abs = None
    with open(path, newline="") as f:
        rows = csv.reader(f)
        header = [h.strip() for h in next(rows)]
        if column is None:
            if len(header) != 2:
                sys.exit(f"several channels in CSV {header[1:]}; pick one with --column")
            col = 1
        elif column in header:
            col = header.index(column)
        else:
            sys.exit(f"column {column!r} not in {header}")
        iso = None
        for row in rows:
            if not row:
                continue
            ts = row[0]
            if iso is None:
                try:
                    float(ts)
                    iso = False
                except ValueError:
                    iso = True
            if iso:
                dt = parse_iso(ts)
                if t0_abs is None:
                    t0_abs = dt
                t = (dt - t0_abs).total_seconds()
            else:
                t = float(ts)
            v = 1 if row[col].strip() not in ("0", "0.0") else 0
            if levels and v == levels[-1]:
                continue  # a transition on another channel
            times.append(t)
            levels.append(v)
    return times, levels, t0_abs


# ---------------------------------------------------------------- UART

def uart(times, levels, baud, bits, stop):
    """Software UART over the transition list: [(t_start, t_end, byte, ok)]."""
    bit = 1.0 / baud
    n = len(times)

    def level(t):
        i = bisect.bisect_right(times, t) - 1
        return levels[i] if i >= 0 else 1

    out = []
    i = 1
    while i < n:
        if levels[i] != 0:
            i += 1
            continue
        ts = times[i]
        if level(ts + bit / 2) != 0:
            i += 1  # glitch, not a start bit
            continue
        v = 0
        for k in range(bits):
            if level(ts + (1.5 + k) * bit):
                v |= 1 << k
        ok = all(level(ts + (1.5 + bits + s) * bit) for s in range(stop))
        out.append((ts, ts + (1 + bits + stop) * bit, v, ok))
        i = bisect.bisect_right(times, ts + (bits + stop + 0.5) * bit)
    return out


def bursts(chars, char_time, gap_chars):
    """Split into runs of framing-good bytes separated by idle gaps."""
    run, last_end = [], None
    for c in chars:
        if not c[3]:
            if run:
                yield run
            run, last_end = [], None
            continue
        if last_end is not None and c[0] - last_end > gap_chars * char_time:
            yield run
            run = []
        run.append(c)
        last_end = c[1]
    if run:
        yield run


# ---------------------------------------------------------------- Modbus

def crc16(data):
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def rtu_frames(link, chars, baud, report_unparsed):
    """Greedy parse of each burst; handles back-to-back request + reply."""
    frames, unparsed = [], []
    for run in bursts(chars, 10.0 / baud, 5):
        b = bytes(c[2] for c in run)
        i, junk_start = 0, None
        while i < len(b):
            cands = []
            if i + 2 < len(b):
                f = b[i + 1]
                if f & 0x80 and f & 0x7F in (3, 6, 16):
                    cands = [5]
                elif f == 3:
                    cands = [8, 5 + b[i + 2]]
                elif f == 6:
                    # zone cards ack a write with only adr 06 regHi regLo + CRC
                    cands = [8, 6]
                elif f == 16:
                    cands = [8] + ([9 + b[i + 6]] if i + 6 < len(b) else [])
            for size in cands:
                if i + size <= len(b) and crc16(b[i:i + size]) == 0:
                    if junk_start is not None:
                        unparsed.append((link, run[junk_start][0], b[junk_start:i]))
                        junk_start = None
                    frames.append((run[i][0], run[i + size - 1][1], link,
                                   b[i:i + size - 2], b[i:i + size]))
                    i += size
                    break
            else:
                if junk_start is None:
                    junk_start = i
                i += 1
        if junk_start is not None:
            unparsed.append((link, run[junk_start][0], b[junk_start:]))
    if not report_unparsed:
        unparsed = []
    return frames, [u for u in unparsed if len(u[2]) >= 3]


def ascii_frames(chars):
    frames, bad = [], 0
    buf, t_start = None, None
    for ts, te, v, ok in chars:
        ch = chr(v & 0x7F)
        if not ok:
            buf = None
            continue
        if ch == ":":
            buf, t_start = [], ts
            continue
        if buf is None:
            continue
        if ch == "\n":
            text = "".join(buf).rstrip("\r")
            buf = None
            try:
                raw = bytes.fromhex(text)
            except ValueError:
                bad += 1
                continue
            if len(raw) >= 3 and sum(raw) & 0xFF == 0:
                frames.append((t_start, te, "ASC9600", raw[:-1], (":" + text).encode()))
            else:
                bad += 1
        else:
            buf.append(ch)
            if len(buf) > 520:
                buf = None
                bad += 1
    return frames, bad


# ---------------------------------------------------------------- analysis

class Decoder:
    def __init__(self):
        self.pending = {}  # (link, addr) -> request
        self.stats = Counter()
        self.latency = defaultdict(list)
        self.no_reply = Counter()
        self.reg_ops = Counter()

    def describe(self, t0, t1, link, p):
        """Return (device, kind, text) for payload p (address..data, no checksum)."""
        addr, func = p[0], p[1]
        dev = device_name(link, addr)
        key = (link, addr)
        req = self.pending.get(key)

        if func & 0x80:
            self.pending.pop(key, None)
            return dev, "EXC", f"exception func {func & 0x7F:02X} code {p[2]:02X}"

        if func == 3 and len(p) == 6:
            reg, cnt = p[2] << 8 | p[3], p[4] << 8 | p[5]
            self._new_request(key, t1, dict(func=3, reg=reg, cnt=cnt, raw=p, dev=dev))
            self.reg_ops[(dev, "read", reg_name(link, addr, reg), cnt)] += 1
            return dev, "REQ", f"read {reg_name(link, addr, reg)} x{cnt}"

        if func == 3:
            words = [p[3 + 2 * k] << 8 | p[4 + 2 * k] for k in range(p[2] // 2)]
            extra = " +odd byte" if p[2] % 2 else ""
            if req and req["func"] == 3:
                self._answered(key, req, t0)
                # Zone cards: word k comes from register reg+2k (confirmed: a read of
                # 1000 x3 returns PV, OUT, SV). The VFD uses standard reg+k.
                step = 1 if is_vfd(link, addr) else 2
                parts = [f"{reg_name(link, addr, req['reg'] + step * k).split()[1]}"
                         f"[{req['reg'] + step * k:04X}]={w}" for k, w in enumerate(words)]
                return dev, "RSP", (f"{reg_name(link, addr, req['reg'])}: " + " ".join(parts) +
                                    extra + f"  ({p[2]} bytes)")
            return dev, "RSP?", f"{p[2]} bytes, no matching request: " + \
                " ".join(f"{w:04X}" for w in words) + extra

        if func == 6 and len(p) == 4:
            reg = p[2] << 8 | p[3]
            if req and req["func"] == 6 and req["reg"] == reg:
                self._answered(key, req, t0)
                return dev, "ACK", f"{reg_name(link, addr, reg)} (short ack)"
            return dev, "ACK?", f"{reg_name(link, addr, reg)} (short ack, no matching write)"

        if func == 6:
            reg, val = p[2] << 8 | p[3], p[4] << 8 | p[5]
            text = f"{reg_name(link, addr, reg)} = {value_text(link, addr, reg, val)}"
            if req and req["func"] == 6 and req["raw"] == p and t0 - req["t_end"] < 3:
                self._answered(key, req, t0)
                return dev, "ACK", text
            self._new_request(key, t1, dict(func=6, reg=reg, raw=p, dev=dev))
            self.reg_ops[(dev, "write", reg_name(link, addr, reg), value_text(link, addr, reg, val))] += 1
            return dev, "WRITE", text

        if func == 16:
            reg, cnt = p[2] << 8 | p[3], p[4] << 8 | p[5]
            if len(p) == 6:
                if req and req["func"] == 16:
                    self._answered(key, req, t0)
                return dev, "ACK", f"wrote {reg_name(link, addr, reg)} x{cnt}"
            words = [p[7 + 2 * k] << 8 | p[8 + 2 * k] for k in range(p[6] // 2)]
            self._new_request(key, t1, dict(func=16, reg=reg, raw=p, dev=dev))
            return dev, "WRITE", f"{reg_name(link, addr, reg)} x{cnt} = " + \
                " ".join(f"{w:04X}" for w in words)

        return dev, "?", f"func {func:02X}"

    def _new_request(self, key, t_end, req):
        old = self.pending.get(key)
        if old:
            self.no_reply[(old["dev"], old["func"])] += 1
        req["t_end"] = t_end
        self.pending[key] = req
        self.stats[(key[0], req["dev"], "requests")] += 1

    def _answered(self, key, req, t0):
        self.latency[key[0]].append((t0 - req["t_end"]) * 1000)
        self.stats[(key[0], req["dev"], "replies")] += 1
        del self.pending[key]

    def finish(self):
        for req in self.pending.values():
            self.no_reply[(req["dev"], req["func"])] += 1


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("capture", help="Logic 2 exported CSV")
    ap.add_argument("--column", help='channel column name, e.g. "Channel 0"')
    ap.add_argument("--start", help="wall-clock time of capture start, HH:MM:SS[.fff] "
                    "(not needed with ISO8601 timestamps)")
    ap.add_argument("--csv", help="also write decoded frames to this CSV")
    ap.add_argument("--no-unparsed", action="store_true",
                    help="hide clean byte runs that are not valid Modbus frames")
    args = ap.parse_args()

    times, levels, t0_abs = load(args.capture, args.column)
    if len(times) < 2:
        sys.exit("no transitions on that channel")
    if t0_abs is None and args.start:
        t0_abs = datetime.combine(datetime.today(), datetime.strptime(
            args.start, "%H:%M:%S.%f" if "." in args.start else "%H:%M:%S").time())
    print(f"# {len(times)} transitions, {times[-1] - times[0]:.1f} s", file=sys.stderr)

    frames, unparsed = [], []
    f, u = rtu_frames("RTU2400", uart(times, levels, 2400, 8, 1), 2400, not args.no_unparsed)
    frames += f
    unparsed += u
    # 2400 traffic also reads as clean-looking 9600 bytes, so no unparsed report here
    f, _ = rtu_frames("RTU9600", uart(times, levels, 9600, 8, 1), 9600, False)
    frames += [x for x in f if 0x20 <= x[3][0] <= 0x22 or x[3][0] == 1]
    f, bad_ascii = ascii_frames(uart(times, levels, 9600, 7, 2))
    frames += f

    events = [(fr[0], "F", fr) for fr in frames] + [(x[1], "U", x) for x in unparsed]
    events.sort(key=lambda e: e[0])

    def stamp(t):
        if t0_abs is None:
            return f"{t:10.3f}"
        return (t0_abs + timedelta(seconds=t)).strftime("%H:%M:%S.%f")[:-3] + f" +{t:9.3f}"

    dec = Decoder()
    out_csv = None
    if args.csv:
        out_csv = csv.writer(open(args.csv, "w", newline=""))
        out_csv.writerow(["time", "rel_s", "link", "device", "kind", "decoded", "hex"])

    for t, kind, e in events:
        if kind == "F":
            t0, t1, link, payload, raw = e
            dev, k, text = dec.describe(t0, t1, link, payload)
            hexs = raw.decode() if link == "ASC9600" else raw.hex(" ").upper()
        else:
            link, t0, data = e
            dev, k, text = "-", "UNPARSED", f"{len(data)} clean bytes, not a valid frame"
            hexs = data.hex(" ").upper()
        print(f"{stamp(t)}  {link}  {dev:<7} {k:<8} {text}   [{hexs}]")
        if out_csv:
            wall = stamp(t).split()[0] if t0_abs else ""
            out_csv.writerow([wall, f"{t:.6f}", link, dev, k, text, hexs])
    dec.finish()

    print("\n# ---- summary", file=sys.stderr)
    for link in ("RTU2400", "ASC9600", "RTU9600"):
        lat = dec.latency.get(link)
        n = sum(1 for fr in frames if fr[2] == link)
        line = f"# {link}: {n} frames"
        if lat:
            line += f", reply latency ms min/avg/max {min(lat):.1f}/{sum(lat) / len(lat):.1f}/{max(lat):.1f}"
        print(line, file=sys.stderr)
    print(f"# ASCII frames with bad LRC/hex: {bad_ascii}", file=sys.stderr)
    print(f"# unparsed clean byte runs: {len(unparsed)}", file=sys.stderr)
    devs = sorted({(k[0], k[1]) for k in dec.stats})
    for link, dev in devs:
        rq, rp = dec.stats[(link, dev, "requests")], dec.stats[(link, dev, "replies")]
        print(f"#   {link} {dev:<7} requests {rq:6}  replies {rp:6}", file=sys.stderr)
    if dec.no_reply:
        print("# no reply:", ", ".join(f"{d} f{fn:02X} x{c}" for (d, fn), c in
                                       sorted(dec.no_reply.items())), file=sys.stderr)
    print("# register operations:", file=sys.stderr)
    for (dev, op, reg, arg), c in sorted(dec.reg_ops.items()):
        print(f"#   {dev:<7} {op:<5} {reg:<12} {arg!s:<22} x{c}", file=sys.stderr)


if __name__ == "__main__":
    main()
