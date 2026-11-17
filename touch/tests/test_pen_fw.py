"""pen-fw (touch/tools/pen-fw.py): everything that does not need the pen. Offers,
payloads and descriptors here are synthetic (the real firmware never enters git):
the parsers, the packet builders, the plan, the CFU transaction against a fake
pen, the BlueZ / sysfs checks and a Windows catalog signed by a throwaway CA.

    touch/tests/run.sh   (or: python3 -m unittest discover -s touch/tests)
"""

import datetime
import hashlib
import importlib.util
import io
import os
import pathlib
import struct
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

TOOL = pathlib.Path(__file__).resolve().parents[1] / "tools/pen-fw.py"
_spec = importlib.util.spec_from_file_location("pen_fw", TOOL)
fw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fw)

try:
    import cryptography  # noqa: F401

    HAVE_CRYPTO = True
except ImportError:
    HAVE_CRYPTO = False


def offer_bytes(
    component=0x11,
    version=0x0500968B,
    bank=0,
    milestone=5,
    platform=0x43,
    flags=0,
    protocol=4,
    mask=1,
    token=0,
):
    return (
        bytes([0, flags, component, token])
        + struct.pack("<II", version, mask)
        + bytes([protocol | (bank << 4), milestone])
        + struct.pack("<H", platform)
    )


def payload_bytes(records):
    return b"".join(struct.pack("<IB", a, len(d)) + d for a, d in records)


def image_records(size, step=52):
    data = bytes(i & 0xFF for i in range(size))
    return [(a, data[a : a + step]) for a in range(0, size, step)]


# --- a small HID descriptor builder, shaped like the pen's (keyboard + two FF0B collections)
def item(kind, tag, value, size=None):
    if size is None:
        size = 0 if value == 0 and tag != 0 else 1 if value < 0x100 else 2 if value < 0x10000 else 4
    code = {0: 0, 1: 1, 2: 2, 4: 3}[size]
    return bytes([(tag << 4) | (kind << 2) | code]) + value.to_bytes(size, "little")


def main_(tag, v):
    return item(0, tag, v, 1)


def glob(tag, v, size=None):
    return item(1, tag, v, size)


def loc(tag, v):
    return item(2, tag, v)


INPUT, OUTPUT, FEATURE, COLL, END = 8, 9, 11, 10, 12


def pen_like_descriptor(skip_content_in=False):
    d = (
        glob(0, 0x01)
        + loc(0, 0x06)
        + main_(COLL, 1)
        + glob(8, 0x01)
        + glob(7, 8)
        + glob(9, 1)
        + main_(INPUT, 2)
        + item(0, END, 0, 0)
    )
    d += (
        glob(0, 0xFF0B, 2)
        + loc(0, 0x0100)
        + main_(COLL, 1)
        + glob(8, 0x30)
        + glob(9, 1)
        + glob(7, 8)
        + loc(0, 0)
        + main_(OUTPUT, 2)
        + item(0, END, 0, 0)
    )
    d += (
        loc(0, 0x0101)
        + main_(COLL, 1)
        + glob(7, 8)
        + glob(9, 0x3C)
        + glob(8, 0x2A)
        + loc(0, 0xC6)
        + main_(INPUT, 2)
        + loc(0, 0xC7)
        + main_(OUTPUT, 2)
        + loc(0, 0xC8)
        + main_(FEATURE, 3)
    )
    d += glob(7, 0x20) + glob(9, 4)
    if not skip_content_in:
        d += glob(8, 0x2C) + loc(1, 0xCC) + loc(2, 0xCF) + main_(INPUT, 2)
    d += (
        glob(8, 0x2D)
        + loc(1, 0xD8)
        + loc(2, 0xDB)
        + main_(INPUT, 2)
        + loc(1, 0xDC)
        + loc(2, 0xDF)
        + main_(OUTPUT, 2)
    )
    d += item(0, END, 0, 0)
    return d


class Versions(unittest.TestCase):
    def test_format_and_parse(self):
        self.assertEqual(fw.fmt_version(0x0500968B), "5.150.139")
        self.assertEqual(fw.parse_version("5.150.139"), 0x0500968B)
        self.assertEqual(fw.parse_version("0x05008E8B"), 0x05008E8B)
        for bad in ("5.150", "256.0.0", "1.65536.0", "x"):
            with self.assertRaises(ValueError):
                fw.parse_version(bad)

    def test_variant(self):
        self.assertEqual(fw.describe_variant(0x8B), "official release, production-signed")
        self.assertEqual(fw.describe_variant(0x00), "debug, unsigned-signed")


