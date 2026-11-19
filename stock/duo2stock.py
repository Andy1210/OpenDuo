"""Import this phone's own stock files from its stock partitions.

OpenDuo needs files that only Microsoft and Qualcomm may distribute: the DSP,
modem, WiFi, Bluetooth, GPU and video firmware, the touch heat processor with
the Android runtime it runs in, the sensor DSP's configuration with this
phone's own calibration, the camera modules' register tables and the power
profile. None of them is in the image. Installing OpenDuo replaces only
Android's user data partition, so every stock partition is still on the
phone, and this module reads them from there (read only):

    modem_<slot>      FAT: the DSP images, WLAN firmware, modem configuration
    bluetooth_<slot>  FAT: the Bluetooth patch and NVM files
    dsp_<slot>        ext4: the sensor DSP's FastRPC skeletons
    super             Android's dynamic partitions (vendor, system, system_ext
                      of each slot), described by its LP metadata
    persist           ext4: the sensor registry Android generated on THIS phone

The slot is the one Android last booted (the "active" bit of boot_a/boot_b in
the GPT), the other one if the active one does not hold a usable set. super
is Virtual A/B: a logical partition can be spread over several extents, so
vendor, system and system_ext are copied into one file each (a work
directory on the root file system, removed afterwards) and read with
debugfs; the FAT partitions are read with mtools. Their sha256 is compared
with the images of the stock release OpenDuo is tested with (2023.501.541);
another release is used too, with a warning.

The recipes are the ones of the host-side scripts (scripts/collect-firmware.sh,
build-touch-runtime.sh, build-sensors-runtime.sh, build-camera-tables.sh,
build-power-profile.sh): the same inputs give the same files.
"""

import hashlib
import json
import os
import shutil
import struct
import subprocess
import tempfile
import time
import zipfile

LP_SECTOR = 512
LP_RESERVED = 4096
LP_GEOMETRY_MAGIC = 0x616C4467
LP_HEADER_MAGIC = 0x414C5030
LP_LINEAR = 0

# The stock release OpenDuo is developed and tested with: the size and sha256
# of each partition image in Microsoft's OTA package for it (the same bytes
# the phone holds once the update is merged). Hashes only: no vendor file.
KNOWN_RELEASE = "2023.501.541"
KNOWN = {
    "modem": (194424832, "7679740d97f4a179c996833b71437998d66d4cff20fb9bfc0cd3148c22d7e23f"),
    "bluetooth": (1159168, "4e744ace9b46d2e371e9b87dc233de79522b8c6b90927d7a0cf3e130c8f94131"),
    "dsp": (67108864, "3c48baaf64feebbda779cd0ee2d4c9735fd041ce281455a254fbf0e7bc19a8e7"),
    "vendor": (1631506432, "14d3afa645688495521232ca7c50084af7a14a86389f2715c57a4bce8e4dc631"),
    "system": (1211584512, "921a781d0d6745a3ddffe2cfaf2d97b265559714f8b0d918e72984cfb86b3714"),
    "system_ext": (541798400, "31e98b8787a834501457c272781854c5fcccfcb0e5175cd0b950bed700c564cc"),
}
SUPER_PARTS = ("vendor", "system", "system_ext")
RAW_PARTS = ("modem", "bluetooth", "dsp")

# Where the parts go on the phone (the paths the units and drivers read).
FIRMWARE = "usr/lib/firmware"
TOUCH = "opt/duo2-touch"
SENSORS = "usr/share/qcom/sm8350/Microsoft/surfaceduo2"
POWER = "opt/duo2-power-profile"
STATE = "var/lib/duo2-stock"

# The camera modules in the vendor image: module, sensor model, the file the
# device tree names (scripts/build-camera-tables.sh).
CAMERA_MODULES = (
    ("sony_uw_imx481", "0x0481", "imx481-uw"),
    ("sony_front_imx481", "0x0481", "imx481-front"),
    ("sony_tele_imx481", "0x0481", "imx481-tele"),
    ("sony_imx563", "0x0563", "imx563-wide"),
)


class StockError(Exception):
    """A step that cannot go on (the message says why, for the log)."""


def log(msg):
    print(msg, flush=True)


