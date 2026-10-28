import unittest

from oven import protocol as pr
from oven.bus import Bus, BadFrame, NoReply

from .fake_oven import FakeOven


def make_bus(read_only=True, echo=False):
    fakes = []

    def factory(port, **kw):
        fakes.append(FakeOven(port, echo=echo, **kw))
        return fakes[0]

    bus = Bus("fake", read_only=read_only, timeout=0.05, gap=0, echo=echo,
              serial_factory=factory)
    return bus, fakes[0]


class TestCards(unittest.TestCase):
    def test_read_status(self):
        bus, oven = make_bus()
        oven.cards[51][pr.PV] = 11
        self.assertEqual(bus.read_registers(51, pr.PV, 3), [11, 100, 150])
        self.assertEqual(bus.read_registers(71, pr.ADDR, 2), [71, 100])

    def test_writes_refused_when_read_only(self):
        bus, oven = make_bus()
        with self.assertRaises(PermissionError):
            bus.write_register(51, pr.SV, 120)
        with self.assertRaises(PermissionError):
            bus.vfd_write(pr.VFD_CMD, pr.VFD_RUN)
        self.assertEqual(oven.writes, [])

    def test_write_with_short_ack(self):
        bus, oven = make_bus(read_only=False)
        bus.write_register(53, pr.SV, 120)
        self.assertEqual(oven.cards[53][pr.SV], 120)

    def test_no_reply(self):
        bus, oven = make_bus()
        oven.silent.add(52)
        with self.assertRaises(NoReply):
            bus.read_registers(52, pr.PV, 3)
        self.assertEqual(bus.stats["no_reply"], 1)
        self.assertEqual(bus.read_registers(53, pr.PV, 3)[0], 20)

    def test_crc_error(self):
        bus, oven = make_bus()
        oven.corrupt.add(54)
        with self.assertRaises(BadFrame):
            bus.read_registers(54, pr.PV, 3)
        self.assertEqual(bus.stats["bad_frame"], 1)


class TestVfd(unittest.TestCase):
    def test_read_status_and_switch_back(self):
        bus, oven = make_bus()
        oven.vfd[0x2103] = 544
        status = bus.vfd_read(pr.VFD_ERROR, 7)
        self.assertEqual(status[1], 0x0500)
        self.assertEqual(status[3], 544)
        self.assertEqual(bus.mode, "card")
        self.assertEqual(oven.settings["baudrate"], 2400)
        self.assertEqual(bus.read_registers(51, pr.PV, 3)[0], 20)

    def test_write(self):
        bus, oven = make_bus(read_only=False)
        bus.vfd_write(pr.VFD_FREQ, 544)
        self.assertEqual(oven.vfd[pr.VFD_FREQ], 544)
        self.assertEqual(oven.writes[-1], (9600, b":010620010220B6\r\n"))


class TestEcho(unittest.TestCase):
    """Adapters that return our own transmitted bytes before the reply."""

    def test_detect(self):
        bus, oven = make_bus(echo=False)
        oven.echo = True
        self.assertTrue(bus.detect_echo(51)[0])

    def test_card_and_vfd_with_echo(self):
        bus, oven = make_bus(read_only=False, echo=True)
        self.assertEqual(bus.read_registers(51, pr.PV, 3)[0], 20)
        bus.write_register(51, pr.SV, 140)
        bus.vfd_write(pr.VFD_CMD, pr.VFD_RUN)     # reply identical to the echo
        self.assertEqual(bus.vfd_read(pr.VFD_ERROR, 7)[1], 0x0500)
        self.assertEqual(bus.read_registers(52, pr.PV, 3)[0], 20)


if __name__ == "__main__":
    unittest.main()
