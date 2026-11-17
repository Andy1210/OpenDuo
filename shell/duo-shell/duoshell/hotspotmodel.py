"""The quick menu's Hotspot tile and the WiFi tile while the hotspot holds
the phone's own WiFi (pure, tests/test_hotspotmodel.py).

The state comes from duo2-hotspotd's /run/duo2/hotspot.json (hotspot/README.md:
world-readable, no password, no device address in it)."""

import json
import os

HELPER = "/usr/local/sbin/duo2-hotspot"
STATE = "/run/duo2/hotspot.json"
ICON = "network-wireless-hotspot-symbolic"


def installed(helper=HELPER):
    return os.path.exists(helper)


def read_state(path=STATE):
    try:
        with open(path) as f:
            st = json.load(f)
        return st if isinstance(st, dict) else {}
    except (OSError, ValueError):
        return {}


def wifi_on(st):
    return bool(st) and st.get("state") == "on" and bool(st.get("wifi"))


def tile(st, available, airplane):
    """(sensitive, on, label, count): label is one of the English labels
    below (the caller translates), count the devices for "%d devices"."""
    if not available:
        return False, False, "Not installed", 0
    if airplane:
        return False, False, "Airplane mode", 0
    if not wifi_on(st):
        return True, False, "Off", 0
    n = int(st.get("client_count") or 0)
    wifi_devices = (
        len([c for c in st.get("clients") or [] if c.get("via") == "wifi"])
        if st.get("clients")
        else n
    )
    if not st.get("uplink"):
        return True, True, "No mobile data", wifi_devices
    if wifi_devices:
        return True, True, "devices", wifi_devices
    return True, True, "On", 0


def wifi_held(st):
    """Whether the hotspot holds the phone's own WiFi client off (the user had
    it off, or let it be switched off): the WiFi tile then says so, and a tap
    asks the hotspot for it back instead of switching the radio."""
    return wifi_on(st) and (st.get("wifi") or {}).get("client") in ("radio", "cut")


def toggle_words(st):
    """The helper's words for a tap on the tile."""
    return ["off"] if wifi_on(st) else ["on"]


def needs_wifi_off(reply):
    """The helper's answer to `on` says the hotspot cannot run beside the
    WiFi connection, and the user may switch it off for it."""
    return (
        isinstance(reply, dict)
        and reply.get("ok") is False
        and reply.get("code") == "needs-wifi-off"
    )


def parse_reply(out):
    try:
        data = json.loads((out or "").strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"ok": False, "error": "no answer"}
    return data if isinstance(data, dict) else {"ok": False, "error": "no answer"}
