"""The power figures the estimates start from.

Android estimates battery use from the manufacturer's measured table,
power_profile.xml (currents in mA at the battery). The Surface Duo 2's own
stock images carry Microsoft's (a vendor overlay, FrameworksResTarget.apk);
scripts/build-power-profile.sh extracts it from the owner's images and
scripts/install-power-profile.sh puts it on the phone as
/opt/duo2-power-profile/power_profile.xml. It is never in git or in the
image: this module only knows the names of the values.

Where the table has no value, the kernel's energy model gives the CPU
(debugfs energy_model: power in microwatts per frequency, converted at the
nominal battery voltage), and the rest are our own round assumptions (marked
"assumed"); the calibration against the measured battery drain scales them
(model.py). `Profile.sources` says where each figure came from.
"""

import os
import xml.etree.ElementTree as ET

PROFILE_PATHS = ("/opt/duo2-power-profile/power_profile.xml",)
ENERGY_MODEL = "/sys/kernel/debug/energy_model"
NOMINAL_V = 3.85  # the battery's nominal voltage: EM microwatts -> mA

# Our own assumptions where neither the table nor the kernel has a figure.
# Not Microsoft's values: round guesses, scaled by the calibration.
ASSUMED = {
    "screen.on": 120.0,  # both panels lit, lowest brightness, device awake
    "screen.full": 300.0,  # added at full brightness, both panels
    "cpu.active": 5.0,
    "wifi.controller.idle": 2.0,
    "wifi.controller.rx": 150.0,
    "wifi.controller.tx": 180.0,
    "modem.controller.idle": 8.0,
    "modem.controller.rx": 150.0,
    "modem.controller.tx": 180.0,
    "radio.on": 10.0,
    "bluetooth.on": 1.0,
    "bluetooth.active": 100.0,
    "dsp.audio": 20.0,
    "battery.capacity": 4400.0,
}

# Ours, not in any power_profile: per awake second of each DSP and the GPU.
# ADSP and CDSP: the table's audio DSP figure when there is one (an Android
# figure for "the DSP is decoding audio"); the SLPI is a low-power sensor
# island; the GPU's full-speed current at its top frequency.
DSP_ASSUMED = {"slpi": 6.0}
GPU_MAX_MA = 700.0
GPU_MAX_HZ = 840e6


def _num(text):
    try:
        return float((text or "").strip())
    except ValueError:
        return None


def parse_power_profile(text):
    """{name: float | [float]} from a plain power_profile.xml text."""
    root = ET.fromstring(text)
    out = {}
    for el in root:
        name = el.get("name")
        if not name:
            continue
        if el.tag == "item":
            v = _num(el.text)
            if v is not None:
                out[name] = v
        elif el.tag == "array":
            vals = [_num(v.text) for v in el.findall("value")]
            vals = [v for v in vals if v is not None]
            if not vals and _num(el.text) is not None:
                vals = [_num(el.text)]
            out[name] = vals
    return out


def read_energy_model(base=ENERGY_MODEL):
    """[(cpus, [(khz, microwatts)])] per perf domain, or [] (debugfs, root)."""
    domains = []
    try:
        names = sorted(os.listdir(base))
    except OSError:
        return []
    for d in names:
        path = os.path.join(base, d)
        try:
            with open(os.path.join(path, "cpus")) as f:
                cpus = parse_cpulist(f.read())
        except OSError:
            continue
        states = []
        for ps in os.listdir(path):
            if not ps.startswith("ps:"):
                continue
            try:
                with open(os.path.join(path, ps, "frequency")) as f:
                    khz = int(f.read())
                with open(os.path.join(path, ps, "power")) as f:
                    uw = int(f.read())
            except (OSError, ValueError):
                continue
            states.append((khz, uw))
        if cpus and states:
            domains.append((cpus, sorted(states)))
    return sorted(domains, key=lambda d: min(d[0]))


def parse_cpulist(text):
    cpus = []
    for part in text.strip().split(","):
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            cpus.extend(range(int(a), int(b) + 1))
        else:
            cpus.append(int(part))
    return cpus


def interpolate(points, x):
    """Linear interpolation over sorted (x, y) points, clamped at the ends."""
    if not points:
        return None
    if x <= points[0][0]:
        return points[0][1]
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0) if x1 != x0 else y1
    return points[-1][1]


