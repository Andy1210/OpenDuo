"""The panels an app may be shown, as the chooser offers them (pure, apart
from `query_outputs`).

duo-wc answers `get_outputs` on its user socket to any program of the GUI
user (state only, docs/hardware/security.md); the chooser asks it which
panels there are, where they are and whether they are lit.
"""

import json
import os
import socket

# What the chooser prints for "both panels" in screenshot mode (the grim
# gate then takes every output).
ALL = "all"


def socket_path():
    """duo-wc's user socket: $DUO_WC_SOCKET (the session sets it), else the
    runtime directory's."""
    return os.environ.get("DUO_WC_SOCKET") or "/run/duo-wc/duo-wc.sock"


def query_outputs(path=None, timeout=3.0):
    """duo-wc's `get_outputs` list, or [] when it does not answer."""
    try:
        with socket.socket(socket.AF_UNIX) as sock:
            sock.settimeout(timeout)
            sock.connect(path or socket_path())
            sock.sendall(b'{"id":1,"request":"get_outputs"}\n')
            data = b""
            while b"\n" not in data:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                data += chunk
    except OSError:
        return []
    try:
        reply = json.loads(data.split(b"\n")[0] or b"{}")
    except ValueError:
        return []
    outputs = (reply.get("ok") or {}).get("outputs")
    return outputs if isinstance(outputs, list) else []


def panels(outputs):
    """[(name, side, lit)] in layout order, left first. `side` is "left" or
    "right" for the two panels side by side, "" otherwise; `lit` whether the
    panel is on."""
    named = [o for o in outputs if isinstance(o, dict) and isinstance(o.get("name"), str)]
    named.sort(key=lambda o: (o.get("x") is None, o.get("x") or 0, o["name"]))
    sides = ["left", "right"] if len(named) == 2 else [""] * len(named)
    return [(o["name"], side, o.get("power") == "on") for o, side in zip(named, sides)]


def choices(outputs, screenshot=False):
    """[(answer, label)] for the chooser's buttons: one per panel, and for a
    screenshot both panels too. The answer is what the chooser prints."""
    found = panels(outputs)
    out = []
    for name, side, lit in found:
        if side == "left":
            label = "Left panel"
        elif side == "right":
            label = "Right panel"
        else:
            label = "Panel %s" % name
        if not lit:
            label += " (dark)"
        out.append((name, label))
    if screenshot and len(found) > 1:
        out.append((ALL, "Both panels"))
    return out


def valid_answer(answer, outputs, screenshot=False):
    """Whether `answer` is one of the choices (a chooser answer is checked
    before it is used)."""
    return any(answer == a for a, _ in choices(outputs, screenshot))
