import csv
import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout

import profile_report as pr_

# A board curve shaped like the old screenshot ("Loctite GC10 profile.PNG"):
# (time s, °C) corners, joined by straight lines.
SCREENSHOT = [(0, 25), (45, 40), (90, 120), (135, 180), (250, 195), (300, 235),
              (322, 252), (340, 240), (380, 180), (405, 115)]
# One inside the M8 window: 40 -> peak 195 s, soak 60 s, TAL ~68 s, peak 242.
GOOD = [(0, 25), (20, 40), (80, 150), (110, 175), (140, 200), (190, 225),
        (215, 242), (235, 225), (265, 190), (330, 120), (380, 60), (400, 35)]


def curve(corners, t):
    for (t0, v0), (t1, v1) in zip(corners, corners[1:]):
        if t0 <= t <= t1:
            return v0 + (v1 - v0) * (t - t0) / (t1 - t0)
    return corners[-1][1]


def write_log(path, corners, offset=100.0, period=2.5, probe="T1", extra=()):
    """A run log as ovenctl.py writes it: zone rows, other probes open, events."""
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["time", "elapsed_s", "state", "device", "pv", "out", "sv", "note"])
        w.writerow(["10:00:00.000", "5.0", "event", "", "", "", "", "HEAT ON: Z1"])
        t = 0.0
        while t <= offset + corners[-1][0] + 20:
            pv = curve(corners, t - offset) if t >= offset else corners[0][1]
            w.writerow(["10:00:00.000", f"{t:.1f}", "heat", "Z1", 180, 50, 180, ""])
            w.writerow(["10:00:00.000", f"{t:.1f}", "heat", probe, round(pv), 100, "", ""])
            w.writerow(["10:00:00.000", f"{t:.1f}", "heat", "T2", 1370, 0, "", "open"])
            t += period
        for row in extra:
            w.writerow(row)


class TestProfileReport(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "run.csv")

    def tearDown(self):
        self.dir.cleanup()

    def measure(self, corners, **kw):
        write_log(self.path, corners, **kw)
        trace = pr_.read_trace(self.path, "T1")
        passes = pr_.find_passes(trace, 100)
        self.assertEqual(len(passes), 1)
        return pr_.analyse(trace, *passes[0])

    def test_screenshot_curve(self):
        m = self.measure(SCREENSHOT)
        self.assertAlmostEqual(m["peak"], 252, delta=1)
        self.assertAlmostEqual(m["t40_to_peak"], 322 - 45, delta=3)
        self.assertAlmostEqual(m["soak_time"], 144, delta=3)   # 150 °C at 112 s, 200 at 256
        self.assertAlmostEqual(m["tal"], 78, delta=3)          # 217 °C from 277 s to 355 s
        self.assertEqual(pr_.judge(m["peak"], pr_.M8_SAC305.peak), "HIGH")
        self.assertEqual(pr_.judge(m["t40_to_peak"], pr_.M8_SAC305.t40_to_peak), "HIGH")

    def test_good_curve_passes(self):
        m = self.measure(GOOD)
        self.assertAlmostEqual(m["t40_to_peak"], 195, delta=3)
        self.assertAlmostEqual(m["tal"], 68, delta=3)
        self.assertAlmostEqual(m["soak_time"], 60, delta=3)
        self.assertAlmostEqual(m["ramp_avg"], 110 / 60, delta=0.1)
        self.assertLessEqual(m["ramp_max"], 3)
        self.assertLessEqual(m["cool_max"], 4)
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertTrue(pr_.report(m, out=print))
        self.assertNotIn("HIGH", out.getvalue())
        self.assertNotIn("LOW", out.getvalue())

    def test_fast_cooling_flagged(self):
        fast = GOOD[:7] + [(235, 150), (255, 40)]             # 4.6 °C/s down
        m = self.measure(fast)
        self.assertGreater(m["cool_max"], 4)

    def test_open_and_bad_readings_skipped(self):
        extra = [["10:00:00.000", "999.0", "heat", "T1", "", "", "", "no reply"],
                 ["10:00:00.000", "999.5", "heat", "T1", 1360, 0, "", "open"]]
        write_log(self.path, GOOD, extra=extra)
        trace = pr_.read_trace(self.path, "T1")
        self.assertTrue(all(v < 400 for _, v in trace))

    def test_two_passes(self):
        write_log(self.path, GOOD + [(450, 25), (470, 40)] +
                  [(t + 470, v) for t, v in GOOD[1:]])
        trace = pr_.read_trace(self.path, "T1")
        self.assertEqual(len(pr_.find_passes(trace, 100)), 2)

    def test_main_exit_codes(self):
        write_log(self.path, GOOD)
        with redirect_stdout(io.StringIO()):
            self.assertEqual(pr_.main([self.path]), 0)
        write_log(self.path, SCREENSHOT)
        with redirect_stdout(io.StringIO()):
            self.assertEqual(pr_.main([self.path]), 2)
        with redirect_stdout(io.StringIO()) as out:
            self.assertEqual(pr_.main([self.path, "--probe", "T2"]), 1)
        self.assertIn("No T2 readings", out.getvalue())


if __name__ == "__main__":
    unittest.main()
