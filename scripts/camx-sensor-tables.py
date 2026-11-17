#!/usr/bin/env python3
"""Turn a stock camera module description into the imx48x driver's register tables.

  camx-sensor-tables.py MODEL MODULE.bin OUT.bin

MODEL is the sensor's model number (0x0481 or 0x0563), MODULE.bin a
com.qti.sensormodule.<name>.bin from the phone's own vendor image
(scripts/build-camera-tables.sh extracts them). OUT.bin is the firmware file
the imx48x driver loads (drivers/media/i2c/imx48x.c describes the layout):
the module's initial settings as the INIT list, and every resolution setting
(the register lists that set an output size) as a MODE list, in the module's
order. Nothing of the module is in this repository; the output stays on the
machine that built it and on the phone.
"""

import importlib.util
import os
import struct
import sys

LIST_INIT = 1
LIST_MODE = 2


def camx():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "camx-module-dump.py")
    spec = importlib.util.spec_from_file_location("camx_module_dump", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    model = int(sys.argv[1], 0)
    m = camx()
    d, base, ents, order = m.load(sys.argv[2])

    # the register list that follows the "initSettings" entry
    init_id = None
    for k, i in enumerate(order):
        if ents[i][0] == "initSettings":
            init_id = next((j for j in order[k + 1 :] if ents[j][0] == "regSetting"), None)
            break
    if init_id is None:
        sys.exit("%s: no initSettings" % sys.argv[2])

    lists = []
    for i, _, regs in m.reglists(d, base, ents, order):
        # 0x0001 is the model number's low byte (read-only): the decoder's
        # false matches on other fields of a register entry land there
        regs = [(a, v, dl) for a, v, dl in regs if a != 0x0001]
        if i == init_id:
            lists.insert(0, (LIST_INIT, regs))
        elif any(a == 0x034C for a, _, _ in regs):
            lists.append((LIST_MODE, regs))
    if not lists or lists[0][0] != LIST_INIT:
        sys.exit("%s: no initial settings found" % sys.argv[2])

    out = bytearray(b"IMX48XT1" + struct.pack("<HHI", model, len(lists), 0))
    for kind, regs in lists:
        out += struct.pack("<HH", kind, len(regs))
        for a, v, dl in regs:
            if v > 0xFF:
                sys.exit("%s: register 0x%04x value 0x%x is not 8-bit" % (sys.argv[2], a, v))
            out += struct.pack("<HBB", a, v, min(255, (dl + 999) // 1000))
    with open(sys.argv[3], "wb") as f:
        f.write(out)
    print(
        "%s: %d mode tables, %d initial settings"
        % (sys.argv[3], sum(1 for k, _ in lists if k == LIST_MODE), len(lists[0][1]))
    )


if __name__ == "__main__":
    main()
