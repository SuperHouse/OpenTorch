#!/usr/bin/env python3
"""Check a measured board profile against the paste's reflow window.

  profile_report.py logs/run-<time>.csv [--probe T1] [--from S] [--to S]

Reads a run log written by ovenctl.py (it logs T1-T4 on every poll), finds
each pass of the probe through the oven (the probe rises above 40 °C from
below and goes on to at least --min-peak), and prints, for each pass, the
measured profile against the target. The target is the AIM no-clean window
for SAC305 (the production paste, AIM M8; see CLAUDE.md).

Readings with the open-thermocouple flag, or above 400 °C, are skipped.
Times between samples are interpolated, so crossing times are good to about
a second at the usual ~2.5 s poll period.

Limitation: the sensor card filters PV (a lag of a few seconds, seen on
2026-11-03), so a fast peak reads a little low and late, and the steepest
ramp and cooling rates read a little low.
"""

import argparse
import csv
import sys
from dataclasses import dataclass

OPEN_PV = 400          # °C; above this the probe is open or not plugged in
SLOPE_WINDOW = 10.0    # s; ramp and cooling rates are measured over this span


@dataclass(frozen=True)
class Target:
    name: str
    liquidus: float
    ramp_avg: tuple          # °C/s, average from 40 °C to soak_min
    ramp_max: float          # °C/s, steepest over SLOPE_WINDOW before the peak
    soak_min: float
    soak_max: float
    soak_time: tuple         # s from soak_min to soak_max (ramp-soak-spike only)
    tal: tuple               # s above liquidus
    peak: tuple              # °C
    t40_to_peak: tuple       # s
    cool_max: float          # °C/s, steepest fall over SLOPE_WINDOW after the peak


# AIM lead-free profile supplement, no-clean column (Resources/
# aim-reflow-profile-supplement-lead-free.pdf); ramp_max is the IPC/JEDEC limit.
M8_SAC305 = Target(
    name="AIM M8 no-clean, SAC305",
    liquidus=217, ramp_avg=(1, 3), ramp_max=3, soak_min=150, soak_max=200,
    soak_time=(30, 90), tal=(30, 90), peak=(230, 250), t40_to_peak=(180, 270),
    cool_max=4)


def read_trace(path, probe, t_from=None, t_to=None):
    """[(elapsed s, PV)] for the probe, skipping open readings."""
    trace = []
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            if row["device"] != probe or row["state"] == "event":
                continue
            try:
                t, pv = float(row["elapsed_s"]), float(row["pv"])
            except ValueError:
                continue                       # no reply
            if row["note"] == "open" or pv > OPEN_PV:
                continue
            if (t_from is not None and t < t_from) or (t_to is not None and t > t_to):
                continue
            trace.append((t, pv))
    return trace


def crossing(trace, i, level):
    """Time at which the segment trace[i-1] -> trace[i] crosses level."""
    (t0, v0), (t1, v1) = trace[i - 1], trace[i]
    if v1 == v0:
        return t1
    return t0 + (level - v0) * (t1 - t0) / (v1 - v0)


def first_rise(trace, start, end, level):
    """Interpolated time trace first reaches level on [start, end], or None."""
    for i in range(max(start, 1), end + 1):
        if trace[i - 1][1] < level <= trace[i][1]:
            return crossing(trace, i, level)
    return None


def time_above(trace, start, end, level):
    total = 0.0
    for i in range(max(start, 1), end + 1):
        (t0, v0), (t1, v1) = trace[i - 1], trace[i]
        if v0 >= level and v1 >= level:
            total += t1 - t0
        elif v0 < level <= v1:
            total += t1 - crossing(trace, i, level)
        elif v1 < level <= v0:
            total += crossing(trace, i, level) - t0
    return total


def steepest(trace, start, end, sign):
    """Largest sign * slope over at least SLOPE_WINDOW s within [start, end]."""
    best = None
    j = start
    for i in range(start, end + 1):
        j = max(j, i)
        while j <= end and trace[j][0] - trace[i][0] < SLOPE_WINDOW:
            j += 1
        if j > end:
            break
        slope = sign * (trace[j][1] - trace[i][1]) / (trace[j][0] - trace[i][0])
        best = slope if best is None else max(best, slope)
    return best


def find_passes(trace, min_peak):
    """[(start index, peak index, end index)]: the probe rises through 40 °C
    from below and reaches at least min_peak before it falls below 40 °C
    again (or the trace ends)."""
    passes = []
    i = 1
    while i < len(trace):
        if trace[i - 1][1] < 40 <= trace[i][1]:
            j = i
            while j + 1 < len(trace) and trace[j + 1][1] >= 40:
                j += 1
            peak = max(range(i, j + 1), key=lambda k: (trace[k][1], -k))
            if trace[peak][1] >= min_peak:
                passes.append((i - 1, peak, j))
            i = j + 1
        else:
            i += 1
    return passes


