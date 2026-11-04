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
        cases = [({}, None), ({13: 60}, None), ({3: 281}, None), ({3: 0}, None),
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

    def test_misses_are_logged(self):
        rows = []
        sup, oven = make()
        sup.log.row = lambda state, dev, vals, note="": rows.append((dev, note))
        oven.corrupt.add(55)
        sup.step()
        misses = [n for d, n in rows if d == "Z5" and n.startswith("miss:")]
        self.assertEqual(len(misses), Limits().read_tries)
        self.assertIn("CRC", misses[0])

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


class TestConveyor(unittest.TestCase):
    assert_all_off = TestStart.assert_all_off

    def heat_to(self, sup, oven, sv=40, **kw):
        sup.start({3: sv}, belt_hz=5, **kw)
        while oven.cards[53][pr.PV] < sv:
            sup.step()

    def test_belt_runs_before_any_card_setup(self):
        sup, oven = make()
        sup.start({3: 60}, belt_hz=5)
        self.assertIs(sup.state, State.HEATING)
        self.assertEqual(oven.vfd[pr.VFD_FREQ], 500)
        self.assertEqual(oven.vfd[pr.VFD_CMD], pr.VFD_RUN)
        frames = [f for _, f in oven.writes]
        run = frames.index(pr.vfd_write_request(pr.VFD_CMD, pr.VFD_RUN))
        self.assertLess(run, frames.index(pr.write_request(53, pr.SV, 60)))
        self.assertLess(run, frames.index(pr.write_request(51, pr.AL2, 400)))
        self.assertLess(run, frames.index(pr.write_request(51, pr.CMD, pr.CMD_HEAT_ON)))

    def test_belt_refusals_send_nothing_on(self):
        cases = [({3: 101}, None, None),                     # hot SV needs the belt
                 ({3: 60}, 16, None),                        # over max_belt_hz
                 ({3: 60}, 5, lambda o: setattr(o, "vfd_stuck", True)),
                 ({3: 60}, 5, lambda o: setattr(o, "vfd_silent", True)),
                 ({3: 60}, 5, lambda o: o.vfd.__setitem__(pr.VFD_ERROR, 3))]
        for setpoints, hz, prepare in cases:
            with self.subTest(setpoints=setpoints, hz=hz, prepare=prepare):
                sup, oven = make()
                if prepare:
                    prepare(oven)
                with self.assertRaises(Refused):
                    sup.start(setpoints, belt_hz=hz)
                self.assertEqual(heat_ons(oven), [])
                self.assertEqual(oven.cards[51][pr.AL2], 0)
                self.assertIs(sup.state, State.IDLE)

    def test_refused_start_stops_belt_on_cold_oven(self):
        sup, oven = make()
        oven.vfd[pr.VFD_ERROR] = 3
        with self.assertRaises(Refused):
            sup.start({3: 60}, belt_hz=5)
        self.assertEqual(oven.vfd[pr.VFD_CMD], pr.VFD_STOP)
        self.assertIsNone(sup.belt)

    def test_no_belt_allowed_at_low_sv(self):
        sup, oven = make()
        sup.start({3: 100})
        self.assertIs(sup.state, State.HEATING)
        self.assertEqual(oven.vfd[pr.VFD_CMD], 0)            # never written

    def test_belt_runs_after_stop_until_cool(self):
        sup, oven = make(limits=Limits(belt_cool_pv=30))
        self.heat_to(sup, oven)
        sup.stop()
        self.assert_all_off(oven)
        pvs = []
        while sup.belt is not None:
            self.assertEqual(oven.vfd[pr.VFD_CMD], pr.VFD_RUN)
            sup.step()
            pvs.append(oven.cards[53][pr.PV])
        self.assertEqual(oven.vfd[pr.VFD_CMD], pr.VFD_STOP)
        self.assertLess(pvs[-1], 30)
        self.assertGreaterEqual(pvs[-2], 30)                 # stopped on the first cool poll
        self.assertEqual(heat_ons(oven), [51, 53])           # stop is still final

    def test_open_thermocouple_keeps_belt_running(self):
        sup, oven = make(limits=Limits(belt_cool_pv=30))
        self.heat_to(sup, oven)
        sup.stop()
        oven.cards[60][pr.PV] = 1360
        for _ in range(40):
            sup.step()
        self.assertEqual(oven.vfd[pr.VFD_CMD], pr.VFD_RUN)
        self.assertEqual(sup.belt, 5)

    def test_belt_stopping_while_heating_trips(self):
        for name, prepare in [
                ("stopped", lambda o: o.vfd.__setitem__(pr.VFD_STATUS, 0x0500)),
                ("error", lambda o: o.vfd.__setitem__(pr.VFD_ERROR, 6)),
                ("frequency", lambda o: o.vfd.__setitem__(0x2102, 100))]:
            with self.subTest(name):
                sup, oven = make(limits=Limits(belt_cool_pv=10))   # below ambient
                sup.start({3: 60}, belt_hz=5)
                sup.step()
                self.assertIs(sup.state, State.HEATING)
                prepare(oven)
                sup.step()
                self.assertIs(sup.state, State.FAULT)
                self.assertIn("conveyor", sup.fault)
                self.assert_all_off(oven)
                self.assertEqual(sup.belt, 5)                # still ours to stop when cool

    def test_vfd_comms_loss_trips(self):
        sup, oven = make()
        sup.start({3: 60}, belt_hz=5)
        oven.vfd_silent = True
        for _ in range(Limits().comms_fail_polls - 1):
            sup.step()
        self.assertIs(sup.state, State.HEATING)              # misses are tolerated
        sup.step()
        self.assertIs(sup.state, State.FAULT)
        self.assertIn("VFD", sup.fault)
        self.assert_all_off(oven)

    def test_fault_keeps_belt_running_until_cool(self):
        sup, oven = make(limits=Limits(belt_cool_pv=30, overtemp_margin=5))
        self.heat_to(sup, oven, sv=40)
        oven.cards[53][pr.PV] = 50
        sup.step()
        self.assertIs(sup.state, State.FAULT)
        self.assertEqual(oven.vfd[pr.VFD_CMD], pr.VFD_RUN)
        while sup.belt is not None:
            sup.step()
        self.assertEqual(oven.vfd[pr.VFD_CMD], pr.VFD_STOP)


if __name__ == "__main__":
    unittest.main()