# --- GPT -------------------------------------------------------------------


def gpt_entries(path, sector):
    """The GPT of the disk at path: {name: (first_lba, last_lba, attributes)}."""
    with open(path, "rb") as f:
        f.seek(sector)
        hdr = f.read(92)
        if hdr[:8] != b"EFI PART":
            raise StockError("%s: no GPT" % path)
        lba, count, size = struct.unpack_from("<QII", hdr, 72)
        f.seek(lba * sector)
        table = f.read(count * size)
    out = {}
    for i in range(count):
        e = table[i * size : (i + 1) * size]
        if e[:16] == b"\0" * 16:
            continue
        first, last, attr = struct.unpack_from("<QQQ", e, 32)
        name = e[56:128].decode("utf-16-le").split("\0", 1)[0]
        out[name] = (first, last, attr)
    return out


def slot_active(attr):
    """Qualcomm's A/B bits in a GPT entry's attributes: bit 50 = active."""
    return bool((attr >> 50) & 1)


def slot_order(attrs):
    """attrs: {"a": attributes of boot_a, "b": ...}. The active slot first."""
    active = [s for s in ("a", "b") if s in attrs and slot_active(attrs[s])]
    if len(active) == 1:
        return active + [s for s in ("a", "b") if s != active[0]]
    return ["a", "b"]


def boot_attrs(bylabel="/dev/disk/by-partlabel", sysblock="/sys/class/block"):
    """The GPT attributes of boot_a and boot_b, read from their disk."""
    out = {}
    for s in ("a", "b"):
        dev = os.path.realpath(os.path.join(bylabel, "boot_" + s))
        name = os.path.basename(dev)
        try:
            disk = os.path.basename(os.path.dirname(os.path.realpath(os.path.join(sysblock, name))))
            with open(os.path.join(sysblock, disk, "queue", "logical_block_size")) as f:
                sector = int(f.read())
        except (OSError, ValueError):
            continue
        for pname, (_first, _last, attr) in gpt_entries("/dev/" + disk, sector).items():
            if pname == "boot_" + s:
                out[s] = attr
    return out


# --- super (LP metadata) ----------------------------------------------------


def lp_partitions(f, slot_index):
    """Read the LP metadata of super (an open file of the super partition):
    {name: [(byte offset, byte length), ...]} for metadata slot slot_index
    (0 = slot a, 1 = slot b). Checksums are checked."""
    f.seek(LP_RESERVED)
    geo = f.read(4096)
    magic, size = struct.unpack_from("<II", geo, 0)
    if magic != LP_GEOMETRY_MAGIC:
        raise StockError("super: no LP geometry")
    want = geo[8:40]
    check = bytearray(geo[:size])
    check[8:40] = b"\0" * 32
    if hashlib.sha256(check).digest() != want:
        raise StockError("super: LP geometry checksum")
    max_size, slots, _block = struct.unpack_from("<III", geo, 40)
    if slot_index >= slots:
        raise StockError("super: no metadata slot %d" % slot_index)
    f.seek(LP_RESERVED + 2 * 4096 + slot_index * max_size)
    meta = f.read(max_size)
    magic, _major, _minor, hsize = struct.unpack_from("<IHHI", meta, 0)
    if magic != LP_HEADER_MAGIC:
        raise StockError("super: no LP metadata in slot %d" % slot_index)
    header = bytearray(meta[:hsize])
    want = bytes(header[12:44])
    header[12:44] = b"\0" * 32
    if hashlib.sha256(header).digest() != want:
        raise StockError("super: LP header checksum (slot %d)" % slot_index)
    (tsize,) = struct.unpack_from("<I", meta, 44)
    tables = meta[hsize : hsize + tsize]
    if hashlib.sha256(tables).digest() != meta[48:80]:
        raise StockError("super: LP tables checksum (slot %d)" % slot_index)
    (p_off, p_num, p_size), (e_off, e_num, e_size) = (
        struct.unpack_from("<III", meta, 80),
        struct.unpack_from("<III", meta, 92),
    )
    extents = [struct.unpack_from("<QIQI", tables, e_off + i * e_size) for i in range(e_num)]
    out = {}
    for i in range(p_num):
        e = tables[p_off + i * p_size : p_off + (i + 1) * p_size]
        name = e[:36].split(b"\0", 1)[0].decode()
        _attr, first, num, _group = struct.unpack_from("<IIII", e, 36)
        spans = []
        for sectors, kind, start, source in extents[first : first + num]:
            if kind != LP_LINEAR or source != 0:
                raise StockError("super: %s has an extent this tool cannot read" % name)
            spans.append((start * LP_SECTOR, sectors * LP_SECTOR))
        out[name] = spans
    return out


