"""The WiFi side of the hotspot (pure parts, tests/test_wifi.py): what `iw`
says about the chip and the phone's own WiFi connection, the channel the
access point uses, hostapd's configuration and its control-socket messages.

One radio: while the phone itself is on a WiFi network the access point runs
on that network's channel (the chip's interface combinations allow a station
and access points on one channel, and up to two channels). Nothing here
reads or prints the network's name or address (iw's output has them: only
the channel lines are used).
"""

import re

MAC = re.compile(r"\b[0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5}\b")
_FREQ = re.compile(r"\*\s+(\d+)(?:\.\d+)?\s+MHz\s+\[(\d+)\](.*)$")
_BAND = re.compile(r"^\s*Band (\d+):")
_CHANNEL = re.compile(
    r"channel (\d+) \((\d+)(?:\.\d+)? MHz\), width: (\d+) MHz(?: \(no HT\))?"
    r"(?:, center1: (\d+)(?:\.\d+)? MHz)?"
)
BAND_NAMES = {1: "2.4", 2: "5", 4: "6"}
# 5 GHz: the 80 MHz blocks (first channel of each) and their centres.
BLOCKS_80 = {36: 42, 52: 58, 100: 106, 116: 122, 132: 138, 149: 155, 165: 171}


def band_of(freq):
    if 2400 <= freq <= 2500:
        return "2.4"
    if 5150 <= freq < 5925:
        return "5"
    if 5925 <= freq <= 7125:
        return "6"
    return None


def chan_of(freq):
    if freq == 2484:
        return 14
    if 2412 <= freq <= 2472:
        return (freq - 2407) // 5
    if 5000 <= freq < 5925:
        return (freq - 5000) // 5
    if 5925 < freq <= 7125:
        return (freq - 5950) // 5
    return None


def freq_of(band, chan):
    if band == "2.4":
        return 2484 if chan == 14 else 2407 + 5 * chan
    if band == "5":
        return 5000 + 5 * chan
    return None


def parse_phy(text):
    """`iw phy PHY info` -> {"modes": set, "bands": {"2.4": {...}, "5": {...}},
    "share_channels": n}: per band whether HT40, VHT and HE for an access
    point are there, and each channel's flags; share_channels = how many
    channels a station and an access point may use together (0: no
    combination has both)."""
    modes, bands = set(), {}
    band = None
    section = None
    combos = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        m = _BAND.match(raw)
        if m:
            band = bands.setdefault(
                BAND_NAMES.get(int(m.group(1)), m.group(1)),
                {"ht40": False, "vht": False, "he_ap": False, "channels": {}},
            )
            section = "band"
            continue
        if line.startswith("Supported interface modes:"):
            section, band = "modes", None
            continue
        if line.startswith("valid interface combinations:"):
            section, band = "combos", None
            continue
        if section == "modes":
            if line.startswith("* "):
                modes.add(line[2:].strip())
                continue
            section = None
        if section == "combos":
            if line.startswith("* "):
                combos.append(line[2:])
            elif line and combos and not line.endswith(":"):
                combos[-1] += " " + line
            elif line:
                section = None
            continue
        if raw and not raw.startswith("\t") and not raw.startswith(" "):
            section, band = None, None
            continue
        if band is None:
            continue
        if "HT20/HT40" in line:
            band["ht40"] = True
        elif line.startswith("VHT Capabilities"):
            band["vht"] = True
        elif line.startswith("HE Iftypes:"):
            types = [t.strip() for t in line.split(":", 1)[1].split(",")]
            if "AP" in types:
                band["he_ap"] = True
        m = _FREQ.search(line)
        if m:
            rest = m.group(3)
            band["channels"][int(m.group(1))] = {
                "chan": int(m.group(2)),
                "disabled": "disabled" in rest,
                "no_ir": "no IR" in rest or "passive scan" in rest,
                "radar": "radar" in rest,
            }
    share = 0
    for c in combos:
        groups = re.findall(r"#\{([^}]*)\}", c)
        names = [{t.strip() for t in g.split(",")} for g in groups]
        if any("managed" in n for n in names) and any("AP" in n for n in names):
            m = re.search(r"#channels <= (\d+)", c)
            share = max(share, int(m.group(1)) if m else 1)
    return {"modes": modes, "bands": bands, "share_channels": share}


def parse_link(text):
    """`iw dev wlan0 link` -> {"connected": bool, "freq": int | None} (the
    network's name and address are not kept)."""
    text = text or ""
    if not text.lstrip().startswith("Connected to"):
        return {"connected": False, "freq": None}
    m = re.search(r"^\s*freq:\s*(\d+)", text, re.M)
    return {"connected": True, "freq": int(m.group(1)) if m else None}


