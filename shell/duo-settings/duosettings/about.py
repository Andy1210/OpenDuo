"""What Settings > About shows, as data: [(group title, [(label, value)])].

Readers run small commands and read /proc and /sys; the parsers are pure
(tests/test_about.py, with made-up values). Personal identifiers (IMEI,
phone number, serial number, MAC and IP addresses) are shown in the app only
and never logged. With DUO_SETTINGS_FAKE_ABOUT=1 every page is built from
made-up values (pictures and tests must never show a real identifier).
"""

import gettext
import glob
import gzip
import json
import os
import platform
import re
import time

from . import system

_ = gettext.gettext

FAKE_ENV = "DUO_SETTINGS_FAKE_ABOUT"
RELEASE_FILE = "/etc/duo2-release"
APT_HISTORY = "/var/log/apt/history.log"
ARM_PARTS = {
    "0xd05": "Cortex-A55",
    "0xd41": "Cortex-A78",
    "0xd44": "Cortex-X1",
    "0xd46": "Cortex-A510",
    "0xd47": "Cortex-A710",
    "0xd4d": "Cortex-A715",
    "0xd4e": "Cortex-X3",
    "0xd0b": "Cortex-A76",
    "0xd0d": "Cortex-A77",
    "0xd0c": "Neoverse-N1",
}
SOC_IDS = {"415": "SM8350"}
SENSOR_NAME = re.compile(r"^(imx|ov|s5k|gc|hi|ar|mt)[0-9a-z]+ [0-9]+-[0-9a-f]+$", re.I)


def fake():
    return os.environ.get(FAKE_ENV) == "1"


# -- parsers (pure) ----------------------------------------------------------------


def parse_release(text):
    """/etc/duo2-release (key=value lines) -> dict."""
    out = {}
    for line in (text or "").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def os_header(rel, osr, kernel_release, fake_it=False):
    """The About page's heading: {"name", "version", "lines": [..]} from the
    build stamp, os-release and the kernel release. Pure."""
    name = osr.get("NAME") or "OpenDuo"
    version = rel.get("openduo") or osr.get("VERSION") or "?"
    lines = []
    build = rel.get("build") or osr.get("BUILD_ID")
    if build:
        lines.append(_("Build %s") % build)
    lines.append(_("Kernel %s") % kernel_release)
    lines.append(_("Based on Debian 13"))
    return {"name": name, "version": version, "lines": lines}


def header():
    if fake():
        return os_header({"openduo": "0.0.0-fake", "build": "20260101.0000000"}, {}, "7.0.0-fake")
    return os_header(
        parse_release(system.read_text(RELEASE_FILE)), system.os_release(), system.kernel()
    )


def parse_kernel_build(uname_v):
    """'#17 SMP PREEMPT Sun Nov  8 01:00:09 UTC 2026' -> '8 Nov 2026' (or the text)."""
    m = re.search(
        r"([A-Z][a-z]{2}) +([A-Z][a-z]{2}) +(\d{1,2}) [0-9:]+ (?:[A-Z]+ )?(\d{4})", uname_v or ""
    )
    if not m:
        return (uname_v or "").strip() or None
    return "%d %s %s" % (int(m.group(3)), m.group(2), m.group(4))


def last_upgrade(history_texts):
    """The date (YYYY-MM-DD) of the newest apt run that upgraded a package,
    from apt's history.log texts. Blocks without an Upgrade: line are installs
    only. None when there is none."""
    best = None
    for text in history_texts:
        for block in (text or "").split("\n\n"):
            if not re.search(r"^Upgrade: ", block, re.M):
                continue
            m = re.search(r"^End-Date: (\d{4}-\d{2}-\d{2})", block, re.M)
            if m and (best is None or m.group(1) > best):
                best = m.group(1)
    return best


def format_date(iso):
    try:
        t = time.strptime(iso, "%Y-%m-%d")
    except (TypeError, ValueError):
        return iso
    return "%d %s %d" % (t.tm_mday, time.strftime("%b", t), t.tm_year)


def format_uptime(seconds):
    s = int(seconds)
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m = s // 60
    if d:
        return "%d d %d h %d min" % (d, h, m)
    if h:
        return "%d h %d min" % (h, m)
    return "%d min" % m


