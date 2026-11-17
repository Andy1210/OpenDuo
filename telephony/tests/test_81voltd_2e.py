"""81voltd patch 0006: request 0x2E is answered with success plus TLV 0x10 =
u32 0x5F, byte for byte as in Android's imsdatadaemon trace
(codeberg.org/DylanVanAssche/imsd, IMS-QUALCOMM.md, "Message ID 0x002E").
Host-side only: no phone. python3 -m unittest discover -s tests (telephony/)."""

import glob
import os
import struct
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
PATCH = glob.glob(os.path.join(HERE, "..", "81voltd", "patches", "0006-*.patch"))[0]


def tlv(t, value):
    return struct.pack("<BH", t, len(value)) + value


def expected_response(txn):
    body = tlv(2, struct.pack("<HH", 0, 0)) + tlv(0x10, struct.pack("<I", 0x5F))
    return struct.pack("<BHHH", 2, txn, 0x2E, len(body)) + body


class Test2e(unittest.TestCase):
    def test_trace_bytes(self):
        trace = bytes.fromhex("02 03 00 2E 00 0E 00 02 04 00 00 00 00 00 10 04 00 5F 00 00 00")
        self.assertEqual(expected_response(3), trace)
        self.assertEqual(
            trace[7:], bytes.fromhex("0204000000000010 0400 5f000000".replace(" ", ""))
        )

    def test_patch_encodes_that(self):
        p = open(PATCH).read()
        self.assertIn("= 0x2E;", p)  # message id in imsd.qmi
        self.assertIn(".tlv_type = 16,", p)  # TLV 0x10 ...
        self.assertIn("QMI_UNSIGNED_4_BYTE", p)  # ... u32
        self.assertIn(".unkfield_5F = 0x5F,", p)

    def test_only_2e_changed(self):
        p = open(PATCH).read()
        self.assertIn("msg_id == IMSD_UNK2E", p)
        self.assertIn("process_no_op(msg_id, msg);", p)  # every other id: fallback


if __name__ == "__main__":
    unittest.main()
