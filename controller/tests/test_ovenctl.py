import unittest

import ovenctl
from oven import protocol as pr
from oven.supervisor import State

from .test_phase1 import commands, quiet
from .test_supervisor import heat_ons, make


class TestOvenctl(unittest.TestCase):
    def test_parse_zones(self):
        self.assertEqual(ovenctl.parse_zones("all"), list(range(1, 13)))
        self.assertEqual(ovenctl.parse_zones("2,4,6-8"), [2, 4, 6, 7, 8])
        self.assertEqual(ovenctl.parse_zones("3"), [3])

    def test_heat_run(self):
        sup, oven = make()
        self.assertIsNone(ovenctl.heat(sup, {2: 30, 3: 30}, 0.3, 0, period=0, out=quiet))
        self.assertIs(sup.state, State.IDLE)
        self.assertEqual(heat_ons(oven), [51, 52, 53])
        self.assertEqual(commands(oven)[-12][0], 51)          # final heat-off: zone 1 first

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
