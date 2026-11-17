"""Tests of duo2stock: the GPT slot bits, super's LP metadata, the extent
copy, the ELF dependency reader and the firmware merge (no phone, no root).

    cd stock && python3 -m unittest discover -s tests
"""

import hashlib
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import duo2stock as ds


def gpt_disk(path, sector, parts):
    """A disk image with a GPT: parts = [(name, first, last, attr)]."""
    entries = b""
    for name, first, last, attr in parts:
        e = b"\x01" * 16 + b"\x02" * 16 + struct.pack("<QQQ", first, last, attr)
        e += name.encode("utf-16-le").ljust(72, b"\0")
        entries += e
    entries = entries.ljust(128 * 128, b"\0")
    hdr = bytearray(92)
    hdr[:8] = b"EFI PART"
    struct.pack_into("<QII", hdr, 72, 2, 128, 128)
    with open(path, "wb") as f:
        f.write(b"\0" * sector)
        f.write(bytes(hdr).ljust(sector, b"\0"))
        f.write(entries)


def lp_super(path, slots):
    """A super image: slots = [{name: [(start_sector, sectors), ...]}], with
    valid checksums; data at each extent = a byte pattern per partition."""
    max_size = 65536
    geo = bytearray(4096)
    struct.pack_into("<II", geo, 0, ds.LP_GEOMETRY_MAGIC, 52)
    struct.pack_into("<III", geo, 40, max_size, len(slots), 4096)
    geo[8:40] = hashlib.sha256(bytes(geo[:52])).digest()
    metas = []
    for parts in slots:
        ptab, etab = b"", b""
        n_ext = 0
        for name, spans in parts.items():
            ptab += name.encode().ljust(36, b"\0") + struct.pack("<IIII", 1, n_ext, len(spans), 0)
            for start, sectors in spans:
                etab += struct.pack("<QIQI", sectors, 0, start, 0)
                n_ext += 1
        tables = ptab + etab
        hsize = 256
        h = bytearray(hsize)
        struct.pack_into("<IHHI", h, 0, ds.LP_HEADER_MAGIC, 10, 2, hsize)
        struct.pack_into("<I", h, 44, len(tables))
        h[48:80] = hashlib.sha256(tables).digest()
        struct.pack_into("<III", h, 80, 0, len(parts), 52)
        struct.pack_into("<III", h, 92, len(ptab), n_ext, 24)
        h[12:44] = hashlib.sha256(bytes(h)).digest()
        metas.append((bytes(h) + tables).ljust(max_size, b"\0"))
    with open(path, "wb") as f:
        f.write(b"\0" * 4096 + bytes(geo) + bytes(geo))
        for m in metas:
            f.write(m)
        for parts in slots:
            for name, spans in parts.items():
                for i, (start, sectors) in enumerate(spans):
                    f.seek(start * 512)
                    f.write(bytes([(len(name) + i) & 0xFF]) * (sectors * 512))


class Gpt(unittest.TestCase):
    def test_entries_and_active_slot(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "disk")
            gpt_disk(
                p,
                4096,
                [
                    ("boot_a", 10, 20, 0x0073000000000000),
                    ("boot_b", 21, 30, 0x0077000000000000),
                ],
            )
            e = ds.gpt_entries(p, 4096)
            self.assertEqual(e["boot_a"][:2], (10, 20))
            self.assertFalse(ds.slot_active(e["boot_a"][2]))
            self.assertTrue(ds.slot_active(e["boot_b"][2]))
            self.assertEqual(ds.slot_order({"a": e["boot_a"][2], "b": e["boot_b"][2]}), ["b", "a"])

    def test_slot_order_without_one_active(self):
        self.assertEqual(ds.slot_order({}), ["a", "b"])
        both = 1 << 50
        self.assertEqual(ds.slot_order({"a": both, "b": both}), ["a", "b"])
        self.assertEqual(ds.slot_order({"a": both, "b": 0}), ["a", "b"])

    def test_no_gpt(self):
        with tempfile.NamedTemporaryFile() as f:
            f.write(b"\0" * 16384)
            f.flush()
            with self.assertRaises(ds.StockError):
                ds.gpt_entries(f.name, 4096)


