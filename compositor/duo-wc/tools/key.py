#!/usr/bin/python3
"""A synthetic key press on the phone, for testing without fingers (as root):

    sudo tools/key.py power [hold-ms]      the power key (pmic_pwrkey), default 120 ms
    sudo tools/key.py volup|voldown [ms]   the volume keys (gpio-keys, pmic_resin)

Like touch.py: the device is cloned with evemu-device (same name and codes,
so duo-wc treats it like the real key) and the press and release are written
into the clone with the planned timing. The clone is removed at the end.
Needs evemu-tools.
"""

import os
import re
import struct
import subprocess
import sys
import tempfile
import time

KEYS = {"power": ("pmic_pwrkey", 116), "volup": ("gpio-keys", 115), "voldown": ("pmic_resin", 114)}
EV_SYN, EV_KEY = 0, 1


def find_device(name):
    for entry in sorted(os.listdir("/sys/class/input")):
        if not entry.startswith("event"):
            continue
        try:
            with open("/sys/class/input/%s/device/name" % entry) as f:
                if f.read().strip() == name:
                    return "/dev/input/" + entry
        except OSError:
            continue
    raise SystemExit("no input device named %r" % name)


def write(dev, kind, code, value):
    dev.write(struct.pack("qqHHi", 0, 0, kind, code, value))


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in KEYS:
        raise SystemExit(__doc__)
    name, code = KEYS[sys.argv[1]]
    hold = float(sys.argv[2]) / 1000.0 if len(sys.argv) > 2 else 0.12
    source = find_device(name)
    description = subprocess.run(
        ["evemu-describe", source], check=True, capture_output=True, text=True
    ).stdout
    with tempfile.NamedTemporaryFile("w", suffix=".desc", delete=False) as d:
        d.write(description)
        desc_path = d.name
    clone = subprocess.Popen(["evemu-device", desc_path], stdout=subprocess.PIPE, text=True)
    try:
        m = re.search(r"(/dev/input/event\d+)", clone.stdout.readline())
        if not m:
            raise SystemExit("evemu-device failed")
        time.sleep(1.0)  # udev + libinput pick the new device up
        with open(m.group(1), "wb", buffering=0) as dev:
            write(dev, EV_KEY, code, 1)
            write(dev, EV_SYN, 0, 0)
            time.sleep(hold)
            write(dev, EV_KEY, code, 0)
            write(dev, EV_SYN, 0, 0)
        time.sleep(0.3)
    finally:
        clone.terminate()
        clone.wait()
        os.unlink(desc_path)


if __name__ == "__main__":
    main()
