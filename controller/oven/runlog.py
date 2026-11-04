"""CSV log of a run on the oven: one row per reading and per event."""

import csv
import time
from datetime import datetime

from . import protocol as pr


class Log:
    def __init__(self, path):
        self.f = open(path, "w", newline="") if path else None
        self.w = csv.writer(self.f) if path else None
        if self.w:
            self.w.writerow(["time", "elapsed_s", "state", "device", "pv", "out", "sv", "note"])
        self.t0 = time.monotonic()

    def row(self, state, device, vals, note=""):
        if self.w:
            vals = tuple(vals or ())[:3]
            self.w.writerow([datetime.now().strftime("%H:%M:%S.%f")[:-3],
                             f"{time.monotonic() - self.t0:.1f}", state, device,
                             *vals, *[""] * (3 - len(vals)), note])
            self.f.flush()

    def event(self, text, out=print):
        out(f"{datetime.now():%H:%M:%S}  ** {text}")
        self.row("event", "", None, text)

    def readings(self, state, zones):
        """zones: {addr: (pv, out, sv) or None}"""
        for addr, r in zones.items():
            self.row(state, pr.device_name(addr), r, "" if r else "no reply")

    def close(self):
        if self.f:
            self.f.close()
