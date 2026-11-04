import unittest

from oven import protocol as pr
from oven.supervisor import ENABLE_SV, Limits, Refused, State, Supervisor

from .test_bus import make_bus
from .test_phase1 import commands, quiet


def make(**kw):
    bus, oven = make_bus(read_only=False)
    oven.require_contactor = True
    oven.cool_step = 1
    return Supervisor(bus, out=quiet, **kw), oven


def heat_ons(oven):
    return [a for a, v in commands(oven) if v == pr.CMD_HEAT_ON]


class TestStart(unittest.TestCase):
    def assert_all_off(self, oven):
        for addr in pr.ZONE_ADDRS:
            self.assertEqual(oven.cards[addr][pr.CMD], pr.CMD_HEAT_OFF, pr.device_name(addr))
        self.assertEqual(oven.cards[51][pr.AL2], 0)

    def test_start_sets_up_and_heats(self):
        sup, oven = make()
        sup.start({3: 60, 4: 70})
        self.assertIs(sup.state, State.HEATING)
        self.assertEqual(oven.cards[53][pr.SV], 60)
        self.assertEqual(oven.cards[54][pr.SV], 70)
        self.assertEqual(oven.cards[51][pr.SV], ENABLE_SV)
        self.assertEqual(oven.cards[51][pr.AL2], 400)
        # every zone off first, then zone 1 on before the others, each once
        cmds = commands(oven)
        first_on = cmds.index((51, pr.CMD_HEAT_ON))
        self.assertEqual({a for a, v in cmds[:first_on]}, set(pr.ZONE_ADDRS))
        self.assertEqual(heat_ons(oven), [51, 53, 54])
        for _ in range(5):
            sup.step()
        self.assertGreater(oven.cards[53][pr.PV], 20)
        self.assertEqual(oven.cards[51][pr.PV], 20)          # enable only

    def test_zone1_heated_keeps_its_sv(self):
        sup, oven = make()
        sup.start({1: 80, 2: 80})
        self.assertEqual(oven.cards[51][pr.SV], 80)
        self.assertEqual(heat_ons(oven), [51, 52])

    def test_refusals_send_nothing_on(self):
        cases = [({}, None), ({13: 60}, None), ({3: 151}, None), ({3: 0}, None),
                 ({3: 60}, lambda o: o.silent.add(60)),
                 ({3: 60}, lambda o: o.cards[57].__setitem__(pr.PV, 90)),
                 ({3: 60}, lambda o: o.cards[51].__setitem__(pr.PV, 1360)),
                 ({3: 60}, lambda o: o.cards[51].__setitem__(pr.PV, ENABLE_SV))]
        for setpoints, prepare in cases:
            with self.subTest(setpoints=setpoints, prepare=prepare):
                sup, oven = make()
                if prepare:
                    prepare(oven)
                with self.assertRaises(Refused):
                    sup.start(setpoints)
                self.assertEqual(heat_ons(oven), [])
                self.assertIs(sup.state, State.IDLE)
                self.assertEqual(sup.setpoints, {})

    def test_sv_readback_failure_refuses(self):
        sup, oven = make()
        real = sup.bus.read_registers
        sup.bus.read_registers = lambda a, r, n: [99] if r == pr.SV else real(a, r, n)
        with self.assertRaises(Refused):
            sup.start({3: 60})
        self.assertEqual(heat_ons(oven), [])

    def test_start_while_heating_refused(self):
        sup, oven = make()
        sup.start({3: 60})
        with self.assertRaises(Refused):
            sup.start({3: 60})
        self.assertEqual(heat_ons(oven), [51, 53])

    def test_heat_on_failure_trips(self):
        sup, oven = make()
        real = sup.bus.write_register

        def write(a, r, v):
            if a == 53 and v == pr.CMD_HEAT_ON:
                oven.drop[53] = 10
            real(a, r, v)
        sup.bus.write_register = write
        sup.start({3: 60})
        self.assertIs(sup.state, State.FAULT)
        self.assertIn("heat-on failed", sup.fault)
        self.assertEqual(oven.cards[51][pr.CMD], pr.CMD_HEAT_OFF)