def network_type(techs):
    """ModemManager's access technologies -> a name people know."""
    t = {x.lower() for x in techs or []}
    if "5gnr" in t:
        return "5G" + (" (with LTE)" if "lte" in t else "")
    for key, name in (
        ("lte", "4G (LTE)"),
        ("hspa", "3G (HSPA)"),
        ("hspa-plus", "3G (HSPA+)"),
        ("umts", "3G (UMTS)"),
        ("edge", "2G (EDGE)"),
        ("gprs", "2G (GPRS)"),
        ("gsm", "2G (GSM)"),
    ):
        if key in t:
            return name
    return None


def parse_modem(modem, sims):
    """`mmcli -J -m N` + {sim path: `mmcli -J -i` properties} -> {"modem": {...},
    "slots": [{...}]}. One entry per SIM slot; the active one carries the
    operator, the network and the signal. Pure."""
    g = (modem or {}).get("modem", {}).get("generic", {})
    p3 = (modem or {}).get("modem", {}).get("3gpp", {})

    def val(v):
        return None if v in (None, "--", "", []) else v

    primary = val(g.get("primary-sim-slot"))
    paths = list(g.get("sim-slots") or [])
    active = val(g.get("sim"))
    numbers = [n for n in (g.get("own-numbers") or []) if n and n != "--"]
    slots = []
    for i, path in enumerate(paths or [active or "/"]):
        number = i + 1
        is_active = (str(primary) == str(number)) if primary else (path == active)
        sim_path = path if path not in (None, "/") else (active if is_active else None)
        props = sims.get(sim_path, {}) if sim_path else {}
        entry = {
            "slot": number,
            "present": sim_path is not None,
            "active": bool(is_active),
            "sim_operator": val(props.get("operator-name")),
        }
        if is_active:
            entry.update(
                operator=val(p3.get("operator-name")) or entry["sim_operator"],
                network=network_type(g.get("access-technologies")),
                signal=(g.get("signal-quality") or {}).get("value"),
                roaming=p3.get("registration-state") == "roaming",
                registered=p3.get("registration-state") in ("home", "roaming"),
                number=numbers[0] if numbers else None,
                state=val(g.get("state")),
            )
        slots.append(entry)
    return {
        "modem": {
            "revision": val(g.get("revision")),
            "imei": val(g.get("equipment-identifier")) or val(p3.get("imei")),
            "bearer_dev": None,
        },
        "slots": slots,
    }


def parse_ip(text):
    """`ip -j addr` -> {ifname: [address, ...]} of the global addresses."""
    try:
        data = json.loads(text)
    except ValueError:
        return {}
    out = {}
    for itf in data:
        addrs = [
            a["local"]
            for a in itf.get("addr_info", [])
            if a.get("scope") == "global" and a.get("local")
        ]
        if addrs:
            out[itf.get("ifname", "")] = addrs
    return out


def group_cpus(cores):
    """[(part '0xd05', max kHz), ...] -> '4 x Cortex-A55 at 1.80 GHz, ...' lines,
    slowest cluster first."""
    groups = {}
    for part, khz in cores:
        groups.setdefault((part, khz), 0)
        groups[(part, khz)] += 1
    lines = []
    for (part, khz), n in sorted(groups.items(), key=lambda kv: kv[0][1]):
        lines.append(
            "%d x %s at %.2f GHz" % (n, ARM_PARTS.get(part, "ARM core " + part), khz / 1e6)
        )
    return lines


def design_capacity_mah(model_name):
    """The Duo 2's battery manager names the cell ('..._3600MAH_...')."""
    m = re.search(r"(\d{3,5})MAH", model_name or "", re.I)
    return int(m.group(1)) if m else None


def group_sensors(names):
    """['imx481 1-0010', 'imx481 2-001a', 'imx563 4-001a'] -> ['IMX481 (3)'...]"""
    counts = {}
    for n in names:
        key = n.split()[0].upper()
        counts[key] = counts.get(key, 0) + 1
    return ["%s%s" % (k, " x %d" % c if c > 1 else "") for k, c in sorted(counts.items())]


def parse_licence(text):
    """Short licence names of a Debian copyright file (DEP-5 'License:' lines;
    older files name the common-licenses they point to). Pure."""
    found = []
    for m in re.finditer(r"^License: *([^\n]+)", text or "", re.M):
        v = re.sub(r"\s+", " ", m.group(1)).strip()
        if v and v not in found:
            found.append(v)
    if not found:
        for m in re.finditer(r"/usr/share/common-licenses/([A-Za-z0-9.+-]+)", text or ""):
            if m.group(1) not in found:
                found.append(m.group(1))
    return found[:6]


