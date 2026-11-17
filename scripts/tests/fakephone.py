#!/usr/bin/env python3
"""A pretend Surface Duo 2 for scripts/install-openduo.sh's tests: one script
that answers as `adb` and as `fastboot` (the link's name says which), with
the phone's state in $FAKEPHONE (a directory):

    state.json   mode (android, fastboot, recovery, off), serial, unlocked,
                 product, the sda table, other devices on the bus;
                 noperm: the modes in which this user may not open the
                 phone (no udev rule), slow: seconds per MiB copied
    blk/NAME     the partitions' contents (by-name and sdaN)
    log          every invocation, one per line (the tests read it)

It knows only the commands the installer sends; anything else is an error,
so a new command shows up in the tests first.
"""

import hashlib
import json
import os
import re
import shlex
import sys
import time

D = os.environ["FAKEPHONE"]
MIB = 1 << 20
# what adb and fastboot print for a device they may not open (platform-tools)
NOPERM = (
    "no permissions (missing udev rules? user is in the plugdev group); "
    "see [http://developer.android.com/tools/device.html]"
)


def noperm(st):
    return st["mode"] in st.get("noperm", [])


def copy(src, dst, st):
    """src to dst by MiB, as slowly as the state says."""
    while True:
        data = src.read(MIB)
        if not data:
            return
        dst.write(data)
        dst.flush()
        if st.get("slow"):
            time.sleep(st["slow"])


def load():
    with open(os.path.join(D, "state.json")) as f:
        return json.load(f)


def save(st):
    with open(os.path.join(D, "state.json"), "w") as f:
        json.dump(st, f, indent=1)


def blk(name):
    return os.path.join(D, "blk", name)


def dev_path(st, dev):
    """/dev/block/by-name/X or /dev/block/sdaN -> the file holding it."""
    m = re.fullmatch(r"/dev/block/by-name/([\w.-]+)", dev)
    if m:
        name = m.group(1)
        if os.path.exists(blk(name)):
            return blk(name)
        for p in st["sda"]:
            if p["name"] == name:
                path = blk("sda%d" % p["num"])
                if not os.path.exists(path):  # an empty partition
                    open(path, "wb").close()
                return path
        return blk(name)
    m = re.fullmatch(r"/dev/block/(sda\d+)", dev)
    if m:
        return blk(m.group(1))
    raise SystemExit("fakephone: unknown device %s" % dev)


def names(st):
    out = sorted(n for n in os.listdir(os.path.join(D, "blk")) if not re.fullmatch(r"sda\d+", n))
    return sorted(set(out) | {p["name"] for p in st["sda"]})


def parted_m(st):
    lines = ["BYT;", "/dev/block/sda:%ds:scsi:4096:4096:gpt:FAKE UFS:;" % st["sda_sectors"]]
    for p in sorted(st["sda"], key=lambda p: p["num"]):
        lines.append(
            "%d:%ds:%ds:%ds:%s:%s:%s;"
            % (
                p["num"],
                p["start"],
                p["end"],
                p["end"] - p["start"] + 1,
                p.get("fs", ""),
                p["name"],
                p.get("flags", ""),
            )
        )
    return "\n".join(lines) + "\n"


def parted(st, args):
    """The few parted command lines the installer uses."""
    a = [x for x in args if x not in ("-s", "-m")]
    if a[:2] == ["-a", "none"]:
        a = a[2:]
    dev, cmd = a[0], a[1:]
    if cmd == ["unit", "B", "print"]:
        return "Model: FAKE (scsi)\nDisk %s\n" % dev
    if dev != "/dev/block/sda":
        if cmd == ["unit", "s", "print"]:
            return "BYT;\n%s:1000s:scsi:4096:4096:gpt:FAKE:;\n" % dev
        raise SystemExit("fakephone: parted on %s: %s" % (dev, cmd))
    if cmd == ["unit", "s", "print"]:
        return parted_m(st)
    if cmd[0] == "rm":
        st["sda"] = [p for p in st["sda"] if p["num"] != int(cmd[1])]
        return ""
    if cmd[0] == "mkpart":
        name, fs, start, end = cmd[1], cmd[2], int(cmd[3].rstrip("s")), int(cmd[4].rstrip("s"))
        for p in st["sda"]:
            if not (end < p["start"] or start > p["end"]):
                raise SystemExit("fakephone: parted: overlaps %s" % p["name"])
        used = {p["num"] for p in st["sda"]}
        num = min(n for n in range(1, 129) if n not in used)
        st["sda"].append({"num": num, "name": name, "start": start, "end": end, "fs": fs})
        open(blk("sda%d" % num), "wb").close()
        return ""
    if cmd[0] == "set" and cmd[2:] == ["esp", "on"]:
        for p in st["sda"]:
            if p["num"] == int(cmd[1]):
                p["flags"] = "boot, esp"
        return ""
    raise SystemExit("fakephone: parted %s" % cmd)