def analyse(trace, start, peak, end, target=M8_SAC305):
    """Measured values for one pass, as {feature: value or None}."""
    tg = target
    t40 = first_rise(trace, start, peak, 40)
    tpk, vpk = trace[peak]
    ts_min = first_rise(trace, start, peak, tg.soak_min)
    ts_max = first_rise(trace, start, peak, tg.soak_max)
    return {
        "t40": t40,
        "t_peak": tpk,
        "peak": vpk,
        "t40_to_peak": tpk - t40 if t40 is not None else None,
        "ramp_avg": (tg.soak_min - 40) / (ts_min - t40)
                    if ts_min is not None and t40 is not None and ts_min > t40 else None,
        "ramp_max": steepest(trace, start, peak, +1),
        "soak_time": ts_max - ts_min if ts_min is not None and ts_max is not None else None,
        "tal": time_above(trace, start, end, tg.liquidus),
        "cool_max": steepest(trace, peak, end, -1),
        "cool_seen": trace[end][0] - tpk,
    }


def judge(value, window):
    """OK / LOW / HIGH for a (lo, hi) window, or a maximum."""
    if value is None:
        return "n/a"
    lo, hi = window if isinstance(window, tuple) else (None, window)
    if lo is not None and value < lo:
        return "LOW"
    if value > hi:
        return "HIGH"
    return "OK"


def report(m, target=M8_SAC305, out=print):
    tg = target

    def fmt(v, unit, nd=0):
        return "--" if v is None else f"{v:.{nd}f} {unit}"

    def win(w, unit):
        return f"{w[0]:g}-{w[1]:g} {unit}" if isinstance(w, tuple) else f"<= {w:g} {unit}"

    rows = [
        ("Peak", fmt(m["peak"], "°C"), win(tg.peak, "°C"), judge(m["peak"], tg.peak)),
        (f"Time above liquidus ({tg.liquidus:g} °C)", fmt(m["tal"], "s"), win(tg.tal, "s"),
         judge(m["tal"], tg.tal)),
        ("40 °C to peak", fmt(m["t40_to_peak"], "s"), win(tg.t40_to_peak, "s"),
         judge(m["t40_to_peak"], tg.t40_to_peak)),
        (f"Ramp, average 40-{tg.soak_min:g} °C", fmt(m["ramp_avg"], "°C/s", 2),
         win(tg.ramp_avg, "°C/s"), judge(m["ramp_avg"], tg.ramp_avg)),
        (f"Ramp, steepest {SLOPE_WINDOW:g} s", fmt(m["ramp_max"], "°C/s", 2),
         win(tg.ramp_max, "°C/s"), judge(m["ramp_max"], tg.ramp_max)),
        (f"Soak {tg.soak_min:g}-{tg.soak_max:g} °C (RSS only)", fmt(m["soak_time"], "s"),
         win(tg.soak_time, "s"), judge(m["soak_time"], tg.soak_time)),
        (f"Cooling, steepest {SLOPE_WINDOW:g} s", fmt(m["cool_max"], "°C/s", 2),
         win(tg.cool_max, "°C/s"), judge(m["cool_max"], tg.cool_max)),
    ]
    w = [max(len(r[k]) for r in rows) for k in range(3)]
    out(f"  {'Feature':<{w[0]}}  {'Measured':>{w[1]}}  {'Target':<{w[2]}}  Result")
    for r in rows:
        out(f"  {r[0]:<{w[0]}}  {r[1]:>{w[1]}}  {r[2]:<{w[2]}}  {r[3]}")
    if m["cool_seen"] < 60:
        out(f"  (only {m['cool_seen']:.0f} s of cooling after the peak in the log)")
    return all(r[3] in ("OK", "n/a") or "RSS" in r[0] for r in rows)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("log", help="run log CSV from ovenctl.py")
    ap.add_argument("--probe", default="T1", choices=["T1", "T2", "T3", "T4"])
    ap.add_argument("--from", dest="t_from", type=float, help="ignore readings before this elapsed s")
    ap.add_argument("--to", dest="t_to", type=float, help="ignore readings after this elapsed s")
    ap.add_argument("--min-peak", type=float, default=100,
                    help="a rise counts as a pass only if it reaches this °C (default 100)")
    a = ap.parse_args(argv)

    trace = read_trace(a.log, a.probe, a.t_from, a.t_to)
    if not trace:
        print(f"No {a.probe} readings in {a.log} (probe open or not polled?)")
        return 1
    passes = find_passes(trace, a.min_peak)
    print(f"{a.log}: {a.probe}, {len(trace)} readings, target {M8_SAC305.name}")
    if not passes:
        print(f"No pass found: {a.probe} never rose through 40 °C to {a.min_peak:g} °C or more. "
              f"Highest reading {max(v for _, v in trace):.0f} °C.")
        return 1
    all_ok = True
    for n, (s, p, e) in enumerate(passes, 1):
        m = analyse(trace, s, p, e)
        print(f"\nPass {n}: 40 °C at {m['t40']:.0f} s, peak at {m['t_peak']:.0f} s "
              f"(elapsed time in the log)")
        all_ok &= report(m)
    print("\nLimitation: the sensor card filters PV (a lag of a few seconds), so the peak and "
          "the steepest rates read a little low.")
    return 0 if all_ok else 2


if __name__ == "__main__":
    sys.exit(main())