# -- readers ---------------------------------------------------------------------------


def _json(argv, timeout=8):
    ok, out = system.run(argv, timeout=timeout)
    try:
        return json.loads(out) if ok else None
    except ValueError:
        return None


def modem_info():
    """ModemManager over D-Bus (works from the session): the modem and, per SIM
    slot, what the SIM says."""
    listing = _json(["mmcli", "-J", "-L"])
    paths = (listing or {}).get("modem-list") or []
    if not paths:
        return None
    modem = _json(["mmcli", "-J", "-m", paths[0]])
    if not modem:
        return None
    g = modem.get("modem", {}).get("generic", {})
    sims = {}
    for path in set((g.get("sim-slots") or []) + [g.get("sim")]):
        if path and path not in ("/", "--"):
            data = _json(["mmcli", "-J", "-i", path])
            if data:
                sims[path] = data.get("sim", {}).get("properties", {})
    info = parse_modem(modem, sims)
    bearers = g.get("bearers") or []
    if bearers:
        b = _json(["mmcli", "-J", "-b", bearers[0]])
        info["modem"]["bearer_dev"] = ((b or {}).get("bearer", {}).get("status", {}) or {}).get(
            "interface"
        )
    return info


def identity():
    """{"serial": ..., "imeis": [...]} from the root helper (None on failure)."""
    ok, out = system.run(["sudo", "-n", system.HELPER, "identity"], timeout=40)
    try:
        return json.loads(out) if ok else None
    except ValueError:
        return None


def bluetooth_address():
    ok, out = system.run(
        [
            "busctl",
            "--system",
            "get-property",
            "org.bluez",
            "/org/bluez/hci0",
            "org.bluez.Adapter1",
            "Address",
        ]
    )
    m = re.search(r'"([0-9A-Fa-f:]{17})"', out) if ok else None
    return m.group(1).upper() if m else None


def wifi_mac():
    return system.read_text("/sys/class/net/wlan0/address").strip() or None


def uptime_seconds():
    try:
        return float(system.read_text("/proc/uptime").split()[0])
    except (ValueError, IndexError):
        return None


def apt_histories():
    texts = []
    for path in sorted(glob.glob(APT_HISTORY + "*")):
        try:
            opener = gzip.open if path.endswith(".gz") else open
            with opener(path, "rt", errors="replace") as f:
                texts.append(f.read())
        except OSError:
            continue
    return texts


def package_version(name):
    ok, out = system.run(["dpkg-query", "-W", "-f", "${Version}", name])
    return out.strip() if ok and out.strip() else None


def cpu_cores():
    cores = []
    parts = {}
    cur = None
    for line in system.read_text("/proc/cpuinfo").splitlines():
        if line.startswith("processor"):
            cur = int(line.split(":")[1])
        elif line.startswith("CPU part") and cur is not None:
            parts[cur] = line.split(":")[1].strip()
    for n, part in sorted(parts.items()):
        try:
            khz = int(
                system.read_text("/sys/devices/system/cpu/cpu%d/cpufreq/cpuinfo_max_freq" % n)
            )
        except ValueError:
            continue
        cores.append((part, khz))
    return cores


def read_battery():
    base = None
    for b in glob.glob("/sys/class/power_supply/*"):
        if system.read_text(b + "/type").strip() == "Battery":
            base = b
            break
    if not base:
        return {}

    def r(n):
        return system.read_text(os.path.join(base, n)).strip() or None

    return {
        "capacity": r("capacity"),
        "status": r("status"),
        "health": r("health"),
        "soh": r("state_of_health"),
        "cycles": r("cycle_count"),
        "model": r("model_name"),
        "technology": r("technology"),
        "full": r("charge_full"),
        "design": r("charge_full_design"),
    }


def display_info():
    modes = []
    for conn in sorted(glob.glob("/sys/class/drm/card*-*")):
        if system.read_text(conn + "/status").strip() == "connected":
            first = system.read_text(conn + "/modes").split("\n")[0].strip()
            if first:
                modes.append(first)
    return modes


def camera_sensors():
    names = []
    for d in sorted(glob.glob("/sys/class/video4linux/v4l-subdev*")):
        n = system.read_text(d + "/name").strip()
        if SENSOR_NAME.match(n):
            names.append(n)
    return names


