#!/usr/bin/python3 -I
"""Generate the Python bindings duo-lock needs (ext-session-lock-v1 and
viewporter) and the clipboard bridge's (ext-data-control-v1) with
pywayland's scanner, into DIR (default
/usr/local/lib/duo-shell/protocols): DIR/ext_session_lock_v1/,
DIR/viewporter/, and DIR/wayland -> pywayland's own core bindings (the
generated code imports wl_surface and wl_output from there).

Run by install.sh on the phone (needs python3-pywayland, wayland-protocols).
"""

import os
import sys

import pywayland.protocol.wayland as core
from pywayland.scanner import Protocol

XML = [
    "/usr/share/wayland-protocols/staging/ext-session-lock/ext-session-lock-v1.xml",
    "/usr/share/wayland-protocols/stable/viewporter/viewporter.xml",
    "/usr/share/wayland-protocols/staging/ext-data-control/ext-data-control-v1.xml",
]


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "/usr/local/lib/duo-shell/protocols"
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "__init__.py"), "w"):
        pass
    link = os.path.join(out, "wayland")
    if not os.path.islink(link):
        os.symlink(os.path.dirname(core.__file__), link)
    protocols = [Protocol.parse_file(x) for x in XML]
    imports = {name[len("Wl") :] if False else name: "wayland" for name in []}
    # The core interfaces the generated code refers to live in ./wayland.
    for module in os.listdir(os.path.dirname(core.__file__)):
        if module.endswith(".py") and module != "__init__.py":
            imports[module[:-3]] = "wayland"
    for protocol in protocols:
        for interface in protocol.interface:
            imports[interface.name] = protocol.name
    for protocol in protocols:
        protocol.output(out, imports)
    print("generated %s in %s" % (", ".join(p.name for p in protocols), out))


if __name__ == "__main__":
    main()