class Super(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d)
        self.p = os.path.join(self.d, "super")
        lp_super(
            self.p,
            [
                {"vendor_a": [(400, 8)], "system_a": [(500, 4), (300, 2)]},
                {"vendor_b": [(600, 8)], "system_b": [(700, 2), (800, 2), (900, 1)]},
            ],
        )

    def test_partitions_per_slot(self):
        with open(self.p, "rb") as f:
            a = ds.lp_partitions(f, 0)
            b = ds.lp_partitions(f, 1)
        self.assertEqual(a["system_a"], [(500 * 512, 4 * 512), (300 * 512, 2 * 512)])
        self.assertEqual(b["system_b"], [(700 * 512, 1024), (800 * 512, 1024), (900 * 512, 512)])
        self.assertNotIn("vendor_b", a)

    def test_copy_joins_the_extents_in_order(self):
        with open(self.p, "rb") as f:
            spans = ds.lp_partitions(f, 1)["system_b"]
            out = os.path.join(self.d, "system.img")
            digest = ds.copy_spans(f, spans, out)
        data = open(out, "rb").read()
        want = bytes([8]) * 1024 + bytes([9]) * 1024 + bytes([10]) * 512
        self.assertEqual(data, want)
        self.assertEqual(digest, hashlib.sha256(want).hexdigest())

    def test_a_bad_checksum_is_refused(self):
        with open(self.p, "r+b") as f:
            f.seek(4096 * 3 + 300)  # inside slot 0's tables
            f.write(b"\xff")
        with open(self.p, "rb") as f:
            with self.assertRaises(ds.StockError):
                ds.lp_partitions(f, 0)
            self.assertIn("vendor_b", ds.lp_partitions(f, 1))

    def test_no_such_slot(self):
        with open(self.p, "rb") as f:
            with self.assertRaises(ds.StockError):
                ds.lp_partitions(f, 2)


class Elf(unittest.TestCase):
    def test_needed_matches_readelf(self):
        exe = shutil.which("readelf")
        target = os.path.realpath(sys.executable)
        if not exe:
            self.skipTest("no readelf")
        out = subprocess.run([exe, "-d", target], capture_output=True, text=True).stdout
        want = [
            line.split("[", 1)[1].rstrip("]") for line in out.splitlines() if "(NEEDED)" in line
        ]
        with open(target, "rb") as f:
            if f.read(6)[4:6] != b"\x02\x01":
                self.skipTest("not a 64-bit little-endian ELF")
        self.assertEqual(ds.elf_needed(target), want)

    def test_not_an_elf(self):
        with tempfile.NamedTemporaryFile() as f:
            f.write(b"#!/bin/sh\n")
            f.flush()
            with self.assertRaises(ds.StockError):
                ds.elf_needed(f.name)


class Props(unittest.TestCase):
    def test_vndk_version(self):
        self.assertEqual(ds.vndk_version(["ro.vndk.version=30\n"]), "30")
        self.assertEqual(ds.vndk_version(["a=b\n", "ro.vendor.build.version.sdk=31\n"]), "31")
        with self.assertRaises(ds.StockError):
            ds.vndk_version(["a=b"])


class Images(unittest.TestCase):
    def test_release_needs_every_image(self):
        img = ds.Images({}, None, "b", {n: d for n, (_s, d) in ds.KNOWN.items()})
        self.assertEqual(img.release(), ds.KNOWN_RELEASE)
        h = dict(img.hashes, vendor="0" * 64)
        self.assertIsNone(ds.Images({}, None, "b", h).release())


