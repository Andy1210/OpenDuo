"""What the hotspot shows (pure, tests/test_state.py): the devices on it from
hostapd's stations and networkd's DHCP leases, the state file the shell, Duo
Settings and duo2-wwan read (/run/duo2/hotspot.json: world-readable, so no
password and no device address in it).
"""

import ipaddress
import json
import os

STATE = "/run/duo2/hotspot.json"
RUN_DIR = "/run/duo2-hotspot"
WANT = RUN_DIR + "/want.json"


def _ip(v):
    if isinstance(v, str):
        try:
            return str(ipaddress.ip_address(v))
        except ValueError:
            return None
    if isinstance(v, list) and len(v) in (4, 16) and all(isinstance(x, int) for x in v):
        try:
            return str(ipaddress.ip_address(bytes(v)))
        except ValueError:
            return None
    return None


def _mac(v):
    if isinstance(v, str) and len(v.split(":")) == 6:
        return v.lower()
    if isinstance(v, list) and len(v) == 6 and all(isinstance(x, int) and 0 <= x < 256 for x in v):
        return ":".join("%02x" % x for x in v)
    return None


def _chaddr(lease):
    """A lease's hardware address. systemd 257 gives the DHCP message's
    whole chaddr field (16 bytes) with "HardwareAddressLength" beside it:
    the first 6 bytes are an Ethernet / WiFi address."""
    hw = lease.get("HardwareAddress")
    n = lease.get("HardwareAddressLength")
    if (
        isinstance(hw, list)
        and n == 6
        and len(hw) >= 6
        and lease.get("HardwareAddressType", 1) == 1
    ):
        return _mac(hw[:6])
    return _mac(hw)


def parse_leases(networkctl_json):
    """`networkctl --json=short status IFACE` -> [{"ip", "mac", "name"}]
    (systemd 255-257 give the DHCP server's leases with "Address", the
    hardware address and, from some version on, "Hostname"; missing fields
    stay None)."""
    out = []

    def walk(o):
        if isinstance(o, dict):
            leases = o.get("Leases")
            if isinstance(leases, list) and "PoolSize" in o:
                for lease in leases:
                    if not isinstance(lease, dict):
                        continue
                    ip = _ip(lease.get("Address"))
                    if not ip:
                        continue
                    mac = _chaddr(lease)
                    if mac is None:
                        cid = lease.get("ClientId")
                        # client id type 1 (Ethernet) + the 6 bytes
                        if isinstance(cid, list) and len(cid) == 7 and cid[0] == 1:
                            mac = _mac(cid[1:])
                    name = lease.get("Hostname")
                    out.append(
                        {
                            "ip": ip,
                            "mac": mac,
                            "name": name if isinstance(name, str) and name else None,
                        }
                    )
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(networkctl_json)
    return out


def clean_name(name):
    """A device's own name (DHCP option 12) as shown: printable, at most 40."""
    if not isinstance(name, str):
        return None
    name = "".join(c for c in name if c.isprintable()).strip()
    return name[:40] or None


def clients(stations, leases_by_iface, now):
    """The devices on the hotspot, oldest first: WiFi stations (authorized
    only) with their lease's address and name, and the USB host (a lease on
    the USB device). No MAC address goes out of here."""
    out = []
    wifi_leases = {le["mac"]: le for le in leases_by_iface.get("wifi", []) if le.get("mac")}
    for st in stations:
        if not st.get("authorized"):
            continue
        lease = wifi_leases.get(st["mac"], {})
        out.append(
            {
                "via": "wifi",
                "name": clean_name(lease.get("name")),
                "ip": lease.get("ip"),
                "since": int(now - st.get("connected_time", 0)),
            }
        )
    for lease in leases_by_iface.get("usb", []):
        out.append(
            {
                "via": "usb",
                "name": clean_name(lease.get("name")),
                "ip": lease.get("ip"),
                "since": None,
            }
        )
    out.sort(key=lambda c: (c["since"] is None, c["since"] or 0))
    return out


def same_clients(a, b):
    """The same devices (how, name, address), whatever their "since" says:
    it is computed from hostapd's connected time and may move by a second
    between two reads."""

    def key(cl):
        return [(c.get("via"), c.get("name"), c.get("ip")) for c in cl or []]

    return key(a) == key(b)


def write_json(path, data, mode=0o644):
    """Atomically (a reader never sees half a file)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, sort_keys=True)
        f.write("\n")
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def read_json(path):
    try:
        with open(path) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def off_state(reason=None, error=None, code=None, request=None):
    st = {
        "state": "off",
        "wifi": None,
        "usb": None,
        "clients": [],
        "client_count": 0,
        "uplink": None,
        "use_5g": False,
    }
    if reason:
        st["reason"] = reason
    if error:
        st["error"] = error
    if code:
        st["code"] = code
    if request:
        st["request"] = request
    return st