def parse_dev_info(text):
    """`iw dev IFACE info` -> {"freq", "width", "center1"} of its channel, or
    None (no channel: not connected)."""
    m = _CHANNEL.search(text or "")
    if not m:
        return None
    freq, width = int(m.group(2)), int(m.group(3))
    center = int(m.group(4)) if m.group(4) else freq
    return {"freq": freq, "width": width, "center1": center}


def usable(phy, freq):
    """Whether an access point may send on this 20 MHz channel."""
    b = phy["bands"].get(band_of(freq) or "")
    c = b["channels"].get(freq) if b else None
    return bool(c) and not (c["disabled"] or c["no_ir"] or c["radar"])


def lower_of_pair(chan):
    """5 GHz: whether a channel is the lower one of its 40 MHz pair."""
    first = 149 if chan >= 149 else 36
    return ((chan - first) // 4) % 2 == 0


def block_80(chan):
    """5 GHz: (first channel, centre) of the 80 MHz block a channel is in."""
    for first, centre in BLOCKS_80.items():
        if first <= chan < first + 16:
            return first, centre
    return None


def chandef(phy, freq, width, center1=None):
    """The access point's channel: {"band", "freq", "chan", "width", "ht40":
    "+" / "-" / None, "seg0": centre channel for 80 MHz}. The width is cut
    down until every 20 MHz part may be used and the chip supports it; None
    when not even 20 MHz works."""
    band, chan = band_of(freq), chan_of(freq)
    caps = phy["bands"].get(band or "")
    if caps is None or band not in ("2.4", "5") or not usable(phy, freq):
        return None
    width = min(width or 20, 80)
    if band == "2.4":
        width = min(width, 40)
    while width > 20:
        if width == 80 and band == "5" and caps["vht"] and caps["ht40"]:
            blk = block_80(chan)
            if blk and all(usable(phy, freq_of("5", blk[0] + 4 * i)) for i in range(4)):
                return {
                    "band": band,
                    "freq": freq,
                    "chan": chan,
                    "width": 80,
                    "ht40": "+" if lower_of_pair(chan) else "-",
                    "seg0": blk[1],
                }
            width = 40
            continue
        if width == 40 and caps["ht40"]:
            if band == "5":
                plus = lower_of_pair(chan)
            elif center1 and center1 != freq:
                plus = center1 > freq  # 2.4 GHz: as the client's network
            else:
                plus = chan <= 7
            other = freq + 20 if plus else freq - 20
            if usable(phy, other):
                return {
                    "band": band,
                    "freq": freq,
                    "chan": chan,
                    "width": 40,
                    "ht40": "+" if plus else "-",
                    "seg0": None,
                }
        width = 20
    return {"band": band, "freq": freq, "chan": chan, "width": 20, "ht40": None, "seg0": None}


DEFAULT_WIDTH = {"5": 80, "2.4": 20}


def plan_channel(phy, band, sta, settings_channel=None, other_channel=True, alternatives=None):
    """Where the access point goes.

    sta: None (the phone is on no WiFi network) or parse_dev_info's dict.
    Returns (chandef, how) with how = "shared" (the client network's
    channel), "own" (no client connection), "second" (another channel beside
    the client's: the chip switches), or (None, reason) with reason
    "no-concurrency" (the chip cannot), "channel" (the client's channel
    cannot be shared and another is not allowed), "no-channel"."""
    if sta is not None:
        if phy.get("share_channels", 0) < 1:
            return None, "no-concurrency"
        cd = chandef(phy, sta["freq"], sta["width"], sta.get("center1"))
        if cd is not None:
            return cd, "shared"
        if not other_channel or phy.get("share_channels", 0) < 2:
            return None, "channel"
    wanted = [(band, settings_channel)] + list(alternatives or [])
    for b, ch in wanted:
        if not ch:
            continue
        freq = freq_of(b, int(ch))
        if freq is None:
            continue
        cd = chandef(phy, freq, DEFAULT_WIDTH.get(b, 20))
        if cd is not None:
            return cd, ("second" if sta is not None else "own")
    # Anything at all on the wanted band first, then the other one.
    for b in (band, "5" if band == "2.4" else "2.4"):
        caps = phy["bands"].get(b)
        if not caps:
            continue
        for freq in sorted(caps["channels"]):
            if usable(phy, freq):
                cd = chandef(phy, freq, DEFAULT_WIDTH.get(b, 20))
                if cd is not None:
                    return cd, ("second" if sta is not None else "own")
    return None, "no-channel"


KEY_MGMT = {
    "wpa2-wpa3": ("WPA-PSK SAE", 1),
    "wpa3": ("SAE", 2),
    "wpa2": ("WPA-PSK", 0),
}


def hostapd_conf(iface, ctrl_dir, ssid, password, security, cd, phy, max_clients=8):
    """hostapd's configuration (the password in it: written 0600, never
    logged). The name goes in as hex (no quoting to get wrong)."""
    caps = phy["bands"][cd["band"]]
    mgmt, mfp = KEY_MGMT[security]
    lines = [
        "# duo2-hotspot (hotspot/README.md): written at every start, in /run only",
        "interface=%s" % iface,
        "driver=nl80211",
        "ctrl_interface=%s" % ctrl_dir,
        "ctrl_interface_group=0",
        "ssid2=%s" % ssid.encode("utf-8").hex(),
        "utf8_ssid=1",
        "hw_mode=%s" % ("a" if cd["band"] == "5" else "g"),
        "channel=%d" % cd["chan"],
        "beacon_int=100",
        "dtim_period=2",
        "max_num_sta=%d" % max_clients,
        "auth_algs=1",
        "ignore_broadcast_ssid=0",
        "ap_isolate=0",
        "disassoc_low_ack=1",
        "wmm_enabled=1",
        "ieee80211n=1",
    ]
    if cd["ht40"]:
        lines.append("ht_capab=[HT40%s]" % cd["ht40"])
    if cd["band"] == "5" and caps.get("vht"):
        lines += ["ieee80211ac=1", "vht_oper_chwidth=%d" % (1 if cd["width"] == 80 else 0)]
        if cd["width"] == 80:
            lines.append("vht_oper_centr_freq_seg0_idx=%d" % cd["seg0"])
    if caps.get("he_ap"):
        lines.append("ieee80211ax=1")
        if cd["band"] == "5":
            lines.append("he_oper_chwidth=%d" % (1 if cd["width"] == 80 else 0))
            if cd["width"] == 80:
                lines.append("he_oper_centr_freq_seg0_idx=%d" % cd["seg0"])
    lines += [
        "wpa=2",
        "wpa_key_mgmt=%s" % mgmt,
        "rsn_pairwise=CCMP",
        "ieee80211w=%d" % mfp,
    ]
    if "SAE" in mgmt:
        lines += ["sae_require_mfp=1", "sae_pwe=2"]
    lines += [
        "wpa_passphrase=%s" % password,
        "logger_syslog=0",
        "logger_stdout=-1",
        "logger_stdout_level=2",
    ]
    return "\n".join(lines) + "\n"


def parse_event(msg):
    """A hostapd control-socket event ("<3>AP-STA-CONNECTED aa:...") ->
    (name, mac or None); (None, None) for anything else."""
    msg = (msg or "").strip()
    if msg.startswith("<") and ">" in msg[:4]:
        msg = msg.split(">", 1)[1]
    if not msg or not msg[0].isalpha():
        return None, None
    words = msg.split()
    name = words[0]
    mac = words[1].lower() if len(words) > 1 and MAC.fullmatch(words[1]) else None
    return name, mac


def parse_sta(reply):
    """A STA-FIRST / STA-NEXT reply -> {"mac", "authorized", "connected_time",
    "rx_bytes", "tx_bytes"}, or None at the end of the list."""
    lines = (reply or "").strip().splitlines()
    if not lines or not MAC.fullmatch(lines[0].strip()):
        return None
    info = {}
    for line in lines[1:]:
        if "=" in line:
            k, v = line.split("=", 1)
            info[k.strip()] = v.strip()

    def num(k):
        try:
            return int(info.get(k, "0"))
        except ValueError:
            return 0

    return {
        "mac": lines[0].strip().lower(),
        "authorized": "[AUTHORIZED]" in info.get("flags", ""),
        "connected_time": num("connected_time"),
        "rx_bytes": num("rx_bytes"),
        "tx_bytes": num("tx_bytes"),
    }


def mask_macs(line, salt=b""):
    """A log line with every MAC address replaced by a short name that only
    tells devices apart within one run (a hash with this run's salt): the
    journal keeps no device address."""
    import hashlib

    return MAC.sub(
        lambda m: "dev-" + hashlib.sha256(salt + m.group(0).lower().encode()).hexdigest()[:4], line
    )