class Offers(unittest.TestCase):
    def test_parse(self):
        o = fw.Offer.parse(offer_bytes(component=0x10, version=0x05008E8B, bank=1, milestone=3))
        self.assertEqual(
            (o.component, o.version, o.bank, o.milestone, o.platform, o.protocol, o.compat_mask),
            (0x10, 0x05008E8B, 1, 3, 0x43, 4, 1),
        )

    def test_bad(self):
        with self.assertRaises(fw.CfuError):
            fw.Offer.parse(bytes(15))
        with self.assertRaises(fw.CfuError):
            fw.Offer.parse(offer_bytes(component=0xFF))

    def test_report_sets_our_token_and_refuses_force(self):
        raw = offer_bytes(token=0x00)
        rep = fw.Offer.parse(raw).report()
        self.assertEqual(rep[3], fw.HOST_TOKEN)
        self.assertEqual(rep[:3] + rep[4:], raw[:3] + raw[4:])
        for flag in (0x40, 0x80):
            with self.assertRaises(fw.CfuError):
                fw.Offer.parse(offer_bytes(flags=flag)).report()


class Payloads(unittest.TestCase):
    def test_parse_and_check(self):
        recs = image_records(200)
        self.assertEqual(fw.parse_payload(payload_bytes(recs)), recs)
        self.assertEqual(fw.check_image(recs), 200)

    def test_bad_payload(self):
        with self.assertRaises(fw.CfuError):
            fw.parse_payload(b"\x00\x00\x00")  # trailing bytes
        with self.assertRaises(fw.CfuError):
            fw.parse_payload(struct.pack("<IB", 0, 0))  # an empty record
        with self.assertRaises(fw.CfuError):
            fw.parse_payload(struct.pack("<IB", 0, 9) + b"short")
        with self.assertRaises(fw.CfuError):
            fw.check_image([(0, b"a" * 52), (60, b"b")])  # a gap

    def test_chunks_like_microsofts_driver(self):
        chunks = fw.payload_chunks(image_records(130))  # 52 + 52 + 26
        self.assertEqual(
            [(f, a, len(d)) for f, a, d in chunks],
            [(fw.FLAG_FIRST, 0, 52), (0, 52, 52), (fw.FLAG_LAST, 104, 26)],
        )
        small = [(a, bytes([a]) * 10) for a in range(0, 100, 10)]  # contiguous 10-byte records
        chunks = fw.payload_chunks(small)
        self.assertEqual([len(d) for _, _, d in chunks], [52, 48])
        self.assertEqual(b"".join(d for _, _, d in chunks), b"".join(d for _, d in small))
        gap = fw.payload_chunks([(0, b"a" * 10), (100, b"b" * 10)])
        self.assertEqual([(a, len(d)) for _, a, d in gap], [(0, 10), (100, 10)])
        one = fw.payload_chunks([(0, b"x")])
        self.assertEqual(one[0][0], fw.FLAG_FIRST | fw.FLAG_LAST)


class Reports(unittest.TestCase):
    def test_content(self):
        b = fw.build_content(1, fw.FLAG_FIRST, 0x34, b"\xaa" * 52)
        self.assertEqual(len(b), 60)
        self.assertEqual(b[:8], bytes([0x80, 52, 1, 0, 0x34, 0, 0, 0]))
        b = fw.build_content(0x1234, fw.FLAG_LAST, 0x1BF48, b"\x01\x02")
        self.assertEqual(b[:10], bytes([0x40, 2, 0x34, 0x12, 0x48, 0xBF, 0x01, 0, 1, 2]))
        self.assertEqual(b[10:], bytes(50))
        for bad in (b"", b"x" * 53):
            with self.assertRaises(fw.CfuError):
                fw.build_content(1, 0, 0, bad)

    def test_info_and_command(self):
        self.assertEqual(fw.build_info(fw.INFO_START_LIST), bytes([1, 0, 0xFF, 0xA0]) + bytes(12))
        self.assertEqual(
            fw.build_command(fw.CMD_NOTIFY_ON_READY), bytes([1, 0, 0xFE, 0xA0]) + bytes(12)
        )

    def test_responses(self):
        r = bytes([0, 0, 0, 0xA0, 0, 0, 0, 0, 0x04, 0, 0, 0, 0x02, 0, 0, 0])
        self.assertEqual(fw.parse_offer_response(r), (0xA0, 0x04, fw.ST_REJECT))
        self.assertEqual(
            fw.parse_content_response(struct.pack("<HHB", 7, 0, 6) + bytes(11)), (7, 6)
        )
        with self.assertRaises(fw.CfuError):
            fw.parse_offer_response(bytes(4))

    def test_version_response(self):
        b = struct.pack("<I", 3 | (2 << 24))
        b += struct.pack("<IBBH", 0x0500948B, 0x51, 0x11, 0x43)  # bank 1, vendor 5
        b += struct.pack("<IBBH", 0x0500858B, 0x30, 0x10, 0x43)
        b += struct.pack("<IBBH", 0x0500758B, 0x50, 0x13, 0x43)
        b += bytes(60 - len(b))
        r = fw.parse_version_response(b)
        self.assertEqual((r["count"], r["protocol"], r["extension"]), (3, 2, 0))
        c = r["components"][0]
        self.assertEqual(
            (c.version, c.bank, c.vendor, c.component, c.platform), (0x0500948B, 1, 5, 0x11, 0x43)
        )
        self.assertEqual(
            fw.versions_by_component(r),
            {0x11: [0x0500948B], 0x10: [0x0500858B], 0x13: [0x0500758B]},
        )
        with self.assertRaises(fw.CfuError):
            fw.parse_version_response(struct.pack("<I", 8) + bytes(56))  # 8 do not fit


