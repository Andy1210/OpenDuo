#!/usr/bin/env python3
"""Read the camera module descriptions (CamX "Chromatix" binaries) of the stock vendor image.

The files are com.qti.sensormodule.<name>.bin under /vendor/lib64/camera of the phone's own
vendor.img. They stay out of git: this script only prints what is in them (docs/hardware/camera.md).

  camx-module-dump.py strings FILE        names: sensor, EEPROM, actuator, OIS, flash
  camx-module-dump.py modes FILE          every sensor mode: output size, lanes, link rate
  camx-module-dump.py regs FILE [ID,...]  register lists (address=data, +delay)
  camx-module-dump.py power FILE          power sequences (type value delay-ms)

Format (reverse engineered): "QTI Chromatix Header", then from 0xcc 56-byte schema entries
{u32 id; char name[32]; u32 a, b; i32 parent; u32 offset; u32 size}; the data starts right after
the schema, values are interned (equal values share one offset). A register list is a blob in
which each register is the id of an entry named "slaveAddr" followed by the 16-bit address; its
data is the value of the next entry ("registerData"), its delay the one after ("delayUs").
"""

import re
import struct
import sys

POWER_TYPES = [
    "MCLK",
    "VANA",
    "VDIG",
    "VIO",
    "VAF",
    "VAF_PWDM",
    "CUSTOM_REG1",
    "CUSTOM_REG2",
    "RESET",
    "STANDBY",
    "CUSTOM_GPIO1",
    "CUSTOM_GPIO2",
    "VANA1",
]


def load(path):
    d = open(path, "rb").read()
    if not d.startswith(b"QTI Chromatix Header"):
        sys.exit("%s: not a Chromatix binary" % path)
    off, ents, order = 0xCC, {}, []
    while off + 56 <= len(d):
        (i,) = struct.unpack_from("<I", d, off)
        nm = d[off + 4 : off + 36].split(b"\0")[0]
        if not re.fullmatch(rb"[A-Za-z_\[\]0-9]+", nm or b"!"):
            break
        _, _, _, dd, sz = struct.unpack_from("<IIiII", d, off + 36)
        ents[i] = (nm.decode(), dd, sz)
        order.append(i)
        off += 56
    return d, off, ents, order


def raw(d, base, ent):
    _, dd, sz = ent
    return d[base + dd : base + dd + sz]


def scalar(d, base, ents, i):
    if i not in ents:
        return None
    b = raw(d, base, ents[i])
    return int.from_bytes(b, "little") if len(b) in (1, 2, 4) else None


def reglists(d, base, ents, order, want=None):
    for i in order:
        nm, _, sz = ents[i]
        if nm != "regSetting" or not sz or (want and i not in want):
            continue
        blob = raw(d, base, ents[i])
        regs = []
        for p in range(0, len(blob) - 8, 4):
            t, a = struct.unpack_from("<II", blob, p)
            if (
                t in ents
                and ents[t][0] == "slaveAddr"
                and t + 1 in ents
                and ents[t + 1][0] == "registerData"
                and a <= 0xFFFF
            ):
                dl = scalar(d, base, ents, t + 2) if ents.get(t + 2, ("",))[0] == "delayUs" else 0
                regs.append((a, scalar(d, base, ents, t + 1) or 0, dl or 0))
        yield i, sz, regs


def cmd_strings(d, base, ents, order):
    for i in order:
        nm, _, sz = ents[i]
        b = raw(d, base, ents[i])
        if sz > 1 and b.endswith(b"\0") and re.fullmatch(rb"[ -~]+\0", b):
            print("%5d %-24s %s" % (i, nm, b[:-1].decode()))


def cmd_regs(d, base, ents, order, want):
    for i, sz, regs in reglists(d, base, ents, order, want):
        print("regSetting id=%d (%d bytes, %d registers)" % (i, sz, len(regs)))
        print(
            "  "
            + " ".join(
                "%04x=%02x" % (a, v) + ("(+%dus)" % dl if dl else "")
                for a, v, dl in regs
                if a != 0x0001
            )
        )


def cmd_modes(d, base, ents, order, mclk=19.2):
    for i, _, regs in reglists(d, base, ents, order):
        r = {a: v for a, v, _ in regs if a != 0x0001}
        if 0x034C not in r:
            continue

        def w(a, r=r):  # a 16-bit register of this list
            return (r.get(a, 0) << 8) | r.get(a + 1, 0)

        op = mclk / (r.get(0x030D) or 1) * w(0x030E) / (r.get(0x030B) or 1)
        print(
            "id=%-5d %4dx%-4d binning %02x  lanes %d  fll %d llp %d  %.1f Mb/s per lane"
            % (
                i,
                w(0x034C),
                w(0x034E),
                r.get(0x0901, 0),
                r.get(0x0114, -1) + 1,
                w(0x0340),
                w(0x0342),
                op,
            )
        )


def cmd_power(d, base, ents, order):
    for i in order:
        nm, _, sz = ents[i]
        if nm != "powerSetting" or not sz or sz % 12:
            continue
        v = struct.unpack("<%dI" % (sz // 4), raw(d, base, ents[i]))
        steps = []
        for k in range(0, len(v), 3):
            t, val, dl = v[k : k + 3]
            name = POWER_TYPES[t] if t < len(POWER_TYPES) else str(t)
            steps.append("%s%s/%dms" % (name, "=%d" % val if val else "", dl))
        print("powerSetting id=%d: %s" % (i, " -> ".join(steps)))


def main():
    if len(sys.argv) < 3 or sys.argv[1] not in ("strings", "modes", "regs", "power"):
        sys.exit(__doc__)
    d, base, ents, order = load(sys.argv[2])
    if sys.argv[1] == "regs":
        want = set(int(x, 0) for x in sys.argv[3].split(",")) if len(sys.argv) > 3 else None
        cmd_regs(d, base, ents, order, want)
    else:
        globals()["cmd_" + sys.argv[1]](d, base, ents, order)


if __name__ == "__main__":
    main()