def motion_sensors():
    """What iio-sensor-proxy reports (system bus)."""
    out = []
    for prop, label in (
        ("HasAccelerometer", "Accelerometer"),
        ("HasAmbientLight", "Ambient light"),
        ("HasCompass", "Compass"),
        ("HasProximity", "Proximity"),
    ):
        ok, text = system.run(
            [
                "busctl",
                "--system",
                "get-property",
                "net.hadess.SensorProxy",
                "/net/hadess/SensorProxy",
                "net.hadess.SensorProxy",
                prop,
            ]
        )
        if ok and text.strip().endswith("true"):
            out.append(label)
    return out


def storage_total():
    try:
        sectors = int(system.read_text("/sys/block/sda/size"))
    except ValueError:
        return None
    return sectors * 512


# -- the pages' data --------------------------------------------------------------------

FAKE_SERIAL = "0F0A0B0C0D0E"


def status_groups(ident=None, modem="unset"):
    """Status page groups. `ident` = identity() (serial, IMEIs), `modem` = modem_info()."""
    if fake():
        return fake_status()
    if modem == "unset":
        modem = modem_info()
    groups = []
    slots = (modem or {}).get("slots", [])
    mm = (modem or {}).get("modem", {})
    imeis = (ident or {}).get("imeis") or []
    mm_imei = mm.get("imei")
    if not modem:
        groups.append((_("Mobile"), [(_("SIM"), _("No modem"))]))
    for s in slots:
        rows = []
        if not s["present"]:
            rows.append((_("SIM"), _("Empty")))
        elif not s["active"]:
            rows.append(
                (
                    _("SIM"),
                    _("Not in use")
                    + (" (%s)" % s["sim_operator"] if s.get("sim_operator") else ""),
                )
            )
        else:
            rows.append((_("Operator"), s.get("operator") or s.get("sim_operator") or _("Unknown")))
            rows.append((_("Network type"), s.get("network") or _("Not connected")))
            if s.get("signal") is not None:
                rows.append((_("Signal strength"), "%s %%" % s["signal"]))
            rows.append((_("Roaming"), _("Roaming") if s.get("roaming") else _("Not roaming")))
            if s.get("number"):
                rows.append((_("Phone number"), s["number"]))
            else:
                rows.append((_("Phone number"), _("Unknown (the SIM does not store it)")))
        groups.append((_("SIM %d") % s["slot"] if len(slots) > 1 else _("SIM"), rows))
    by_sub = {i["sub"]: i for i in imeis}
    if by_sub:
        first = by_sub[min(by_sub)]
        if len(by_sub) > 1 and len({i["imei"] for i in by_sub.values()}) > 1:
            ids = []
            for sub, i in sorted(by_sub.items()):
                ids.append((_("IMEI (SIM %d)") % (sub + 1), i["imei"]))
                if i.get("sv"):
                    ids.append((_("IMEI SV (SIM %d)") % (sub + 1), i["sv"]))
        else:
            ids = [(_("IMEI"), first["imei"])]
            if first.get("sv"):
                ids.append((_("IMEI SV"), first["sv"]))
    elif mm_imei:
        ids = [(_("IMEI"), mm_imei)]
    else:
        ids = [(_("IMEI"), _("Unavailable"))]
    groups.append((_("Phone identity"), ids))
    # Addresses
    ips = parse_ip(system.run(["ip", "-j", "addr"])[1])
    dev = mm.get("bearer_dev")
    addr = []
    addr.append((_("Wi-Fi IP address"), "\n".join(ips.get("wlan0", [])) or _("Not connected")))
    addr.append(
        (
            _("Mobile IP address"),
            "\n".join(ips.get(dev, [])) if dev and ips.get(dev) else _("Not connected"),
        )
    )
    addr.append((_("Wi-Fi MAC address"), wifi_mac() or _("Unavailable")))
    addr.append((_("Bluetooth address"), bluetooth_address() or _("Unavailable")))
    groups.append((_("Addresses"), addr))
    serial = (ident or {}).get("serial")
    up = uptime_seconds()
    groups.append(
        (
            _("Device"),
            [
                (_("Serial number"), serial or _("Unavailable")),
                (_("Up time"), format_uptime(up) if up is not None else "?"),
            ],
        )
    )
    return groups