class TestStop(unittest.TestCase):
    assert_all_off = TestStart.assert_all_off

    def test_stop_is_final(self):
        sup, oven = make()
        sup.start({3: 40})
        while oven.cards[53][pr.PV] < 40:
            sup.step()
        sup.stop()
        self.assertIs(sup.state, State.IDLE)
        self.assert_all_off(oven)
        offs = [a for a, v in commands(oven) if v == pr.CMD_HEAT_OFF]
        self.assertEqual(offs[-12:][:2], [51, 53])           # zone 1 first, then the run
        for _ in range(30):                                  # cools well below SV
            sup.step()
        self.assertLess(oven.cards[53][pr.PV], 30)
        self.assertEqual(heat_ons(oven), [51, 53])

    def test_overtemp_on_heating_zone(self):
        sup, oven = make(limits=Limits(overtemp_margin=10))
        sup.start({3: 60})
        oven.cards[53][pr.PV] = 70
        sup.step()
        self.assertIs(sup.state, State.FAULT)
        self.assertIn("Z3 PV 70", sup.fault)
        self.assert_all_off(oven)

    def test_overtemp_on_other_zone_uses_highest_sv(self):
        sup, oven = make(limits=Limits(overtemp_margin=10))
        sup.start({3: 60, 4: 80})
        oven.cards[60][pr.PV] = 85                           # under 80 + 10: fine
        sup.step()
        self.assertIs(sup.state, State.HEATING)
        oven.cards[60][pr.PV] = 95
        sup.step()
        self.assertIs(sup.state, State.FAULT)
        self.assertIn("Z10", sup.fault)

    def test_open_thermocouple_trips(self):
        sup, oven = make()
        sup.start({3: 60})
        oven.cards[56][pr.PV] = 1360
        sup.step()
        self.assertIs(sup.state, State.FAULT)
        self.assertIn("open thermocouple", sup.fault)

    def test_single_misses_tolerated_and_counted(self):
        sup, oven = make()
        sup.start({3: 60})
        for _ in range(4):
            oven.drop[55] = 1                                # one miss, retry succeeds
            sup.step()
        oven.drop[58] = 2                                    # one failed poll
        sup.step()
        self.assertIs(sup.state, State.HEATING)
        self.assertEqual(sup.stats["Z5"]["missed"], 4)
        self.assertEqual(sup.stats["Z5"]["retried"], 4)
        self.assertEqual(sup.stats["Z5"]["failed"], 0)
        self.assertEqual(sup.stats["Z8"]["failed"], 1)

    def test_comms_loss_trips_and_retries_heat_off(self):
        sup, oven = make()
        sup.start({3: 60})
        oven.silent.add(55)
        for _ in range(Limits().comms_fail_polls):
            sup.step()
        self.assertIs(sup.state, State.FAULT)
        self.assertIn("Z5", sup.fault)
        self.assertEqual([z.name for z in sup.pending_off], ["Z5"])
        with self.assertRaises(Refused):
            sup.reset()                                      # Z5 off not confirmed
        oven.silent.clear()
        sup.step()
        self.assertEqual(sup.pending_off, [])
        self.assert_all_off(oven)
        sup.reset()
        self.assertIs(sup.state, State.IDLE)
        sup.start({3: 60})                                   # a new explicit start works
        self.assertIs(sup.state, State.HEATING)

    def test_fault_is_latched(self):
        sup, oven = make()
        sup.start({3: 60})
        oven.cards[53][pr.PV] = 200
        sup.step()
        sup.stop()
        self.assertIs(sup.state, State.FAULT)
        with self.assertRaises(Refused):
            sup.start({3: 60})

    def test_bus_silent_while_idle_is_not_a_fault(self):
        sup, oven = make()
        oven.silent.update(pr.ZONE_ADDRS)
        sup.step()
        self.assertTrue(sup.bus_silent)
        self.assertIs(sup.state, State.IDLE)

    def test_sensors_and_vfd_polled(self):
        sup, oven = make(sensors=True, vfd_every=2)
        oven.cards[72][pr.PV], oven.cards[72][pr.OUT] = 1360, 0
        r = sup.step()
        self.assertTrue(r["T2"].open)
        self.assertFalse(r["T1"].open)
        self.assertEqual(r["VFD"]["state"], "stopped")
        self.assertNotIn("VFD", sup.step())
        self.assertIn("VFD", sup.step())
        self.assertEqual(sup.bus.mode, "card")


if __name__ == "__main__":
    unittest.main()
