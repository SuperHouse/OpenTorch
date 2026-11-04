import tempfile
import unittest
from pathlib import Path

import ovenctl
from oven import protocol as pr
from oven.supervisor import Limits, State

from .test_phase1 import commands, quiet
from .test_supervisor import heat_ons, make


class TestOvenctl(unittest.TestCase):
    def test_parse_zones(self):
        self.assertEqual(ovenctl.parse_zones("all"), list(range(1, 13)))
        self.assertEqual(ovenctl.parse_zones("2,4,6-8"), [2, 4, 6, 7, 8])
        self.assertEqual(ovenctl.parse_zones("3"), [3])

    def test_load_profile(self):
        path = Path(ovenctl.__file__).parent / "profiles" / "loctite-gc10.toml"
        name, setpoints, hz = ovenctl.load_profile(path)
        self.assertEqual(name, "Loctite GC10")
        self.assertEqual(sorted(setpoints), list(range(1, 13)))
        self.assertEqual(setpoints[12], 260)
        self.assertEqual(hz, 5.44)

    def test_load_profile_rejects_bad_files(self):
        with tempfile.TemporaryDirectory() as d:
            for text in ["setpoints = [100, 100]",
                         "setpoints = [100, 100, 100, 100, 100, 100, 100, 100, 100, 100, 100, -1]",
                         "setpoints = [100, 100, 100, 100, 100, 100, 100, 100, 100, 100, 100, 1.5]",
                         'belt_hz = "fast"\nsetpoints = [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]']:
                with self.subTest(text):
                    path = Path(d) / "p.toml"
                    path.write_text(text)
                    with self.assertRaises(ValueError):
                        ovenctl.load_profile(path)
            path.write_text("setpoints = [0, 120, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]")
            self.assertEqual(ovenctl.load_profile(path), ("p", {2: 120}, None))

    def test_heat_run(self):
        sup, oven = make()
        self.assertIsNone(ovenctl.heat(sup, {2: 30, 3: 30}, 0.3, 0, period=0, out=quiet))
        self.assertIs(sup.state, State.IDLE)
        self.assertEqual(heat_ons(oven), [51, 52, 53])
        self.assertEqual(commands(oven)[-12][0], 51)          # final heat-off: zone 1 first

    def test_heat_run_waits_for_belt_cool_down(self):
        sup, oven = make(limits=Limits(belt_cool_pv=25))
        self.assertIsNone(ovenctl.heat(sup, {3: 30}, 0.3, 0, period=0, out=quiet, belt_hz=5))
        self.assertIsNone(sup.belt)
        self.assertEqual(oven.vfd[pr.VFD_CMD], pr.VFD_STOP)
        self.assertLess(max(oven.cards[a][pr.PV] for a in pr.ZONE_ADDRS), 25)

    def test_ctrl_c_in_cool_down_leaves_belt_running(self):
        sup, oven = make(limits=Limits(belt_cool_pv=25))
        real = sup.step
        n = []

        def step():
            n.append(1)
            if sup.state is State.IDLE and sup.belt is not None and len(n) > 30:
                raise KeyboardInterrupt
            return real()
        sup.step = step
        events = []
        sup.out = events.append
        ovenctl.heat(sup, {3: 40}, 0.5, 0, period=0, out=quiet, belt_hz=5)
        self.assertEqual(oven.vfd[pr.VFD_CMD], pr.VFD_RUN)
        self.assertTrue(any("CONVEYOR LEFT RUNNING" in str(e) for e in events))
        for addr in pr.ZONE_ADDRS:
            self.assertEqual(oven.cards[addr][pr.CMD], pr.CMD_HEAT_OFF)

    def test_idle_shutdown_during_cool_down(self):
        sup, oven = make(limits=Limits(belt_cool_pv=10))      # never cool enough
        real = sup.step

        def step():
            if sup.state is State.IDLE and sup.belt is not None and sup.polls > 20:
                oven.silent.update(pr.ZONE_ADDRS)             # the oven shuts down
                oven.vfd_silent = True
            return real()
        sup.step = step
        events = []
        sup.out = events.append
        ovenctl.heat(sup, {3: 40}, 0.2, 0, period=0, out=quiet, belt_hz=5)
        self.assertTrue(any("idle shutdown" in e and "hottest zone" in e for e in events))

    def test_fault_ends_hold(self):
        sup, oven = make()
        oven.heat_step = 70                                   # 20 -> 90 on the first read
        fault = ovenctl.heat(sup, {3: 60}, 30, 0, period=0, out=quiet)
        self.assertIn("over-temperature: Z3 PV 90", fault)
        self.assertIs(sup.state, State.FAULT)
        self.assertEqual(oven.cards[51][pr.CMD], pr.CMD_HEAT_OFF)

    def test_ctrl_c_sends_heat_off(self):
        sup, oven = make()
        real = sup.step
        n = []

        def step():
            n.append(1)
            if len(n) > 5:
                raise KeyboardInterrupt
            return real()
        sup.step = step
        with self.assertRaises(KeyboardInterrupt):
            ovenctl.heat(sup, {3: 60}, 30, 0, period=0, out=quiet)
        for addr in pr.ZONE_ADDRS:
            self.assertEqual(oven.cards[addr][pr.CMD], pr.CMD_HEAT_OFF)
        self.assertEqual(oven.cards[51][pr.AL2], 0)


if __name__ == "__main__":
    unittest.main()
