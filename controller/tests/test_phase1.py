import unittest

import phase1
from oven import protocol as pr

from .test_bus import make_bus


def quiet(*a, **k):
    pass


def commands(oven):
    """(addr, value) of every 0x1022 write, in order."""
    out = []
    for baud, f in oven.writes:
        if baud == 2400 and f[1] == 6 and (f[2] << 8 | f[3]) == pr.CMD:
            out.append((f[0], f[4] << 8 | f[5]))
    return out


def run(bus, zone=3, sv=60, seconds=0.05, **kw):
    return phase1.heat_test(bus, zone, sv, seconds, after=0, period=0, out=quiet, **kw)


class TestHeat(unittest.TestCase):
    def assert_all_off(self, oven):
        for addr in pr.ZONE_ADDRS:
            self.assertEqual(oven.cards[addr][pr.CMD], pr.CMD_HEAT_OFF, pr.device_name(addr))

    def test_normal_run(self):
        bus, oven = make_bus(read_only=False)
        self.assertIsNone(run(bus))
        self.assertEqual(oven.cards[53][pr.SV], 60)
        self.assertGreater(oven.cards[53][pr.PV], 20)
        cmds = commands(oven)
        on = cmds.index((53, pr.CMD_HEAT_ON))
        # every zone off before heat-on, heat-on sent once; after: zone 1, then target
        self.assertEqual({a for a, v in cmds[:on]}, set(pr.ZONE_ADDRS))
        self.assertEqual(cmds.count((53, pr.CMD_HEAT_ON)), 1)
        self.assertEqual(cmds[on + 1:on + 3], [(51, pr.CMD_HEAT_OFF), (53, pr.CMD_HEAT_OFF)])
        self.assert_all_off(oven)

    def test_sv_cap_and_zone_range(self):
        bus, oven = make_bus(read_only=False)
        with self.assertRaises(ValueError):
            run(bus, sv=phase1.MAX_TEST_SV + 1)
        with self.assertRaises(ValueError):
            run(bus, zone=13)
        self.assertEqual(oven.writes, [])

    def test_refuses_when_already_hot(self):
        bus, oven = make_bus(read_only=False)
        oven.cards[53][pr.PV] = 70
        with self.assertRaises(phase1.Abort):
            run(bus)
        self.assertEqual(commands(oven), [])

    def test_no_heat_on_if_sv_readback_wrong(self):
        bus, oven = make_bus(read_only=False)
        real = bus.read_registers
        bus.read_registers = lambda a, r, n: [99] if r == pr.SV else real(a, r, n)
        with self.assertRaises(phase1.Abort):
            run(bus)
        self.assertNotIn((53, pr.CMD_HEAT_ON), commands(oven))

    def test_overshoot_aborts(self):
        bus, oven = make_bus(read_only=False)
        oven.heat_step = 60                      # 20 -> 80 on the first read
        reason = run(bus, seconds=5)
        self.assertIn("over SV", reason)
        self.assert_all_off(oven)

    def test_other_zone_hot_aborts(self):
        bus, oven = make_bus(read_only=False)
        real = bus.read_registers

        def read(a, r, n):
            w = real(a, r, n)
            if a == 60 and r == pr.PV and oven.cards[53][pr.CMD] == pr.CMD_HEAT_ON:
                w[0] = phase1.HOT_ABORT
            return w
        bus.read_registers = read
        self.assertIn("Z10", run(bus, seconds=5))
        self.assert_all_off(oven)

    def test_target_silent_aborts(self):
        bus, oven = make_bus(read_only=False)
        real = bus.write_register

        def write(a, r, v):
            real(a, r, v)
            if v == pr.CMD_HEAT_ON:
                oven.silent.add(a)
        bus.write_register = write
        self.assertIn("stopped answering", run(bus, seconds=5))
        others = [a for a in pr.ZONE_ADDRS if a != 53]
        for a in others:
            self.assertEqual(oven.cards[a][pr.CMD], pr.CMD_HEAT_OFF)

    def test_ctrl_c_sends_heat_off(self):
        bus, oven = make_bus(read_only=False)
        real = bus.read_registers
        calls = []

        def read(a, r, n):
            if oven.cards[53][pr.CMD] == pr.CMD_HEAT_ON:
                calls.append(a)
                if len(calls) > 20:
                    raise KeyboardInterrupt
            return real(a, r, n)
        bus.read_registers = read
        with self.assertRaises(KeyboardInterrupt):
            run(bus, seconds=5)
        self.assert_all_off(oven)


    def test_enable_z1(self):
        bus, oven = make_bus(read_only=False)
        self.assertIsNone(run(bus, enable_z1=True))
        self.assertEqual(oven.cards[51][pr.SV], phase1.ENABLE_SV)
        self.assertEqual(oven.cards[51][pr.AL2], 400)
        self.assertEqual(oven.cards[51][pr.PV], 20)         # SV below PV: no heat
        cmds = commands(oven)
        self.assertLess(cmds.index((51, pr.CMD_HEAT_ON)), cmds.index((53, pr.CMD_HEAT_ON)))
        self.assert_all_off(oven)

    def test_after_phase_stops_when_bus_goes_silent(self):
        bus, oven = make_bus(read_only=False)
        real = bus.write_register
        lines = []

        def write(a, r, v):
            real(a, r, v)
            if a == 62 and v == pr.CMD_HEAT_OFF and oven.cards[53][pr.SV] == 60:
                oven.silent.update(pr.ZONE_ADDRS)   # card power drops after heat-off
        bus.write_register = write
        t = phase1.time.monotonic()
        phase1.heat_test(bus, 3, 60, 0.05, after=30, period=0, out=lines.append)
        self.assertLess(phase1.time.monotonic() - t, 10)
        self.assertTrue(any("bus silent" in x for x in lines))


class TestOther(unittest.TestCase):
    def test_off_all(self):
        bus, oven = make_bus(read_only=False)
        self.assertEqual(phase1.heat_off(bus, pr.ZONE_ADDRS), [])
        self.assertEqual(len(commands(oven)), 12)

    def test_off_reports_silent(self):
        bus, oven = make_bus(read_only=False)
        oven.silent.add(55)
        self.assertEqual(phase1.heat_off(bus, pr.ZONE_ADDRS, tries=1), [55])

    def test_lamp(self):
        bus, oven = make_bus(read_only=False)
        phase1.lamp(bus, True, out=quiet)
        self.assertEqual(oven.cards[51][pr.AL2], 400)
        phase1.lamp(bus, False, out=quiet)
        self.assertEqual(oven.cards[51][pr.AL2], 0)

    def test_vfd(self):
        bus, oven = make_bus(read_only=False)
        with self.assertRaises(ValueError):
            phase1.vfd_run(bus, phase1.MAX_HZ + 1, out=quiet)
        phase1.vfd_run(bus, 5.44, out=quiet)
        self.assertEqual(oven.vfd[pr.VFD_FREQ], 544)
        self.assertEqual(oven.vfd[pr.VFD_CMD], pr.VFD_RUN)
        phase1.vfd_stop(bus, out=quiet)
        self.assertEqual(oven.vfd[pr.VFD_CMD], pr.VFD_STOP)
        self.assertEqual(bus.mode, "card")


if __name__ == "__main__":
    unittest.main()