def fake_status():
    return [
        (
            _("SIM 1"),
            [
                (_("Operator"), "Example Mobile"),
                (_("Network type"), "5G (with LTE)"),
                (_("Signal strength"), "80 %"),
                (_("Roaming"), _("Not roaming")),
                (_("Phone number"), "+00 000 0000000"),
            ],
        ),
        (_("SIM 2"), [(_("SIM"), _("Not in use"))]),
        (_("Phone identity"), [(_("IMEI"), "000000000000000"), (_("IMEI SV"), "00")]),
        (
            _("Addresses"),
            [
                (_("Wi-Fi IP address"), "192.0.2.10"),
                (_("Mobile IP address"), "198.51.100.7"),
                (_("Wi-Fi MAC address"), "02:00:00:00:00:01"),
                (_("Bluetooth address"), "02:00:00:00:00:02"),
            ],
        ),
        (_("Device"), [(_("Serial number"), FAKE_SERIAL), (_("Up time"), "1 d 2 h 3 min")]),
    ]


def waydroid_version():
    if not os.path.exists("/usr/bin/waydroid"):
        return None
    ok, out = system.run(["waydroid", "status"], timeout=6)
    if ok and "RUNNING" in out.upper().split("SESSION:")[-1][:20]:
        ok2, ver = system.run(["waydroid", "prop", "get", "ro.build.version.release"], timeout=6)
        if ok2 and ver.strip():
            return "Android %s (Waydroid %s)" % (ver.strip(), package_version("waydroid") or "?")
    return "Waydroid %s (Android not running)" % (package_version("waydroid") or "?")


def software_groups(gtk_versions="", compositor="?", modem=None):
    if fake():
        return [
            (
                None,
                [
                    (_("OpenDuo version"), "0.0.0-fake"),
                    (_("Build"), "20260101.0000000"),
                    (_("Kernel version"), "7.0.0-fake"),
                    (_("Kernel built"), "1 Jan 2026"),
                    (_("Modem firmware"), "MPSS.FAKE.1"),
                    (_("Debian version"), "13.0 (trixie)"),
                    (_("Last security update"), "1 Jan 2026"),
                    (_("Graphics driver"), "Mesa 0.0.0 (fake)"),
                ],
            )
        ]
    rel = parse_release(system.read_text(RELEASE_FILE))
    osr = system.os_release()
    rows = []
    rows.append((_("OpenDuo version"), rel.get("openduo") or _("Unknown (no build stamp)")))
    if rel.get("build"):
        stamp = rel["build"]
        if rel.get("built"):
            stamp += "\n" + _("Built %s") % rel["built"][:10]
        rows.append((_("Build"), stamp))
    deb = system.debian_version()
    codename = osr.get("VERSION_CODENAME")
    rows.append(
        (
            _("Debian version"),
            ("%s (%s)" % (deb, codename) if codename else deb)
            if deb
            else osr.get("PRETTY_NAME", "?"),
        )
    )
    rows.append((_("Kernel version"), system.kernel()))
    rows.append((_("Kernel built"), parse_kernel_build(platform.version()) or "?"))
    rows.append(
        (_("Modem firmware"), ((modem or {}).get("modem", {}).get("revision")) or _("Unavailable"))
    )
    up = last_upgrade(apt_histories())
    rows.append((_("Last security update"), format_date(up) if up else _("None recorded")))
    mesa = package_version("libgl1-mesa-dri")
    rows.append(
        (
            _("Graphics driver"),
            ("Mesa %s (freedreno, turnip)" % mesa.split("-")[0]) if mesa else _("Unknown"),
        )
    )
    wd = waydroid_version()
    if wd:
        rows.append((_("Android apps"), wd))
    rows.append((_("Compositor"), "duo-wc %s" % compositor))
    rows.append((_("GTK / libadwaita"), gtk_versions))
    return [(None, rows)]