class Profile:
    """The figures in mA (at the battery), with where each one came from."""

    def __init__(self, table=None, energy_model=None, clusters=None):
        self.table = table or {}
        self.sources = {}
        self.em = energy_model or []
        # clusters: [[cpu, ...], ...] little first (cpufreq policies)
        self.clusters = clusters or [cpus for cpus, _ in self.em] or [[0, 1, 2, 3], [4, 5, 6], [7]]
        self.cpu_points = []
        for i, cpus in enumerate(self.clusters):
            speeds = self.table.get("cpu.core_speeds.cluster%d" % i)
            power = self.table.get("cpu.core_power.cluster%d" % i)
            if (
                isinstance(speeds, list)
                and isinstance(power, list)
                and speeds
                and len(speeds) == len(power)
            ):
                self.cpu_points.append(sorted(zip(speeds, power)))
                self.sources["cpu.cluster%d" % i] = "power_profile"
                continue
            em = [states for c, states in self.em if c and c[0] in cpus]
            if em:
                self.cpu_points.append([(khz, uw / 1000.0 / NOMINAL_V) for khz, uw in em[0]])
                self.sources["cpu.cluster%d" % i] = "energy model"
            else:
                self.cpu_points.append([(300000, 5.0), (3000000, 300.0)])
                self.sources["cpu.cluster%d" % i] = "assumed"

    @classmethod
    def load(cls, paths=PROFILE_PATHS, em_base=ENERGY_MODEL, clusters=None):
        table = {}
        for p in paths:
            try:
                with open(p) as f:
                    table = parse_power_profile(f.read())
                break
            except (OSError, ET.ParseError):
                continue
        return cls(table, read_energy_model(em_base), clusters)

    def value(self, name):
        v = self.table.get(name)
        if isinstance(v, list):
            v = v[-1] if v else None
        if v is not None:
            self.sources.setdefault(name, "power_profile")
            return float(v)
        self.sources.setdefault(name, "assumed")
        return ASSUMED.get(name, 0.0)

    def has(self, name):
        return name in self.table

    def cpu_ma(self, cluster, khz):
        """mA one busy core of `cluster` draws at `khz` (interpolated)."""
        pts = self.cpu_points[cluster] if cluster < len(self.cpu_points) else self.cpu_points[-1]
        return interpolate(pts, khz) or 0.0

    def screen(self):
        """(on, full) mA for BOTH panels: the table has one screen figure;
        we take it as the whole device's screen (both panels) and split it
        evenly per panel (an assumption, calibrated)."""
        return self.value("screen.on"), self.value("screen.full")

    def radio_on(self):
        v = self.table.get("radio.on")
        if isinstance(v, list) and v:
            self.sources["radio.on"] = "power_profile"
            return float(v[-1])  # the last signal bin: in service
        return self.value("radio.on")

    def bluetooth(self):
        on = (
            self.value("bluetooth.controller.idle")
            if self.has("bluetooth.controller.idle")
            else self.value("bluetooth.on")
        )
        if self.has("bluetooth.controller.rx"):
            active = (
                self.value("bluetooth.controller.rx") + self.value("bluetooth.controller.tx")
            ) / 2
        else:
            active = self.value("bluetooth.active")
        return on, active

    def dsp_ma(self, name):
        if name in ("adsp", "cdsp"):
            self.sources["dsp.%s" % name] = (
                "power_profile (dsp.audio)" if self.has("dsp.audio") else "assumed"
            )
            return self.value("dsp.audio")
        self.sources["dsp.%s" % name] = "assumed"
        return DSP_ASSUMED.get(name, 6.0)

    def gpu_ma(self, hz):
        """mA of the GPU while busy at `hz` (assumed: no table figure; scales
        with the square of the frequency, calibrated)."""
        self.sources["gpu"] = "assumed"
        r = max(0.0, min(1.5, hz / GPU_MAX_HZ))
        return GPU_MAX_MA * (0.15 + 0.85 * r * r)

    def capacity_mah(self):
        if self.has("battery.typical.capacity"):
            return self.value("battery.typical.capacity")
        return self.value("battery.capacity")

    def summary(self):
        """What the report says about the figures' origins."""
        return {"file": bool(self.table), "sources": dict(sorted(self.sources.items()))}