class Descriptor(unittest.TestCase):
    def test_finds_the_cfu_reports_by_usage(self):
        r = fw.find_cfu_reports(pen_like_descriptor())
        self.assertEqual(
            r,
            {
                "version": (0x2A, 60),
                "content_out": (0x2A, 60),
                "content_in": (0x2C, 16),
                "offer_in": (0x2D, 16),
                "offer_out": (0x2D, 16),
            },
        )

    def test_missing_report(self):
        with self.assertRaises(fw.CfuError):
            fw.find_cfu_reports(pen_like_descriptor(skip_content_in=True))

    def test_not_in_the_cfu_collection(self):
        # the same usages outside a 0xFF0B/0x0101 collection are not the CFU interface
        d = (
            glob(0, 0xFF0B, 2)
            + loc(0, 0x0102)
            + main_(COLL, 1)
            + glob(7, 8)
            + glob(9, 60)
            + glob(8, 0x2A)
            + loc(0, 0xC8)
            + main_(FEATURE, 2)
            + item(0, END, 0, 0)
        )
        with self.assertRaises(fw.CfuError):
            fw.find_cfu_reports(d)

    def test_truncated(self):
        with self.assertRaises(fw.CfuError):
            fw.parse_report_descriptor(b"\x06\x0b")


class Plan(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        d = pathlib.Path(self.dir.name)
        for name, comp, ver, bank in (
            ("X_HAPTIC", 0x13, 0x0500758B, 0),
            ("X_MCU-B0", 0x10, 0x05008E8B, 0),
            ("X_BLE-LO", 0x11, 0x0500968B, 0),
            ("X_BLE-HI", 0x11, 0x0500968B, 1),
        ):
            (d / (name + ".offer.bin")).write_bytes(
                offer_bytes(component=comp, version=ver, bank=bank)
            )
            (d / (name + ".payload.bin")).write_bytes(payload_bytes(image_records(120)))
        self.d = d

    def tearDown(self):
        self.dir.cleanup()

    def test_order_labels_and_unknown(self):
        pairs = fw.load_pairs([str(self.d)])
        self.assertEqual([p.label for p in pairs], ["BLE-HI", "BLE-LO", "MCU-B0", "HAPTIC"])
        self.assertTrue(all(p.known == "" for p in pairs))  # synthetic: not a Microsoft set

    def test_missing_payload(self):
        (self.d / "X_MCU-B0.payload.bin").unlink()
        with self.assertRaises(fw.CfuError):
            fw.load_pairs([str(self.d)])

    def test_only_newer(self):
        pairs = fw.load_pairs([str(self.d)])
        plan = fw.plan_offers(
            pairs, {0x11: [0x0500948B, 0x0500968B], 0x10: [0x0500858B], 0x13: [0x0500758B]}
        )
        todo = [p.label for p, why in plan if why is None]
        self.assertEqual(todo, ["MCU-B0"])  # BLE: one bank already has 5.150; HAPTIC the same
        plan = fw.plan_offers(pairs, {0x11: [0x0500948B]})
        self.assertEqual([p.label for p, why in plan if why is None], ["BLE-HI", "BLE-LO"])
        self.assertIn("no such component", dict((p.label, w) for p, w in plan)["MCU-B0"])

    def test_update_refuses_an_unknown_set(self):
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(out):
            rc = fw.main(["update", "--fw", str(self.d), "--current", "0x11=5.148.139"])
        self.assertEqual(rc, 1)
        self.assertIn("not a known Microsoft firmware set", out.getvalue())


class FakePen:
    """Answers like a pen: info packets accepted, offers by a table, content in order."""

    def __init__(self, offer_answers, content_status=None, wrong_token=False):
        self.reports = {
            "offer_out": (0x2D, 16),
            "offer_in": (0x2D, 16),
            "content_out": (0x2A, 60),
            "content_in": (0x2C, 16),
            "version": (0x2A, 60),
        }
        self.offer_answers = list(offer_answers)
        self.content_status = content_status or {}
        self.wrong_token = wrong_token
        self.sent, self.queue = [], []

    def drain(self):
        self.queue.clear()

    def write(self, role, data):
        self.sent.append((role, data))
        if role == "offer_out":
            token = 0x11 if self.wrong_token else data[3]
            if data[2] == 0xFF:
                status, rr = fw.ST_ACCEPT, 0
            else:
                status, rr = self.offer_answers.pop(0)
            self.queue.append(
                ("offer_in", bytes([0, 0, 0, token, 0, 0, 0, 0, rr, 0, 0, 0, status, 0, 0, 0]))
            )
        elif role == "content_out":
            seq = struct.unpack_from("<H", data, 2)[0]
            status = self.content_status.get(seq, 0)
            self.queue.append(("content_in", struct.pack("<HHB", seq, 0, status) + bytes(11)))

    def read(self, role, timeout):
        for i, (r, d) in enumerate(self.queue):
            if r == role:
                return self.queue.pop(i)[1]
        return None


def make_pair(label, comp=0x11, size=200):
    return fw.Pair(
        label,
        label + ".offer.bin",
        label + ".payload.bin",
        fw.Offer.parse(offer_bytes(component=comp)),
        payload_bytes(image_records(size)),
        "test",
    )


class Transaction(unittest.TestCase):
    def test_bank_rejected_then_accepted_one_payload(self):
        pen = FakePen([(fw.ST_REJECT, 0x04), (fw.ST_ACCEPT, 0)])
        hi, lo, mcu = make_pair("BLE-HI"), make_pair("BLE-LO"), make_pair("MCU-B0", 0x10)
        done = fw.run_transaction(
            fw.Session(pen, log=lambda s: None), [hi, lo, mcu], log=lambda s: None
        )
        self.assertIs(done, lo)
        offers = [d for r, d in pen.sent if r == "offer_out"]
        self.assertEqual(
            [o[2] for o in offers], [0xFF, 0xFF, 0x11, 0x11, 0xFF]
        )  # MCU never offered
        self.assertEqual([o[0] for o in offers if o[2] == 0xFF], [0, 1, 2])
        content = [d for r, d in pen.sent if r == "content_out"]
        seqs = [struct.unpack_from("<H", c, 2)[0] for c in content]
        self.assertEqual(seqs, [1, 2, 3, 4])  # 200 bytes = 52+52+52+44, from 1
        self.assertEqual([c[0] for c in content], [0x80, 0, 0, 0x40])
        self.assertEqual(
            b"".join(c[8 : 8 + c[1]] for c in content), b"".join(d for _, d in lo.records)
        )

    @staticmethod
    def lose_end_offer_list(pen):
        orig = pen.write

        def write(role, data):
            if role == "offer_out" and data[2] == 0xFF and data[0] == fw.INFO_END_LIST:
                raise OSError(19, "No such device")
            orig(role, data)

        pen.write = write
        return pen

    def test_end_offer_list_lost_after_a_delivery(self):
        # the pen may restart right after the image is in: END_OFFER_LIST then goes nowhere
        pen = self.lose_end_offer_list(FakePen([(fw.ST_ACCEPT, 0)]))
        pair = make_pair("BLE-HI")
        self.assertIs(
            fw.run_transaction(fw.Session(pen, log=lambda s: None), [pair], log=lambda s: None),
            pair,
        )
        # ... but without a delivery the same failure is an error
        pen = self.lose_end_offer_list(FakePen([(fw.ST_REJECT, 0)]))
        with self.assertRaises(OSError):
            fw.run_transaction(
                fw.Session(pen, log=lambda s: None), [make_pair("BLE-HI")], log=lambda s: None
            )

    def test_nothing_accepted(self):
        pen = FakePen([(fw.ST_REJECT, 0x00)])
        done = fw.run_transaction(
            fw.Session(pen, log=lambda s: None), [make_pair("MCU-B0", 0x10)], log=lambda s: None
        )
        self.assertIsNone(done)
        self.assertFalse([1 for r, _ in pen.sent if r == "content_out"])

    def test_content_error_stops(self):
        pen = FakePen([(fw.ST_ACCEPT, 0)], content_status={4: 0x06})  # the last block: signature
        with self.assertRaisesRegex(fw.CfuError, "ERROR_SIGNATURE"):
            fw.run_transaction(
                fw.Session(pen, log=lambda s: None), [make_pair("BLE-HI")], log=lambda s: None
            )

    def test_wrong_token_stops(self):
        pen = FakePen([], wrong_token=True)
        with self.assertRaisesRegex(fw.CfuError, "token"):
            fw.run_transaction(
                fw.Session(pen, log=lambda s: None), [make_pair("BLE-HI")], log=lambda s: None
            )

    def test_timeout_stops(self):
        pen = FakePen([(fw.ST_ACCEPT, 0)])
        pen.read = lambda role, timeout: (
            None if role == "content_in" else FakePen.read(pen, role, timeout)
        )
        with self.assertRaisesRegex(fw.CfuError, "no response"):
            fw.run_transaction(
                fw.Session(pen, log=lambda s: None), [make_pair("BLE-HI")], log=lambda s: None
            )


class UntilDone(unittest.TestCase):
    """run_until_done: every newer part, one per transaction, retries after a dropped link."""

    def setUp(self):
        self.t = 0.0
        self.versions = {0x11: 0x0500948B, 0x10: 0x0500858B}  # BLE 5.148, MCU 5.133
        self.statuses = []

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += s

    def status(self):
        st = fw.StatusFile(None)
        orig = st.set

        def record(**kw):
            orig(**kw)
            self.statuses.append(dict(st.data))

        st.set = record
        return st

    def resp(self):
        return {
            "count": len(self.versions),
            "protocol": 4,
            "extension": 0,
            "components": [fw.Component(v, 0, 0, c, 0x43) for c, v in self.versions.items()],
        }

    def device(self, pen):
        test = self

        class Dev:
            reports = pen.reports

            def get_version(self):
                return test.resp()

            def close(self):
                pass

            def drain(self):
                pen.drain()

            def write(self, role, data):
                pen.write(role, data)

            def read(self, role, timeout):
                return pen.read(role, timeout)

        return Dev()

    def pairs(self):
        ble = fw.Pair(
            "BLE-LO",
            "x",
            "y",
            fw.Offer.parse(offer_bytes(component=0x11, version=0x0500968B)),
            payload_bytes(image_records(100)),
            "test",
        )
        mcu = fw.Pair(
            "MCU-B0",
            "x",
            "y",
            fw.Offer.parse(offer_bytes(component=0x10, version=0x05008E8B)),
            payload_bytes(image_records(100)),
            "test",
        )
        return [ble, mcu]

    def test_two_parts_with_a_restart_between(self):
        pen = FakePen([(fw.ST_ACCEPT, 0), (fw.ST_ACCEPT, 0)])
        delivered, restart = [], {"pending": False}

        def find():
            if restart["pending"]:  # the pen restarts: its link goes once, then comes back
                restart["pending"] = False
                return []
            return [("/dev/hidraw9", pen.reports, "Surface Slim Pen 2")]

        orig_payload = fw.Session.payload

        def payload(sess, pair, progress=True, on_progress=None):
            r = orig_payload(sess, pair, progress=False, on_progress=on_progress)
            delivered.append(pair.label)
            self.versions[pair.offer.component] = pair.offer.version  # newer after the restart
            restart["pending"] = True
            return r

        fw.Session.payload = payload
        try:
            rc = fw.run_until_done(
                self.pairs(),
                self.status(),
                log=lambda s: None,
                find=find,
                open_dev=lambda n, r: self.device(pen),
                check=lambda node: [],
                sleep=self.sleep,
                now=self.now,
            )
        finally:
            fw.Session.payload = orig_payload
        self.assertEqual(rc, 0)
        self.assertEqual(delivered, ["BLE-LO", "MCU-B0"])
        last = self.statuses[-1]
        self.assertEqual(last["state"], "done")
        self.assertEqual([d["label"] for d in last["delivered"]], ["BLE-LO", "MCU-B0"])
        sending = [st for st in self.statuses if st["state"] == "sending"]
        self.assertTrue(sending and sending[-1]["sent"] == sending[-1]["total"])
        self.assertEqual([st["state"] for st in self.statuses].count("restarting"), 2)

    def test_a_dropped_link_waits_and_starts_the_part_again(self):
        pen = FakePen([(fw.ST_ACCEPT, 0), (fw.ST_ACCEPT, 0)])
        calls = {"n": 0}
        links = {"up": True}

        def find():
            return [("/dev/hidraw9", pen.reports, "pen")] if links["up"] else []

        orig_payload = fw.Session.payload

        def payload(sess, pair, progress=True, on_progress=None):
            calls["n"] += 1
            if calls["n"] == 1:
                links["up"] = False  # the pen went to sleep mid-transfer
                raise OSError(19, "No such device")
            self.versions[pair.offer.component] = pair.offer.version
            return orig_payload(sess, pair, progress=False, on_progress=on_progress)

        def sleep(s):
            self.sleep(s)
            if self.statuses[-1]["state"] == "waiting-link" and self.t > 30:
                links["up"] = True  # the owner pressed the top button

        fw.Session.payload = payload
        try:
            pairs = self.pairs()[:1]
            rc = fw.run_until_done(
                pairs,
                self.status(),
                log=lambda s: None,
                find=find,
                open_dev=lambda n, r: self.device(pen),
                check=lambda node: [],
                sleep=sleep,
                now=self.now,
            )
        finally:
            fw.Session.payload = orig_payload
        self.assertEqual(rc, 0)
        states = [st["state"] for st in self.statuses]
        self.assertIn("interrupted", states)
        self.assertIn("waiting-link", states)
        self.assertEqual(states[-1], "done")
        self.assertEqual(calls["n"], 2)

    def test_refused_and_no_link(self):
        pen = FakePen([])
        rc = fw.run_until_done(
            self.pairs(),
            self.status(),
            log=lambda s: None,
            find=lambda: [("/dev/hidraw9", pen.reports, "pen")],
            open_dev=lambda n, r: self.device(pen),
            check=lambda node: [("charger", "the phone is not on a charger")],
            sleep=self.sleep,
            now=self.now,
        )
        self.assertEqual(rc, 3)
        self.assertEqual(self.statuses[-1]["state"], "refused")
        self.assertEqual(
            self.statuses[-1]["problems"],
            [{"code": "charger", "text": "the phone is not on a charger"}],
        )
        self.assertFalse(pen.sent)  # nothing went to the pen
        self.statuses.clear()
        rc = fw.run_until_done(
            self.pairs(),
            self.status(),
            log=lambda s: None,
            find=lambda: [],
            open_dev=lambda n, r: self.device(pen),
            check=lambda node: [],
            sleep=self.sleep,
            now=self.now,
            wait_link=60,
        )
        self.assertEqual(rc, 4)
        self.assertEqual([st["state"] for st in self.statuses], ["waiting-link", "failed"])

    def test_up_to_date_sends_nothing(self):
        self.versions = {0x11: 0x0500968B, 0x10: 0x05008E8B}
        pen = FakePen([])
        rc = fw.run_until_done(
            self.pairs(),
            self.status(),
            log=lambda s: None,
            find=lambda: [("/dev/hidraw9", pen.reports, "pen")],
            open_dev=lambda n, r: self.device(pen),
            check=lambda node: [],
            sleep=self.sleep,
            now=self.now,
        )
        self.assertEqual(rc, 0)
        self.assertEqual(self.statuses[-1]["state"], "done")
        self.assertFalse(pen.sent)

    def test_status_file_is_readable_by_everyone(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "fw", "status.json")
            st = fw.StatusFile(path)
            st.set(state="sending", label="BLE-LO", sent=1, total=10)
            st.set(state="restarting")
            import json

            with open(path) as f:
                data = json.load(f)
            self.assertEqual(data["state"], "restarting")
            self.assertNotIn("sent", data)  # a new state starts clean
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o644)

    def test_inspect_json_names_the_set(self):
        pairs = self.pairs()
        pairs[0].known = "Microsoft 1.22.139.0 BLE-LO 5.150.139"
        pairs[1].known = "Microsoft 1.22.139.0 MCU-B0 5.142.139"
        hap = fw.Pair(
            "HAPTIC",
            "x",
            "y",
            fw.Offer.parse(offer_bytes(component=0x13, version=0x0500758B)),
            payload_bytes(image_records(100)),
            "HAPTIC 5.117.139",
        )
        orig = fw.load_pairs
        fw.load_pairs = lambda paths: pairs + [hap]
        out = io.StringIO()
        try:
            with redirect_stdout(out):
                self.assertEqual(fw.main(["inspect", "--json", "somewhere"]), 0)
        finally:
            fw.load_pairs = orig
        import json

        got = json.loads(out.getvalue())
        self.assertEqual(got["set"], "Microsoft 1.22.139.0")
        self.assertTrue(got["known"])
        self.assertEqual([p["name"] for p in got["parts"]], ["BLE", "MCU", "HAPTIC"])

    def test_json_outputs(self):
        resp = self.resp()
        got = fw.versions_json(resp)
        self.assertEqual(
            [(v["name"], v["version"]) for v in got], [("BLE", "5.148.139"), ("MCU", "5.133.139")]
        )
        got = fw.pairs_json(self.pairs())
        self.assertEqual(
            [(p["label"], p["version"]) for p in got],
            [("BLE-LO", "5.150.139"), ("MCU-B0", "5.142.139")],
        )