def sha_file(path, size=None):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        data = f.read() if size is None else f.read(size)
    if size is not None and len(data) < size:
        data += b"\0" * (size - len(data))
    h.update(data)
    return h.hexdigest()


def shell(st, line):
    line = line.strip()
    if line == "ls /dev/block/by-name/":
        # Like the WOA recovery: the whole disks are listed by name too.
        return "\r\n".join(names(st) + ["sda", "sdb"]) + "\r\n"
    if line == "ls /dev/block/":
        return "by-name\r\nsda\r\nsdb\r\nsdg\r\nloop0\r\n"
    if line in ("chmod 755 /tmp/parted", "sync"):
        return ""
    if line.startswith("/tmp/parted "):
        if not os.path.exists(os.path.join(D, "parted-pushed")):
            raise SystemExit("fakephone: /tmp/parted: not found")
        out = parted(st, shlex.split(line)[1:])
        save(st)
        return out
    m = re.fullmatch(
        r"dd if=/tmp/openduo-write.img of=(\S+) bs=4194304 2>/dev/null; rm -f /tmp/openduo-write.img",
        line,
    )
    if m:
        tmp = os.path.join(D, "tmp-write.img")
        with open(tmp, "rb") as src, open(dev_path(st, m.group(1)), "r+b") as dst:
            dst.write(src.read())
        os.remove(tmp)
        return ""
    m = re.fullmatch(r"blockdev --getsize64 (\S+)", line)
    if m:
        return "%d\n" % os.path.getsize(dev_path(st, m.group(1)))
    m = re.fullmatch(r"sha256sum (\S+)", line)
    if m:
        return "%s  %s\n" % (sha_file(dev_path(st, m.group(1))), m.group(1))
    m = re.fullmatch(r"head -c (\d+) (\S+) \| sha256sum", line)
    if m:
        return "%s  -\n" % sha_file(dev_path(st, m.group(2)), int(m.group(1)))
    m = re.fullmatch(r"\[ -b (/dev/block/sda\d+) \] && echo yes", line)
    if m:
        return "yes\n" if os.path.exists(dev_path(st, m.group(1))) else ""
    raise SystemExit("fakephone: shell: %r" % line)


