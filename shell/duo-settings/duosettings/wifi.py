"""WiFi through wpa_supplicant's control interface (/run/wpa_supplicant/wlan0,
group netdev). The parsers are pure (tests/test_wifi.py).

Secrets: a passphrase goes into wpa_supplicant and nowhere else. For WPA2 it
is turned into the 256-bit PSK here (PBKDF2, as wpa_passphrase does), so
wpa_supplicant's config keeps only the derived key; WPA3 (SAE) needs the
passphrase itself, which wpa_supplicant then keeps in its own config. Nothing
is logged, nothing is kept in memory after the request.
"""

import hashlib
import os
import socket
import tempfile

CTRL_DIR = "/run/wpa_supplicant"


def decode_ssid(text):
    """wpa_supplicant prints SSIDs with printf escapes (\\xNN, \\\\, \\")."""
    out = bytearray()
    i = 0
    while i < len(text):
        c = text[i]
        if c == "\\" and i + 1 < len(text):
            n = text[i + 1]
            if n == "x" and i + 3 < len(text):
                try:
                    out.append(int(text[i + 2 : i + 4], 16))
                    i += 4
                    continue
                except ValueError:
                    pass
            out.extend({"n": b"\n", "r": b"\r", "t": b"\t", "e": b"\x1b"}.get(n, n.encode()))
            i += 2
            continue
        out.extend(c.encode())
        i += 1
    return out.decode("utf-8", "replace")


def security(flags):
    """'open', 'wpa2', 'wpa3', 'wpa2/wpa3', 'enterprise' or 'wep' from the
    scan flags ([WPA2-PSK-CCMP][ESS]...)."""
    f = flags.upper()
    if "EAP" in f:
        return "enterprise"
    sae = "SAE" in f
    psk = "PSK" in f
    if sae and psk:
        return "wpa2/wpa3"
    if sae:
        return "wpa3"
    if psk:
        return "wpa2"
    if "WEP" in f:
        return "wep"
    return "open"


def bars(dbm):
    """0..4 signal bars (the status bar's thresholds)."""
    for limit, n in ((-55, 4), (-65, 3), (-75, 2), (-85, 1)):
        if dbm >= limit:
            return n
    return 0


def parse_scan(text):
    """SCAN_RESULTS -> one entry per SSID (the strongest BSS), strongest
    first: {ssid, signal, freq, security}. Hidden networks are left out."""
    best = {}
    for line in text.splitlines()[1:]:
        parts = line.split("\t")
        if len(parts) < 5:
            continue
        bssid, freq, level, flags, raw = parts[:5]
        ssid = decode_ssid(raw)
        if not ssid or ssid.startswith("\x00"):
            continue
        try:
            level = int(level)
            freq = int(freq)
        except ValueError:
            continue
        cur = best.get(ssid)
        if cur is None or level > cur["signal"]:
            best[ssid] = {
                "ssid": ssid,
                "signal": level,
                "freq": freq,
                "security": security(flags),
                "bssid": bssid,
            }
    return sorted(best.values(), key=lambda n: -n["signal"])


def parse_networks(text):
    """LIST_NETWORKS -> [{id, ssid, current, disabled}]."""
    out = []
    for line in text.splitlines()[1:]:
        parts = line.split("\t")
        if len(parts) < 2 or not parts[0].isdigit():
            continue
        flags = parts[3] if len(parts) > 3 else ""
        out.append(
            {
                "id": int(parts[0]),
                "ssid": decode_ssid(parts[1]),
                "current": "[CURRENT]" in flags,
                "disabled": "[DISABLED]" in flags,
                "temp_disabled": "[TEMP-DISABLED]" in flags,
            }
        )
    return out


def parse_status(text):
    status = {}
    for line in text.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            status[k] = v
    if "ssid" in status:
        status["ssid"] = decode_ssid(status["ssid"])
    return status


def ssid_hex(ssid):
    return ssid.encode("utf-8").hex()


def wpa_psk(ssid, passphrase):
    """The WPA2 PSK for a passphrase (IEEE 802.11i: PBKDF2-SHA1, 4096 rounds,
    the SSID as the salt), 64 hex digits."""
    return hashlib.pbkdf2_hmac(
        "sha1", passphrase.encode("utf-8"), ssid.encode("utf-8"), 4096, 32
    ).hex()


def passphrase_problem(passphrase, sec):
    """None if `passphrase` can be used for a network of security `sec`,
    otherwise a short reason."""
    if sec in ("wpa2", "wpa2/wpa3", "wpa3"):
        if not 8 <= len(passphrase) <= 63:
            return "length"
        if any(ord(c) < 32 or ord(c) == 127 for c in passphrase):
            return "chars"
        if sec == "wpa3" and '"' in passphrase:
            return "chars"
    return None


def network_commands(nid, ssid, sec, passphrase):
    """SET_NETWORK commands for a new network (the secret is in them: never
    log these)."""
    cmds = ["SET_NETWORK %d ssid %s" % (nid, ssid_hex(ssid)), "SET_NETWORK %d scan_ssid 0" % nid]
    if sec == "open":
        cmds.append("SET_NETWORK %d key_mgmt NONE" % nid)
    elif sec in ("wpa2", "wpa2/wpa3"):
        cmds.append("SET_NETWORK %d key_mgmt WPA-PSK" % nid)
        cmds.append("SET_NETWORK %d psk %s" % (nid, wpa_psk(ssid, passphrase)))
    elif sec == "wpa3":
        cmds.append("SET_NETWORK %d key_mgmt SAE" % nid)
        cmds.append("SET_NETWORK %d ieee80211w 2" % nid)
        cmds.append('SET_NETWORK %d sae_password "%s"' % (nid, passphrase))
    else:
        raise ValueError("unsupported security: %s" % sec)
    return cmds


class WpaCtrl:
    """One request, one answer, on wpa_supplicant's control socket."""

    def __init__(self, iface="wlan0"):
        self.path = os.path.join(CTRL_DIR, iface)
        self.sock = None
        self.local = None

    def available(self):
        return os.path.exists(self.path)

    def open(self):
        if self.sock:
            return
        d = tempfile.mkdtemp(prefix="duo-settings-wpa-")
        os.chmod(d, 0o755)
        self.local = os.path.join(d, "s")
        s = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        s.bind(self.local)
        os.chmod(self.local, 0o777)
        s.connect(self.path)
        s.settimeout(4)
        self.sock = s

    def close(self):
        if self.sock:
            self.sock.close()
            self.sock = None
        if self.local:
            try:
                os.unlink(self.local)
                os.rmdir(os.path.dirname(self.local))
            except OSError:
                pass
            self.local = None

    def request(self, cmd):
        self.open()
        self.sock.send(cmd.encode())
        while True:
            data = self.sock.recv(65536).decode("utf-8", "replace")
            if not data.startswith("<"):  # unsolicited events start with <N>
                return data

    def ok(self, cmd):
        return self.request(cmd).strip() == "OK"