class Install(unittest.TestCase):
    def test_merge_keeps_other_files_and_drops_old_imports(self):
        with tempfile.TemporaryDirectory() as d:
            dest = os.path.join(d, "fw")
            os.makedirs(os.path.join(dest, "x"))
            open(os.path.join(dest, "regulatory.db"), "w").write("debian")
            man = os.path.join(d, "firmware.list")
            new = os.path.join(d, "new")
            os.makedirs(os.path.join(new, "qca"))
            open(os.path.join(new, "qca", "a.tlv"), "w").write("1")
            open(os.path.join(new, "qca", "gone.bin"), "w").write("old")
            os.symlink("a.tlv", os.path.join(new, "qca", "wcna.tlv"))
            self.assertEqual(ds.merge_firmware(new, dest, man), 3)
            self.assertTrue(os.path.islink(os.path.join(dest, "qca", "wcna.tlv")))
            os.remove(os.path.join(new, "qca", "gone.bin"))
            open(os.path.join(new, "qca", "a.tlv"), "w").write("2")
            self.assertEqual(ds.merge_firmware(new, dest, man), 2)
            self.assertFalse(os.path.exists(os.path.join(dest, "qca", "gone.bin")))
            self.assertEqual(open(os.path.join(dest, "qca", "a.tlv")).read(), "2")
            self.assertEqual(open(os.path.join(dest, "regulatory.db")).read(), "debian")

    def test_replace_dir(self):
        with tempfile.TemporaryDirectory() as d:
            dest = os.path.join(d, "opt", "duo2-touch")
            os.makedirs(dest)
            open(os.path.join(dest, "old"), "w").close()
            new = os.path.join(d, "new")
            os.makedirs(new)
            open(os.path.join(new, "linker64"), "w").close()
            ds.replace_dir(new, dest)
            self.assertEqual(os.listdir(dest), ["linker64"])
            self.assertFalse(os.path.exists(dest + ".old"))


class Unit(unittest.TestCase):
    def test_the_unit_runs_once_and_before_its_users(self):
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        text = open(os.path.join(here, "duo2-stock-import.service")).read()
        self.assertIn("ConditionPathExists=!/var/lib/duo2-stock/imported", text)
        for u in ("duo-wc.service", "duo2-touchd.service", "hexagonrpcd.service"):
            self.assertIn(u, text.split("Before=", 1)[1].split("\n", 1)[0])
        self.assertIn("After=local-fs.target", text)


@unittest.skipUnless(
    os.environ.get("DUO2_STOCK_IMAGES") and shutil.which("debugfs") and shutil.which("mcopy"),
    "DUO2_STOCK_IMAGES (an unpacked OTA's img/), DUO2_STOCK_PERSIST, debugfs and mcopy",
)
class FromImages(unittest.TestCase):
    """The whole import from real stock images (the maintainer's machine)."""

    def test_import(self):
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        repo = os.path.dirname(here)
        with tempfile.TemporaryDirectory() as d:
            helpers = os.path.join(d, "helpers")
            os.makedirs(os.path.join(helpers, "scripts"))
            for f in (
                "collect-firmware.sh",
                "mdt2mbn.py",
                "camx-sensor-tables.py",
                "camx-module-dump.py",
                "axml2xml.py",
            ):
                shutil.copy(os.path.join(repo, "scripts", f), os.path.join(helpers, "scripts"))
            root = os.path.join(d, "root")
            img = ds.Images.from_dir(
                os.environ["DUO2_STOCK_IMAGES"], os.environ["DUO2_STOCK_PERSIST"]
            )
            st = os.path.join(root, ds.STATE)
            os.makedirs(st)
            touchd = os.environ.get("DUO2_STOCK_TOUCHD", os.path.join(repo, "out", "touchd"))
            state = ds.import_from(img, root, helpers, touchd, st)
            self.assertEqual(
                sorted(state["parts"]), ["camera", "firmware", "power", "sensors", "touch"]
            )
            self.assertTrue(
                os.path.exists(os.path.join(root, ds.TOUCH, "lib64", "libsurfacetouch_c1.so"))
            )
            self.assertTrue(
                os.path.exists(os.path.join(root, ds.FIRMWARE, "qcom/sm8350/microsoft/adsp.mbn"))
            )


if __name__ == "__main__":
    unittest.main()
