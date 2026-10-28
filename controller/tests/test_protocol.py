"""Frame building/parsing checked against frames from the bus captures."""

import unittest

from oven import protocol as pr


class TestRtu(unittest.TestCase):
    def test_read_request_matches_capture(self):
        self.assertEqual(pr.read_request(0x33, pr.PV, 3).hex(" "), "33 03 10 00 00 03 05 19")
        self.assertEqual(pr.read_request(0x3E, pr.PV, 3).hex(" "), "3e 03 10 00 00 03 04 04")
        self.assertEqual(pr.read_request(0x33, pr.OUT, 2).hex(" "), "33 03 10 02 00 02 65 19")

    def test_write_request_matches_capture(self):
        self.assertEqual(pr.write_request(0x33, pr.CMD, pr.CMD_HEAT_ON).hex(" "),
                         "33 06 10 22 00 99 e9 78")
        self.assertEqual(pr.write_request(0x33, pr.CMD, pr.CMD_HEAT_OFF).hex(" "),
                         "33 06 10 22 00 cc 29 47")

    def test_pv_reply(self):
        reply = bytes.fromhex("33 03 06 00 0B 00 64 00 B4 88 7D")
        self.assertTrue(pr.crc_ok(reply))
        self.assertEqual(pr.words(reply[3:-2]), [11, 100, 180])

    def test_short_write_ack(self):
        self.assertTrue(pr.crc_ok(bytes.fromhex("3E 06 10 06 60 0F")))
        self.assertFalse(pr.crc_ok(bytes.fromhex("3E 06 10 06 60 0E")))


class TestAscii(unittest.TestCase):
    def test_vfd_frames_match_capture(self):
        self.assertEqual(pr.vfd_write_request(pr.VFD_CMD, pr.VFD_RUN), b":010620000002D7\r\n")
        self.assertEqual(pr.vfd_write_request(pr.VFD_FREQ, 0x0220), b":010620010220B6\r\n")

    def test_parse(self):
        self.assertEqual(pr.parse_ascii(b":010620010BB815\r\n"), bytes.fromhex("010620010BB8"))
        with self.assertRaises(ValueError):
            pr.parse_ascii(b":010620010BB816\r\n")
        with self.assertRaises(ValueError):
            pr.parse_ascii(b"010620010BB815\r\n")


if __name__ == "__main__":
    unittest.main()