def hardware_groups():
    if fake():
        return fake_hardware()
    groups = []
    model = system.read_text("/sys/devices/soc0/machine").strip() or "Microsoft Surface Duo 2"
    soc_id = system.read_text("/sys/devices/soc0/soc_id").strip()
    rev = system.read_text("/sys/devices/soc0/revision").strip()
    soc = (
        "Qualcomm Snapdragon 888 (%s)" % SOC_IDS[soc_id]
        if soc_id in SOC_IDS
        else system.read_text("/sys/devices/soc0/family").strip() or "?"
    )
    if rev:
        soc += ", rev. " + rev
    cpu = group_cpus(cpu_cores())
    mem = system.memory_total()
    used, total = system.storage("/")
    disk = storage_total()
    hw = [
        (_("Model"), model),
        (_("Processor"), soc),
        (_("CPU cores"), "\n".join(cpu) or "?"),
        (_("Graphics"), "Qualcomm Adreno 660"),
        (_("Memory"), system.human_bytes(mem)),
        (
            _("Storage"),
            _("%s total, %s used") % (system.human_bytes(disk or total), system.human_bytes(used)),
        ),
    ]
    groups.append((None, hw))
    bat = read_battery()
    if bat:
        rows = [
            (_("Charge"), "%s %% (%s)" % (bat.get("capacity") or "?", bat.get("status") or "?"))
        ]
        design = design_capacity_mah(bat.get("model"))
        if bat.get("design") and bat["design"].isdigit() and int(bat["design"]) > 0:
            design = int(bat["design"]) // 1000
        if design:
            rows.append((_("Design capacity"), "%d mAh" % design))
        if bat.get("full") and bat["full"].isdigit() and int(bat["full"]) > 0:
            rows.append((_("Full charge capacity"), "%d mAh" % (int(bat["full"]) // 1000)))
        health = system.battery().get("health")
        if health:
            rows.append((_("Health"), _("%d %% of its design capacity") % health))
        elif bat.get("health"):
            rows.append((_("Health"), bat["health"]))
        if bat.get("cycles") is not None:
            rows.append((_("Charge cycles"), bat["cycles"]))
        if bat.get("technology"):
            rows.append((_("Technology"), bat["technology"]))
        groups.append((_("Battery"), rows))
    modes = display_info()
    rate = system.refresh_rate()
    if modes:
        label = (
            "%d x %s" % (len(modes), modes[0].split("@")[0])
            if len(set(modes)) == 1
            else ", ".join(modes)
        )
        groups.append((_("Displays"), [(_("Panels"), label + (", %d Hz" % rate if rate else ""))]))
    cams = group_sensors(camera_sensors())
    sens = motion_sensors()
    groups.append(
        (
            _("Cameras and sensors"),
            [
                (_("Camera sensors"), "\n".join(cams) or _("None found")),
                (_("Sensors"), "\n".join(sens) or _("None found")),
            ],
        )
    )
    return groups


def fake_hardware():
    return [
        (
            None,
            [
                (_("Model"), "Example Phone"),
                (_("Processor"), "Example SoC (EX0000)"),
                (
                    _("CPU cores"),
                    "4 x Cortex-A55 at 1.80 GHz\n3 x Cortex-A78 at 2.42 GHz\n1 x Cortex-X1 at 2.84 GHz",
                ),
                (_("Graphics"), "Example GPU"),
                (_("Memory"), "8.0 GB"),
                (_("Storage"), "256 GB total, 20 GB used"),
            ],
        ),
        (
            _("Battery"),
            [
                (_("Charge"), "80 % (Charging)"),
                (_("Design capacity"), "3600 mAh"),
                (_("Health"), "100 % of its design capacity"),
                (_("Charge cycles"), "12"),
            ],
        ),
        (_("Displays"), [(_("Panels"), "2 x 1344x1892, 90 Hz")]),
        (
            _("Cameras and sensors"),
            [
                (_("Camera sensors"), "IMX000 x 3\nIMX001"),
                (_("Sensors"), "Accelerometer\nAmbient light"),
            ],
        ),
    ]


def licence_packages():
    """[(package, 'MIT, GPL-2+', copyright path)] for every installed package
    with a copyright file, sorted. Reads each file's head only."""
    out = []
    for path in glob.glob("/usr/share/doc/*/copyright") + glob.glob(
        "/usr/local/share/doc/*/copyright"
    ):
        name = os.path.basename(os.path.dirname(path))
        try:
            with open(path, errors="replace") as f:
                head = f.read(60000)
        except OSError:
            continue
        out.append((name, ", ".join(parse_licence(head)) or _("See the licence text"), path))
    return sorted(out)


def fake_licences():
    return [
        ("example-lib", "MIT", ""),
        ("example-tool", "GPL-2+, LGPL-2.1", ""),
        ("example-font", "OFL-1.1", ""),
    ]


def read_copyright(path, limit=200000):
    try:
        with open(path, errors="replace") as f:
            return f.read(limit)
    except OSError:
        return ""