def copy_spans(src, spans, dst):
    """Copy the extents of a logical partition into one file; its sha256."""
    h = hashlib.sha256()
    with open(dst, "wb") as out:
        for off, length in spans:
            src.seek(off)
            while length:
                b = src.read(min(length, 8 << 20))
                if not b:
                    raise StockError("super: short read")
                h.update(b)
                out.write(b)
                length -= len(b)
    return h.hexdigest()


def hash_prefix(path, length):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while length:
            b = f.read(min(length, 8 << 20))
            if not b:
                return None
            h.update(b)
            length -= len(b)
    return h.hexdigest()


# --- ELF (the touch runtime's dependency closure) ---------------------------


def elf_needed(path):
    """The DT_NEEDED names of a 64-bit little-endian ELF."""
    with open(path, "rb") as f:
        data = f.read()
    if data[:4] != b"\x7fELF" or data[4] != 2 or data[5] != 1:
        raise StockError("%s: not a 64-bit little-endian ELF" % path)
    phoff = struct.unpack_from("<Q", data, 0x20)[0]
    phsize, phnum = struct.unpack_from("<HH", data, 0x36)
    loads, dynamic = [], None
    for i in range(phnum):
        ptype, _flags, off, vaddr, _paddr, filesz = struct.unpack_from(
            "<IIQQQQ", data, phoff + i * phsize
        )
        if ptype == 1:
            loads.append((vaddr, off, filesz))
        elif ptype == 2:
            dynamic = (off, filesz)
    if dynamic is None:
        return []

    def to_off(addr):
        for vaddr, off, filesz in loads:
            if vaddr <= addr < vaddr + filesz:
                return addr - vaddr + off
        raise StockError("%s: address 0x%x outside the file" % (path, addr))

    needed, strtab = [], None
    off, size = dynamic
    for i in range(size // 16):
        tag, val = struct.unpack_from("<qQ", data, off + i * 16)
        if tag == 0:
            break
        if tag == 1:
            needed.append(val)
        elif tag == 5:
            strtab = to_off(val)
    if strtab is None:
        return []
    names = []
    for n in needed:
        end = data.index(b"\0", strtab + n)
        names.append(data[strtab + n : end].decode())
    return names


# --- the stock images --------------------------------------------------------


class Images:
    """The six stock images of one slot, as paths debugfs and mtools read.

    On the phone (from_device) the FAT and dsp partitions are read in place
    and the super partitions are copied into work/; from_dir takes the
    images of an unpacked OTA package (the host, and the tests)."""

    def __init__(self, paths, persist, slot, hashes):
        self.paths = paths
        self.persist = persist
        self.slot = slot
        self.hashes = hashes  # name -> sha256 (None: not checked)

    def release(self):
        """The known release when every image matches it, else None."""
        for name, (_size, digest) in KNOWN.items():
            if self.hashes.get(name) != digest:
                return None
        return KNOWN_RELEASE

    @classmethod
    def from_dir(cls, d, persist, check=True):
        paths = {n: os.path.join(d, n + ".img") for n in RAW_PARTS + SUPER_PARTS}
        for p in paths.values():
            if not os.path.exists(p):
                raise StockError("no %s" % p)
        hashes = {}
        if check:
            for n, p in paths.items():
                size, _ = KNOWN[n]
                hashes[n] = hash_prefix(p, size) if os.path.getsize(p) == size else None
        return cls(paths, persist, None, hashes)

    @classmethod
    def from_device(cls, slot, work, bylabel="/dev/disk/by-partlabel", copy=True):
        """Copy super's partitions of the slot into work/ (copy=False: only
        hash them) and hash the raw ones."""
        paths, hashes = {}, {}
        for n in RAW_PARTS:
            p = os.path.join(bylabel, "%s_%s" % (n, slot))
            if not os.path.exists(p):
                raise StockError("no partition %s_%s" % (n, slot))
            paths[n] = p
            hashes[n] = hash_prefix(p, KNOWN[n][0])
        with open(os.path.join(bylabel, "super"), "rb") as f:
            parts = lp_partitions(f, {"a": 0, "b": 1}[slot])
            for n in SUPER_PARTS:
                spans = parts.get("%s_%s" % (n, slot))
                if not spans:
                    raise StockError("super: no %s_%s" % (n, slot))
                total = sum(length for _off, length in spans)
                dst = os.path.join(work, n + ".img") if copy else os.devnull
                log("  %s_%s: %d MiB in %d extent(s)" % (n, slot, total >> 20, len(spans)))
                hashes[n] = copy_spans(f, spans, dst)
                paths[n] = dst
        persist = os.path.join(bylabel, "persist")
        return cls(paths, persist, slot, hashes)


# --- tools -------------------------------------------------------------------


def run(cmd, **kw):
    r = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if r.returncode != 0:
        raise StockError("%s: %s" % (" ".join(cmd[:3]), (r.stderr or r.stdout).strip()[-400:]))
    return r.stdout


def debugfs(image, request):
    """debugfs -R on an image or a partition (read only: no -w)."""
    return run(["debugfs", "-R", request, image], stdin=subprocess.DEVNULL)


def fat_copy(image, dest):
    """mcopy -s the FAT's /image directory into dest/."""
    os.makedirs(dest, exist_ok=True)
    env = dict(os.environ, MTOOLS_SKIP_CHECK="1")
    run(["mcopy", "-s", "-n", "-i", image, "::image", dest + "/"], env=env)


def need(path):
    if not os.path.exists(path):
        raise StockError("missing after extraction: %s" % path)
    return path


def chmod_tree(top):
    """u=rwX,go=rX, as the host recipes leave their trees."""
    for d, _dirs, files in os.walk(top):
        os.chmod(d, 0o755)
        for f in files:
            p = os.path.join(d, f)
            if os.path.islink(p):
                continue
            mode = os.stat(p).st_mode
            os.chmod(p, 0o755 if mode & 0o111 else 0o644)


# --- the parts ---------------------------------------------------------------


def build_firmware(img, work, helpers, out):
    """/lib/firmware's tree (scripts/collect-firmware.sh on the extracted
    partitions), plus the camera tables."""
    stock = os.path.join(work, "stock")
    fat_copy(img.paths["modem"], os.path.join(stock, "modem"))
    fat_copy(img.paths["bluetooth"], os.path.join(stock, "bt"))
    os.makedirs(os.path.join(stock, "vendor"), exist_ok=True)
    debugfs(img.paths["vendor"], "rdump /firmware %s" % os.path.join(stock, "vendor"))
    os.makedirs(os.path.dirname(out), exist_ok=True)
    run(["bash", os.path.join(helpers, "scripts", "collect-firmware.sh"), stock, out])


def build_camera(img, work, helpers, fwdir):
    w = os.path.join(work, "camera")
    os.makedirs(w, exist_ok=True)
    dst = os.path.join(fwdir, "qcom", "sm8350", "microsoft")
    os.makedirs(dst, exist_ok=True)
    made = []
    for module, model, name in CAMERA_MODULES:
        src = os.path.join(w, module + ".bin")
        debugfs(
            img.paths["vendor"], "dump /lib64/camera/com.qti.sensormodule.%s.bin %s" % (module, src)
        )
        if not os.path.getsize(need(src)):
            raise StockError("camera: %s is empty" % module)
        out = os.path.join(dst, name + ".bin")
        run(
            [
                "python3",
                "-I",
                os.path.join(helpers, "scripts", "camx-sensor-tables.py"),
                model,
                src,
                out,
            ]
        )
        os.chmod(out, 0o644)
        made.append(name)
    return made


def vndk_version(props):
    """ro.vndk.version where the vendor states it, else its SDK level."""
    found = {}
    for text in props:
        for line in text.splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                found.setdefault(k.strip(), v.strip())
    v = found.get("ro.vndk.version") or found.get("ro.vendor.build.version.sdk")
    if not v:
        raise StockError("touch: the vendor image states no VNDK version")
    return v


def build_touch(img, work, daemon_dir, out):
    """/opt/duo2-touch (scripts/build-touch-runtime.sh): Microsoft's heat
    processor, the stock linker and the libraries it needs, resolved the way
    Android's linker resolves a vendor library; duo2-touchd and the liblog
    shim are the touch package's."""
    w = os.path.join(work, "touch")
    for d in ("vendor", "system/bin", "vndk", "apex"):
        os.makedirs(os.path.join(w, d), exist_ok=True)
    daemon = need(os.path.join(daemon_dir, "duo2-touchd"))
    shim = need(os.path.join(daemon_dir, "liblog-stderr.so"))
    v, s, x = img.paths["vendor"], img.paths["system"], img.paths["system_ext"]
    props = []
    for name in ("build.prop", "default.prop"):
        p = os.path.join(w, "vendor", name)
        debugfs(v, "dump /%s %s" % (name, p))
        if os.path.exists(p):
            with open(p, errors="replace") as f:
                props.append(f.read())
    debugfs(v, "rdump /lib64 %s" % os.path.join(w, "vendor"))
    debugfs(s, "rdump /system/lib64 %s" % os.path.join(w, "system"))
    debugfs(s, "dump /system/bin/bootstrap/linker64 %s" % os.path.join(w, "system/bin/linker64"))
    ver = vndk_version(props)
    log("  vendor VNDK version: %s" % ver)
    apex = os.path.join(w, "apex", "vndk.apex")
    debugfs(x, "dump /apex/com.android.vndk.v%s.apex %s" % (ver, apex))
    with zipfile.ZipFile(need(apex)) as z:
        z.extract("apex_payload.img", os.path.join(w, "apex"))
    debugfs(
        os.path.join(w, "apex", "apex_payload.img"), "rdump /lib64 %s" % os.path.join(w, "vndk")
    )
    search = [
        need(os.path.join(w, "system/lib64/bootstrap")),
        need(os.path.join(w, "vendor/lib64")),
        need(os.path.join(w, "vndk/lib64")),
        need(os.path.join(w, "system/lib64")),
    ]
    lib = os.path.join(out, "lib64")
    os.makedirs(lib)
    shutil.copyfile(need(os.path.join(w, "system/bin/linker64")), os.path.join(out, "linker64"))
    shutil.copyfile(daemon, os.path.join(out, "duo2-touchd"))
    shutil.copyfile(shim, os.path.join(out, "liblog-stderr.so"))
    shutil.copyfile(
        need(os.path.join(w, "vendor/lib64/c1/libsurfacetouch_c1.so")),
        os.path.join(lib, "libsurfacetouch_c1.so"),
    )
    while True:
        added = False
        for f in sorted(os.listdir(lib)) + ["../duo2-touchd"]:
            p = os.path.join(lib, f)
            if f != "../duo2-touchd" and not f.endswith(".so"):
                continue
            for n in elf_needed(p):
                if os.path.exists(os.path.join(lib, n)):
                    continue
                src = next(
                    (os.path.join(d, n) for d in search if os.path.isfile(os.path.join(d, n))), None
                )
                if not src:
                    raise StockError(
                        "touch: no %s in the stock images (needed by %s)" % (n, os.path.basename(p))
                    )
                shutil.copyfile(src, os.path.join(lib, n))
                added = True
        if not added:
            break
    for f in os.listdir(lib):
        os.chmod(os.path.join(lib, f), 0o644)
    os.chmod(os.path.join(out, "linker64"), 0o755)
    os.chmod(os.path.join(out, "duo2-touchd"), 0o755)
    os.chmod(os.path.join(out, "liblog-stderr.so"), 0o644)
    os.chmod(out, 0o755)
    os.chmod(lib, 0o755)
    return len(os.listdir(lib))


def sns_version(data):
    """The N of the "version=N" line of an sns_reg_config or sns_reg_version
    file (bytes), or None. Android writes sns_reg_version with a trailing
    NUL byte."""
    for line in data.replace(b"\0", b"\n").splitlines():
        key, sep, value = line.partition(b"=")
        if sep and key.strip().lower() == b"version" and value.strip().isdigit():
            return int(value.strip())
    return None


def check_registry(vendor_config, persist_config, persist_version):
    """Raise StockError unless the persist registry belongs to the vendor's
    sns_reg_config. What the SLPI reads is the vendor's config and the
    registry's sns_reg_version (both served read-only: a mismatch would
    make it rebuild the registry, which it cannot), not the copy of the
    config Android keeps beside the registry. Android rebuilds the registry
    and its sns_reg_version on a config update without always refreshing
    that copy (a phone's copy said version=1 beside a version=2 registry:
    OpenDuo issue #1). So an identical copy passes, and otherwise the
    vendor config's version must be the registry's."""
    if vendor_config == persist_config:
        return
    want, have = sns_version(vendor_config), sns_version(persist_version)
    if want is None or want != have:
        raise StockError(
            "sensors: the persist registry was built from another sns_reg_config than the"
            " vendor image's (config version %s, registry version %s)" % (want, have)
        )
    log(
        "  sensors: the registry's copy of sns_reg_config is older (version %s); the"
        " registry itself is version %d, as the vendor's config"
        % (sns_version(persist_config), have)
    )


def build_sensors(img, work, out):
    """What hexagonrpcd serves to the sensor DSP
    (scripts/build-sensors-runtime.sh: the header there explains each file)."""
    x = os.path.join(work, "sensors")
    for d in ("vendor", "persist", "dsp"):
        os.makedirs(os.path.join(x, d), exist_ok=True)
    debugfs(img.paths["vendor"], "rdump /etc/sensors %s" % os.path.join(x, "vendor"))
    debugfs(img.paths["dsp"], "rdump /sdsp %s" % os.path.join(x, "dsp"))
    debugfs(img.persist, "rdump /sensors/registry %s" % os.path.join(x, "persist"))
    for d in ("vendor/sensors/config", "persist/registry/registry", "dsp/sdsp"):
        need(os.path.join(x, d))
    a = os.path.join(x, "vendor/sensors/sns_reg_config")
    b = os.path.join(x, "persist/registry/sns_reg_config")
    v = os.path.join(x, "persist/registry/sns_reg_version")
    with open(need(a), "rb") as fa, open(need(b), "rb") as fb, open(need(v), "rb") as fv:
        check_registry(fa.read(), fb.read(), fv.read())
    t = out
    os.makedirs(os.path.join(t, "sensors"))
    os.makedirs(os.path.join(t, "dsp"))
    shutil.copytree(os.path.join(x, "vendor/sensors/config"), os.path.join(t, "sensors/config"))
    shutil.copyfile(a, os.path.join(t, "sensors/sns_reg.conf"))
    shutil.copyfile(
        os.path.join(x, "persist/registry/sns_reg_version"),
        os.path.join(t, "sensors/sns_reg_version"),
    )
    shutil.copytree(
        os.path.join(x, "persist/registry/registry"), os.path.join(t, "sensors/registry")
    )
    shutil.copytree(os.path.join(x, "dsp/sdsp"), os.path.join(t, "dsp/sdsp"))
    facts = {
        "socinfo/soc_id": "415",
        "socinfo/revision": "2.1",
        "socinfo/hw_platform": "MTP",
        "socinfo/platform_subtype_id": "12",
        "socinfo/platform_subtype": "Unknown",
        "socinfo/platform_version": "65536",
        "sysfs-platform/soc/soc:surface_util/surface_product_id/surface_product_id": "2",
        "sensors/properties": "ro.build.version.release=12",
    }
    for rel, value in facts.items():
        p = os.path.join(t, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            f.write(value + "\n")
    chmod_tree(t)
    return len(os.listdir(os.path.join(t, "sensors/registry")))


def build_power(img, work, helpers, out):
    """Microsoft's power profile (scripts/build-power-profile.sh): the vendor
    overlay that carries one wins over framework-res."""
    w = os.path.join(work, "power")
    os.makedirs(os.path.join(w, "overlay"), exist_ok=True)
    listing = debugfs(img.paths["vendor"], "ls -p /overlay")
    for line in listing.splitlines():
        parts = line.split("/")
        if len(parts) > 5 and parts[5].endswith(".apk"):
            debugfs(
                img.paths["vendor"],
                "dump /overlay/%s %s" % (parts[5], os.path.join(w, "overlay", parts[5])),
            )
    src = None
    for a in sorted(os.listdir(os.path.join(w, "overlay"))):
        p = os.path.join(w, "overlay", a)
        try:
            with zipfile.ZipFile(p) as z:
                if "res/xml/power_profile.xml" not in z.namelist():
                    continue
        except zipfile.BadZipFile:
            continue
        if src:
            raise StockError("power: more than one overlay has a power profile")
        src = p
    if not src:
        src = os.path.join(w, "framework-res.apk")
        debugfs(img.paths["system"], "dump /system/framework/framework-res.apk %s" % src)
    xml = run(
        [
            "python3",
            "-I",
            os.path.join(helpers, "scripts", "axml2xml.py"),
            need(src),
            "res/xml/power_profile.xml",
        ]
    )
    if "<device" not in xml:
        raise StockError("power: no power profile in %s" % os.path.basename(src))
    os.makedirs(out)
    with open(os.path.join(out, "power_profile.xml"), "w") as f:
        f.write(xml)
    os.chmod(os.path.join(out, "power_profile.xml"), 0o644)
    os.chmod(out, 0o755)


# --- install -----------------------------------------------------------------


def replace_dir(new, dest):
    """Put the tree new/ at dest, the old one removed (rename, then delete)."""
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    old = dest + ".old"
    shutil.rmtree(old, ignore_errors=True)
    if os.path.lexists(dest):
        os.rename(dest, old)
    shutil.move(new, dest)
    shutil.rmtree(old, ignore_errors=True)


def merge_firmware(new, dest, manifest):
    """Copy the firmware tree into dest (other packages' files stay: the
    regulatory database, for one). The files of an earlier import that this
    one does not have any more are removed (the manifest lists them)."""
    files = []
    for d, _dirs, names in os.walk(new):
        for n in names:
            files.append(os.path.relpath(os.path.join(d, n), new))
    old = []
    if os.path.exists(manifest):
        with open(manifest) as f:
            old = [line.strip() for line in f if line.strip()]
    for rel in sorted(set(old) - set(files)):
        p = os.path.join(dest, rel)
        if os.path.lexists(p):
            os.remove(p)
    for rel in sorted(files):
        s, t = os.path.join(new, rel), os.path.join(dest, rel)
        os.makedirs(os.path.dirname(t), exist_ok=True)
        if os.path.lexists(t):
            os.remove(t)
        if os.path.islink(s):
            os.symlink(os.readlink(s), t)
        else:
            shutil.copy2(s, t)
    for d, dirs, _names in os.walk(dest):
        for n in dirs:
            os.chmod(os.path.join(d, n), 0o755)
    with open(manifest + ".tmp", "w") as f:
        f.write("".join(r + "\n" for r in sorted(files)))
    os.replace(manifest + ".tmp", manifest)
    return len(files)


# --- the whole import ----------------------------------------------------------


REQUIRED = ("firmware", "touch", "sensors")
OPTIONAL = ("camera", "power")


def build_all(img, work, helpers, daemon_dir, stage):
    """Every part into stage/ (a tree laid out like the root). Returns
    (made, warnings); raises on a required part."""
    made, warnings = {}, []
    fw = os.path.join(stage, FIRMWARE)
    steps = [
        ("firmware", lambda: build_firmware(img, work, helpers, fw)),
        ("camera", lambda: build_camera(img, work, helpers, fw)),
        ("touch", lambda: build_touch(img, work, daemon_dir, os.path.join(stage, TOUCH))),
        ("sensors", lambda: build_sensors(img, work, os.path.join(stage, SENSORS))),
        ("power", lambda: build_power(img, work, helpers, os.path.join(stage, POWER))),
    ]
    for name, fn in steps:
        log("== %s" % name)
        try:
            made[name] = fn()
        except (StockError, OSError, subprocess.SubprocessError, zipfile.BadZipFile) as e:
            if name in REQUIRED:
                raise StockError("%s: %s" % (name, e)) from e
            warnings.append("%s: %s" % (name, e))
            log("  skipped: %s" % e)
    return made, warnings


def install(stage, root, made):
    st = os.path.join(root, STATE)
    os.makedirs(st, exist_ok=True)
    n = merge_firmware(
        os.path.join(stage, FIRMWARE),
        os.path.join(root, FIRMWARE),
        os.path.join(st, "firmware.list"),
    )
    log("  %d firmware files in /%s" % (n, FIRMWARE))
    for rel, part in ((TOUCH, "touch"), (SENSORS, "sensors"), (POWER, "power")):
        if part in made:
            replace_dir(os.path.join(stage, rel), os.path.join(root, rel))
            log("  /%s" % rel)


def write_state(root, img, made, warnings):
    st = os.path.join(root, STATE)
    os.makedirs(st, exist_ok=True)
    data = {
        "imported": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "slot": img.slot,
        "stock_release": img.release() or "unknown",
        "parts": sorted(made),
        "warnings": warnings,
    }
    with open(os.path.join(st, "imported.tmp"), "w") as f:
        json.dump(data, f, indent=1, sort_keys=True)
        f.write("\n")
    os.replace(os.path.join(st, "imported.tmp"), os.path.join(st, "imported"))
    return data


def free_bytes(path):
    st = os.statvfs(path)
    return st.f_bavail * st.f_frsize


def import_from(img, root, helpers, daemon_dir, work_parent, keep_work=False):
    """Build every part from img and install it under root."""
    work = tempfile.mkdtemp(prefix="work.", dir=work_parent)
    try:
        stage = os.path.join(work, "stage")
        os.makedirs(stage)
        made, warnings = build_all(img, work, helpers, daemon_dir, stage)
        log("== install")
        install(stage, root, made)
        return write_state(root, img, made, warnings)
    finally:
        if not keep_work:
            shutil.rmtree(work, ignore_errors=True)


def device_import(
    root, helpers, daemon_dir, slot=None, keep_work=False, bylabel="/dev/disk/by-partlabel"
):
    """The phone: try the active slot, then the other one."""
    order = [slot] if slot else slot_order(boot_attrs(bylabel))
    work_parent = os.path.join(root, STATE)
    os.makedirs(work_parent, exist_ok=True)
    need_space = sum(KNOWN[n][0] for n in SUPER_PARTS) + (1 << 30)
    if free_bytes(work_parent) < need_space:
        raise StockError(
            "only %d MiB free on the root file system, %d MiB needed (has it grown to its partition?)"
            % (free_bytes(work_parent) >> 20, need_space >> 20)
        )
    errors = []
    for s in order:
        log("== slot %s" % s)
        images_dir = tempfile.mkdtemp(prefix="images.", dir=work_parent)
        try:
            img = Images.from_device(s, images_dir, bylabel)
            rel = img.release()
            if rel:
                log("  the stock images of %s" % rel)
            else:
                bad = [n for n, (_size, d) in KNOWN.items() if img.hashes.get(n) != d]
                log(
                    "  WARNING: not the stock release OpenDuo is tested with (%s; differs: %s)"
                    % (KNOWN_RELEASE, ", ".join(bad))
                )
            return import_from(img, root, helpers, daemon_dir, work_parent, keep_work)
        except (StockError, OSError) as e:
            log("  slot %s: %s" % (s, e))
            errors.append("slot %s: %s" % (s, e))
        finally:
            if not keep_work:
                shutil.rmtree(images_dir, ignore_errors=True)
    raise StockError("; ".join(errors))


def device_check(bylabel="/dev/disk/by-partlabel"):
    """Read only: which slot, and do its images match the known release."""
    attrs = boot_attrs(bylabel)
    out = {"active": [s for s in ("a", "b") if s in attrs and slot_active(attrs[s])], "slots": {}}
    for s in slot_order(attrs):
        try:
            img = Images.from_device(s, None, bylabel, copy=False)
            out["slots"][s] = {
                "release": img.release() or "unknown",
                "matches": sorted(n for n, (_size, d) in KNOWN.items() if img.hashes.get(n) == d),
            }
        except (StockError, OSError) as e:
            out["slots"][s] = {"error": str(e)}
    return out
