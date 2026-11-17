"""Developer mode's settings (/etc/duo-devmode.conf). Pure.

    developer_mode = on | off   the page's main switch: off turns ssh and
                                USB networking off with it
    usb_network = on | off      the USB development network (usb0), with
                                developer_mode on only

ssh itself has no line here: sshd's own unit being enabled is the state.
Without the file both are on, as it was before the switch existed (an
installation from before); the image ships the file with both off. The USB
gadget script reads the same two lines (rootfs/.../duo2-usb-gadget).
"""

KEYS = ("developer_mode", "usb_network")
LEGACY = {"developer_mode": True, "usb_network": True}  # no file at all

HEADER = """\
# Developer mode (Settings > Developer; devmode/README.md). Written by
# duo-devmode: change it there (sudo duo-devmode mode|usb on|off).
"""


def parse(text):
    """{key: bool} from the file's text (None: no file). The last valid line
    of a key wins; a key with no valid line is off."""
    if text is None:
        return dict(LEGACY)
    out = dict.fromkeys(KEYS, False)
    for line in text.splitlines():
        line = line.split("#", 1)[0]
        if "=" not in line:
            continue
        key, value = (part.strip() for part in line.split("=", 1))
        if key in KEYS and value in ("on", "off"):
            out[key] = value == "on"
    return out


def render(conf):
    return HEADER + "".join("%s = %s\n" % (k, "on" if conf.get(k) else "off") for k in KEYS)


def effective(conf):
    """What is in force: USB networking needs developer mode."""
    return {
        "developer_mode": bool(conf["developer_mode"]),
        "usb_network": bool(conf["developer_mode"] and conf["usb_network"]),
    }