class HostChecks(unittest.TestCase):
    def test_bluez_state(self):
        def dev(name, connected, modalias="", battery=None):
            d = {
                "org.bluez.Device1": {
                    "Name": {"data": name},
                    "Connected": {"data": connected},
                    "Modalias": {"data": modalias},
                }
            }
            if battery is not None:
                d["org.bluez.Battery1"] = {"Percentage": {"data": battery}}
            return d

        objs = {
            "/org/bluez/hci0": {"org.bluez.Adapter1": {}},
            "/org/bluez/hci0/dev_A": dev("Surface Slim Pen 2", True, "usb:v045Ep0C0Fd0596", 64),
            "/org/bluez/hci0/dev_B": dev("Headphones", False),
            "/org/bluez/hci0/dev_C": dev("Watch", True),
        }
        pen, others = fw.bluez_state(objs)
        self.assertEqual(pen["battery"], 64)
        self.assertEqual([o["name"] for o in others], ["Watch"])
        del objs["/org/bluez/hci0/dev_C"]
        self.assertEqual(fw.bluez_state(objs)[1], [])

    def test_charger_online(self):
        with tempfile.TemporaryDirectory() as t:
            for name, kind, online in (
                ("qcom-battmgr-bat", "Battery", None),
                ("qcom-battmgr-usb", "USB", "0"),
                ("qcom-battmgr-wls", "Wireless", "1"),
            ):
                os.makedirs(os.path.join(t, name))
                pathlib.Path(t, name, "type").write_text(kind + "\n")
                if online is not None:
                    pathlib.Path(t, name, "online").write_text(online + "\n")
            self.assertEqual(fw.charger_online(t), ["qcom-battmgr-wls"])
            pathlib.Path(t, "qcom-battmgr-wls", "online").write_text("0\n")
            self.assertEqual(fw.charger_online(t), [])

    def test_find_pens(self):
        with tempfile.TemporaryDirectory() as t:
            for n, hid_id, desc in (
                ("hidraw0", "0018:0000045E:00000C1D", pen_like_descriptor()),
                ("hidraw3", "0005:0000045E:00000C0F", pen_like_descriptor()),
                ("hidraw4", "0005:0000045E:00000C0F", b"\x05\x01"),
            ):
                os.makedirs(os.path.join(t, n, "device"))
                pathlib.Path(t, n, "device", "uevent").write_text(
                    f"HID_ID={hid_id}\nHID_NAME=Surface Pen\n"
                )
                pathlib.Path(t, n, "device", "report_descriptor").write_bytes(desc)
            pens = fw.find_pens(t)
            self.assertEqual([p[0] for p in pens], ["/dev/hidraw3"])
            self.assertEqual(pens[0][1]["version"], (0x2A, 60))


