"""What the status bar shows: battery, WiFi, and the icons for them.

The parsing and the icon choice are pure functions (tests/test_status.py);
the readers touch sysfs and wpa_supplicant's control socket and never raise.

- Battery: /sys/class/power_supply/<battery>/{capacity,status}; the Duo 2's is
  qcom-battmgr-bat (any power supply of type Battery is taken).
- WiFi: wpa_supplicant's control interface (/run/wpa_supplicant/<iface>,
  group netdev): STATUS for the state and SSID, SIGNAL_POLL for the RSSI.
  The image has no NetworkManager and no /proc/net/wireless (no wireless
  extensions in the kernel); sysfs operstate is the fallback.
"""

import os
import socket
import tempfile

POWER_SUPPLY = "/sys/class/power_supply"
WPA_DIR = "/run/wpa_supplicant"


# -- battery -------------------------------------------------------------------


def battery_icon(percent, status):
    """The Adwaita symbolic icon for a battery level (0..100) and its
    status word (Charging, Discharging, Full, Not charging, Unknown)."""
    if percent is None:
        return "battery-missing-symbolic"
    level = max(0, min(100, int(round(percent / 10.0)) * 10))
    charging = status in ("Charging", "Full")
    if level == 100 and charging:
        return "battery-level-100-charged-symbolic"
    return "battery-level-%d%s-symbolic" % (level, "-charging" if charging else "")


def find_battery(root=POWER_SUPPLY):
    """The first power supply of type Battery, or None."""
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return None
    for name in names:
        if _read(os.path.join(root, name, "type")) == "Battery":
            return os.path.join(root, name)
    return None


def read_battery(path):
    """(percent or None, status word or None)."""
    if not path:
        return None, None
    capacity = _read(os.path.join(path, "capacity"))
    try:
        percent = int(capacity) if capacity is not None else None
    except ValueError:
        percent = None
    return percent, _read(os.path.join(path, "status"))


# -- WiFi ----------------------------------------------------------------------


def parse_kv(text):
    """wpa_supplicant's `key=value` lines as a dict."""
    out = {}
    for line in (text or "").splitlines():
        key, sep, value = line.partition("=")
        if sep:
            out[key.strip()] = value.strip()
    return out


def wifi_bars(rssi):
    """0..4 from an RSSI in dBm (None: not known)."""
    if rssi is None:
        return None
    if rssi >= -55:
        return 4
    if rssi >= -65:
        return 3
    if rssi >= -75:
        return 2
    if rssi >= -85:
        return 1
    return 0


WIFI_ICONS = {
    4: "network-wireless-signal-excellent-symbolic",
    3: "network-wireless-signal-good-symbolic",
    2: "network-wireless-signal-ok-symbolic",
    1: "network-wireless-signal-weak-symbolic",
    0: "network-wireless-signal-none-symbolic",
}


def wifi_icon(wifi):
    """The icon for a read_wifi() result."""
    state = wifi.get("state")
    if state == "connected":
        bars = wifi_bars(wifi.get("rssi"))
        if bars is None:
            return "network-wireless-connected-symbolic"
        return WIFI_ICONS[bars]
    if state == "connecting":
        return "network-wireless-acquiring-symbolic"
    if state == "absent":
        return "network-wireless-hardware-disabled-symbolic"
    return "network-wireless-offline-symbolic"


def wifi_from_wpa(status, signal):
    """A read_wifi() result from wpa_supplicant's STATUS and SIGNAL_POLL
    replies (either may be None)."""
    st = parse_kv(status)
    wpa_state = st.get("wpa_state")
    if wpa_state == "COMPLETED":
        state = "connected"
    elif wpa_state in (
        "ASSOCIATING",
        "ASSOCIATED",
        "AUTHENTICATING",
        "4WAY_HANDSHAKE",
        "GROUP_HANDSHAKE",
        "SCANNING",
    ):
        state = "connecting"
    elif wpa_state is None:
        state = None
    else:
        state = "disconnected"
    rssi = None
    sig = parse_kv(signal)
    try:
        rssi = int(sig["RSSI"]) if "RSSI" in sig else None
    except ValueError:
        rssi = None
    if rssi is not None and rssi <= -1000:  # "no signal" sentinel some drivers report
        rssi = None
    return {"state": state, "ssid": st.get("ssid"), "rssi": rssi}


def find_wifi_iface():
    """The first wireless interface (it has a `wireless` or `phy80211` in sysfs)."""
    try:
        names = sorted(os.listdir("/sys/class/net"))
    except OSError:
        return None
    for name in names:
        base = os.path.join("/sys/class/net", name)
        if os.path.exists(os.path.join(base, "phy80211")) or os.path.exists(
            os.path.join(base, "wireless")
        ):
            return name
    return None


class WpaCtrl:
    """A tiny client for wpa_supplicant's control socket (datagrams, the
    client binds its own socket path for the reply)."""

    def __init__(self, iface, ctrl_dir=WPA_DIR, timeout=1.0):
        self.path = os.path.join(ctrl_dir, iface)
        self.timeout = timeout

    def command(self, cmd):
        if not os.path.exists(self.path):
            return None
        local_dir = os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()
        local = os.path.join(local_dir, "duo-shell-wpa-%d" % os.getpid())
        s = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        try:
            try:
                os.unlink(local)
            except OSError:
                pass
            s.bind(local)
            s.settimeout(self.timeout)
            s.connect(self.path)
            s.send(cmd.encode())
            reply = s.recv(4096).decode(errors="replace")
            return reply
        except OSError:
            return None
        finally:
            s.close()
            try:
                os.unlink(local)
            except OSError:
                pass


def read_wifi(iface):
    """{'state': connected|connecting|disconnected|absent, 'ssid', 'rssi'}."""
    if not iface:
        return {"state": "absent", "ssid": None, "rssi": None}
    ctrl = WpaCtrl(iface)
    status = ctrl.command("STATUS")
    if status is not None:
        signal = ctrl.command("SIGNAL_POLL")
        wifi = wifi_from_wpa(status, signal)
        if wifi["state"] is not None:
            return wifi
    # No control socket we may use: what the kernel says about the link.
    oper = _read("/sys/class/net/%s/operstate" % iface)
    return {"state": "connected" if oper == "up" else "disconnected", "ssid": None, "rssi": None}


def _read(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None