def adb(st, argv):
    if argv[:1] == ["version"]:
        return print("Android Debug Bridge version 1.0.41 (fake)")
    if argv[:1] == ["devices"]:
        print("List of devices attached")
        for o in st.get("others", []):
            print(
                "%s\tdevice%s"
                % (
                    o,
                    " usb:1-1 product:dreamltexx model:SM_G950F device:dreamlte transport_id:3"
                    if argv[1:] == ["-l"]
                    else "",
                )
            )
        if st["mode"] in ("android", "recovery") and noperm(st):
            print(
                "%s\t%s%s" % (st["serial"], NOPERM, " usb:1-2 transport_id:9" if argv[1:] else "")
            )
        elif st["mode"] in ("android", "recovery"):
            state = "device" if st["mode"] == "android" else "recovery"
            extra = ""
            if argv[1:] == ["-l"]:
                extra = (
                    " usb:1-2 product:duo2-eea model:Surface_Duo_2 device:duo2 transport_id:7"
                    if st["mode"] == "android"
                    else " usb:1-2 product:twrp_surfaceduo2 model:TWRP device:recovery transport_id:8"
                )
            print("%s\t%s%s" % (st["serial"], state, extra))
        return None
    if argv[:1] in (["kill-server"], ["start-server"]):
        raise SystemExit("fakephone: the adb server must never be restarted")
    if argv[:1] != ["-s"] or argv[1] != st["serial"]:
        raise SystemExit("fakephone: adb without -s %s: %s" % (st["serial"], argv))
    cmd = argv[2:]
    if st["mode"] not in ("android", "recovery"):
        raise SystemExit("fakephone: adb: device '%s' not found" % argv[1])
    if noperm(st):
        raise SystemExit("fakephone: adb: insufficient permissions for device")
    if cmd == ["reboot", "bootloader"]:
        st["mode"] = "fastboot"
    elif cmd == ["reboot"]:
        st["mode"] = "off"
    elif st["mode"] != "recovery":
        raise SystemExit("fakephone: in Android only reboot is expected: %s" % cmd)
    elif cmd[0] == "push" and cmd[2] == "/tmp/parted":
        open(os.path.join(D, "parted-pushed"), "w").close()
        print("%s: 1 file pushed." % cmd[1])
    elif cmd[0] == "push" and cmd[2] == "/tmp/openduo-write.img":
        with open(cmd[1], "rb") as src, open(os.path.join(D, "tmp-write.img"), "wb") as dst:
            dst.write(src.read())
        print("%s: 1 file pushed." % cmd[1])
    elif cmd[0] == "shell":
        sys.stdout.write(shell(st, " ".join(cmd[1:])))
        return st
    elif cmd[0] == "exec-out":
        if cmd[1] == "dd if=/dev/block/sda bs=4096 count=6 2>/dev/null":
            sys.stdout.buffer.write(b"GPT" * 8192)
            return st
        m = re.fullmatch(r"dd if=(\S+) bs=4194304 2>/dev/null", cmd[1])
        if not m:
            raise SystemExit("fakephone: exec-out %r" % cmd[1])
        with open(dev_path(st, m.group(1)), "rb") as f:
            copy(f, sys.stdout.buffer, st)
    elif cmd[0] == "exec-in":
        m = re.fullmatch(r"dd of=(\S+) bs=4194304 2>/dev/null", cmd[1])
        if not m:
            raise SystemExit("fakephone: exec-in %r" % cmd[1])
        path = dev_path(st, m.group(1))
        name = os.path.basename(path)
        if name in ("modemst1", "modemst2", "fsg", "fsc", "persist", "sfpd", "super"):
            raise SystemExit("fakephone: WRITE TO %s" % name)
        with open(path, "wb") as f:
            copy(sys.stdin.buffer, f, st)
    else:
        raise SystemExit("fakephone: adb %s" % cmd)
    return st


def fastboot(st, argv):
    if argv[:1] == ["--version"]:
        return print("fastboot version 37.0.0 (fake)")
    if argv == ["devices"]:
        if st["mode"] == "fastboot":
            print("%s\tfastboot" % (NOPERM if noperm(st) else st["serial"]))
        return None
    if argv[:1] != ["-s"] or argv[1] != st["serial"] or st["mode"] != "fastboot":
        raise SystemExit("fakephone: fastboot %s (mode %s)" % (argv, st["mode"]))
    if noperm(st):
        # the real one waits for ever ("< waiting for SERIAL >")
        raise SystemExit("fakephone: fastboot -s on a device it may not open")
    cmd = argv[2:]
    if cmd == ["getvar", "product"]:
        sys.stderr.write("product: %s\nFinished. Total time: 0.001s\n" % st["product"])
    elif cmd == ["getvar", "unlocked"]:
        sys.stderr.write("unlocked: %s\nFinished. Total time: 0.001s\n" % st["unlocked"])
    elif cmd[0] == "boot":
        if os.path.basename(cmd[1]) != "surfaceduo2-headless.img":
            raise SystemExit("fakephone: fastboot boot %s" % cmd[1])
        st["mode"] = "recovery"
    else:
        raise SystemExit("fakephone: fastboot %s" % cmd)
    return st


def main():
    tool = os.path.basename(sys.argv[0])
    with open(os.path.join(D, "log"), "a") as f:
        f.write("%s %s\n" % (tool, " ".join(sys.argv[1:])))
    st = load()
    new = (fastboot if tool == "fastboot" else adb)(st, sys.argv[1:])
    if new is not None:
        save(new)


if __name__ == "__main__":
    main()