# --- a Windows-style catalog (PKCS#7 signedData over a CTL), signed by a throwaway CA ---------
def der(tag, body):
    n = len(body)
    if n < 0x80:
        ln = bytes([n])
    else:
        nb = n.to_bytes((n.bit_length() + 7) // 8, "big")
        ln = bytes([0x80 | len(nb)]) + nb
    return bytes([tag]) + ln + body


def seq(*xs):
    return der(0x30, b"".join(xs))


def oid(hexstr):
    return der(0x06, bytes.fromhex(hexstr))


def integer(n):
    return der(0x02, n.to_bytes((n.bit_length() + 8) // 8, "big", signed=True))


@unittest.skipUnless(HAVE_CRYPTO, "python3-cryptography is not installed")
class Catalog(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives.serialization import Encoding
        from cryptography.x509.oid import NameOID

        def name(cn):
            return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])

        now = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)

        def cert(subject, key, issuer, issuer_key, ca):
            b = (
                x509.CertificateBuilder()
                .subject_name(name(subject))
                .issuer_name(name(issuer))
                .public_key(key.public_key())
                .serial_number(x509.random_serial_number())
                .not_valid_before(now)
                .not_valid_after(now + datetime.timedelta(days=30))
                .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
            )
            return b.sign(issuer_key, hashes.SHA256())

        keys = [ec.generate_private_key(ec.SECP256R1()) for _ in range(4)]
        root = cert("Test Root", keys[0], "Test Root", keys[0], True)
        inter = cert("Test Intermediate", keys[1], "Test Root", keys[0], True)
        signer = cert("Test Publisher", keys[2], "Test Intermediate", keys[1], False)
        cls.other_root = cert("Other Root", keys[3], "Other Root", keys[3], True).public_bytes(
            Encoding.DER
        )
        cls.root = root.public_bytes(Encoding.DER)
        cls.member = hashlib.sha256(b"a payload").digest()
        sha256 = oid("608648016503040201")
        ctl = seq(
            seq(oid("2b0601040182370c0101")),
            der(0x04, b"list-id"),
            der(0x17, b"260101000000Z"),
            seq(oid("2b0e03021a"), b"\x05\x00"),
            seq(
                seq(
                    der(0x04, b"subject"),
                    der(
                        0x31,
                        seq(
                            oid("2b06010401823702010f"),
                            der(0x31, seq(seq(sha256, b"\x05\x00"), der(0x04, cls.member))),
                        ),
                    ),
                )
            ),
        )
        content_octets = ctl[2:] if ctl[1] < 0x80 else ctl[2 + (ctl[1] & 0x7F) :]
        attrs = seq(oid("2a864886f70d010903"), der(0x31, oid("2b060104018237" + "0a01"))) + seq(
            oid("2a864886f70d010904"), der(0x31, der(0x04, hashlib.sha256(content_octets).digest()))
        )
        signature = keys[2].sign(der(0x31, attrs), ec.ECDSA(hashes.SHA256()))
        signer_info = seq(
            integer(1),
            seq(signer.issuer.public_bytes(), integer(signer.serial_number)),
            seq(sha256, b"\x05\x00"),
            der(0xA0, attrs),
            seq(oid("2a8648ce3d040302")),
            der(0x04, signature),
        )
        signed_data = seq(
            integer(1),
            der(0x31, seq(sha256, b"\x05\x00")),
            seq(oid("2b060104018237" + "0a01"), der(0xA0, ctl)),
            der(0xA0, inter.public_bytes(Encoding.DER) + signer.public_bytes(Encoding.DER)),
            der(0x31, signer_info),
        )
        cls.cat = seq(oid("2a864886f70d010702"), der(0xA0, signed_data))
        cls.ctl = ctl

    def test_good(self):
        res = fw.verify_catalog(self.cat, self.root)
        self.assertIn(self.member, res["digests"])
        self.assertEqual(
            [c.subject.rfc4514_string() for c in res["chain"]],
            ["CN=Test Publisher", "CN=Test Intermediate", "CN=Test Root"],
        )

    def test_changed_content(self):
        i = self.cat.find(self.member)
        bad = self.cat[:i] + bytes([self.member[0] ^ 1]) + self.cat[i + 1 :]
        with self.assertRaisesRegex(fw.CfuError, "signed digest"):
            fw.verify_catalog(bad, self.root)

    def test_bad_signature(self):
        at = len(self.cat) - 10  # the signature is the catalog's last field
        bad = self.cat[:at] + bytes([self.cat[at] ^ 1]) + self.cat[at + 1 :]
        with self.assertRaises(fw.CfuError):
            fw.verify_catalog(bad, self.root)

    def test_other_root(self):
        with self.assertRaises(fw.CfuError):
            fw.verify_catalog(self.cat, self.other_root)

    def test_verify_cat_command_refuses_a_foreign_root(self):
        with tempfile.TemporaryDirectory() as t:
            pathlib.Path(t, "x.cat").write_bytes(self.cat)
            pathlib.Path(t, "root.der").write_bytes(self.root)
            pathlib.Path(t, "a.payload.bin").write_bytes(b"a payload")
            pathlib.Path(t, "b.offer.bin").write_bytes(b"not listed")
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(
                    fw.main(["verify-cat", f"{t}/x.cat", "--root", f"{t}/root.der"]), 2
                )
                self.assertEqual(
                    fw.main(["verify-cat", f"{t}/x.cat", "--root", f"{t}/root.der", "--any-root"]),
                    1,
                )
            text = out.getvalue()
            self.assertRegex(text, r"listed +[0-9a-f]{64}  a.payload.bin")
            self.assertRegex(text, r"NOT LISTED [0-9a-f]{64}  b.offer.bin")


if __name__ == "__main__":
    unittest.main()
