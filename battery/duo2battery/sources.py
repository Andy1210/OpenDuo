"""What the collector reads: /proc, sysfs, debugfs, duo-wc. The parsers are
pure (tested); the readers return None or {} when a source is missing."""

import fcntl
import json
import os
import socket
import struct

CLK_TCK = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100
QCOM_STATS = "/sys/kernel/debug/qcom_stats"
QCOM_TICK_HZ = 19.2e6
DSPS = ("adsp", "cdsp", "slpi")
SUBSYSTEMS = DSPS + ("modem",)
BAT = "/sys/class/power_supply/qcom-battmgr-bat"
USB = "/sys/class/power_supply/qcom-battmgr-usb"
PANELS = (
    ("DSI-1", "/sys/class/drm/card0-DSI-1", "/sys/class/backlight/ae94000.dsi.0"),
    ("DSI-2", "/sys/class/drm/card0-DSI-2", "/sys/class/backlight/ae96000.dsi.0"),
)
REFRESH = "/sys/module/panel_microsoft_elgin/parameters/refresh"
WIFI_IF = "wlan0"
MODEM_IF = "rmnet_ipa0"
DRM_CLIENTS = ("/sys/kernel/debug/dri/0/clients", "/sys/kernel/debug/dri/128/clients")
WC_ADMIN = "/run/duo-wc/admin.sock"


def read(path, limit=65536):
    try:
        with open(path) as f:
            return f.read(limit)
    except (OSError, UnicodeDecodeError):
        return None


def read_int(path):
    t = read(path, 64)
    try:
        return int(t.strip()) if t is not None else None
    except ValueError:
        return None


# -- processes --------------------------------------------------------------------


def parse_stat(text):
    """/proc/PID/stat (or task/TID/stat) -> the fields we use; times in ticks."""
    lp, rp = text.index("("), text.rindex(")")
    rest = text[rp + 2 :].split()
    return {
        "comm": text[lp + 1 : rp],
        "state": rest[0],
        "ppid": int(rest[1]),
        "flags": int(rest[6]),
        "utime": int(rest[11]),
        "stime": int(rest[12]),
        "cutime": int(rest[13]),
        "cstime": int(rest[14]),
        "threads": int(rest[17]),
        "start": int(rest[19]),
        "processor": int(rest[36]) if len(rest) > 36 else -1,
    }


def read_pid_stat(path):
    t = read(path, 2048)
    if not t:
        return None
    try:
        return parse_stat(t)
    except (ValueError, IndexError):
        return None


def parse_proc_stat(text):
    """/proc/stat -> {cpu: (task_ticks, irq_ticks)}: user+nice+system, and
    irq+softirq (the kernel's own, attributed to system:kernel)."""
    out = {}
    for line in text.splitlines():
        if not line.startswith("cpu") or line.startswith("cpu "):
            continue
        f = line.split()
        try:
            cpu = int(f[0][3:])
            v = [int(x) for x in f[1:8]]
        except ValueError:
            continue
        out[cpu] = (v[0] + v[1] + v[2], v[5] + v[6])
    return out


def parse_time_in_state(text):
    """cpufreq stats time_in_state -> {khz: seconds} (the file counts in
    USER_HZ ticks)."""
    out = {}
    for line in (text or "").splitlines():
        f = line.split()
        if len(f) == 2:
            try:
                out[int(f[0])] = int(f[1]) / float(CLK_TCK)
            except ValueError:
                pass
    return out


def cpufreq_policies(base="/sys/devices/system/cpu/cpufreq"):
    """[(policy dir, [cpus])] little to big."""
    out = []
    try:
        names = sorted(os.listdir(base))
    except OSError:
        return out
    for n in names:
        if not n.startswith("policy"):
            continue
        cpus = read(os.path.join(base, n, "related_cpus")) or read(
            os.path.join(base, n, "affected_cpus")
        )
        if cpus:
            try:
                out.append((os.path.join(base, n), sorted(int(c) for c in cpus.split())))
            except ValueError:
                pass
    return sorted(out, key=lambda p: min(p[1]) if p[1] else 99)


# -- the subsystems' sleep (debugfs, root) -----------------------------------------


def parse_qcom_stats(text):
    """{count, entered, exited, sleep} in ticks of the 19.2 MHz counter."""
    out = {}
    for line in (text or "").splitlines():
        k, _, v = line.partition(":")
        k = k.strip().lower()
        try:
            v = int(v.strip())
        except ValueError:
            continue
        if k == "count":
            out["count"] = v
        elif k == "last entered at":
            out["entered"] = v
        elif k == "last exited at":
            out["exited"] = v
        elif k == "accumulated duration":
            out["sleep"] = v
    return out if "sleep" in out else None


def read_qcom_stats():
    return {
        name: parse_qcom_stats(read(os.path.join(QCOM_STATS, name), 512)) for name in SUBSYSTEMS
    }


def remoteproc_states(base="/sys/class/remoteproc"):
    out = {}
    try:
        for r in os.listdir(base):
            name = (read(os.path.join(base, r, "name"), 64) or "").strip()
            state = (read(os.path.join(base, r, "state"), 64) or "").strip()
            if name:
                out[name] = state == "running"
    except OSError:
        pass
    return out


class TickClock:
    """`now` in the subsystems' counter ticks, from CLOCK_BOOTTIME plus an
    offset (the counter started before the kernel). No subsystem event can
    lie in the future, so every seen event bounds the offset from below; the
    largest bound converges within one DSP wake (the ADSP alone wakes about
    once a second)."""

    def __init__(self):
        self.offset = None

    def observe(self, boot_s, stats):
        for st in stats.values():
            if not st:
                continue
            ev = max(st.get("entered", 0), st.get("exited", 0))
            if ev:
                bound = ev - boot_s * QCOM_TICK_HZ
                if self.offset is None or bound > self.offset:
                    self.offset = bound

    def now(self, boot_s):
        return boot_s * QCOM_TICK_HZ + (self.offset or 0)


def sleep_ticks(st, now_ticks):
    """Total sleep so far, the sleep in progress included."""
    if not st:
        return None
    s = st["sleep"]
    if st.get("entered", 0) > st.get("exited", 0):
        s += max(0, now_ticks - st["entered"])
    return s


# -- battery, screen, radios ----------------------------------------------------------


def read_battery():
    def s(name):
        return (read(os.path.join(BAT, name), 64) or "").strip()

    out = {
        "level": read_int(os.path.join(BAT, "capacity")),
        "status": s("status") or "Unknown",
        "charge_uah": read_int(os.path.join(BAT, "charge_counter")),
        "current_ua": read_int(os.path.join(BAT, "current_now")),
        "voltage_uv": read_int(os.path.join(BAT, "voltage_now")),
        "usb_online": read_int(os.path.join(USB, "online")) == 1,
    }
    # each read is a round trip to the ADSP's battery manager: the charger's
    # input only when there is a charger
    out["usb_ua"] = read_int(os.path.join(USB, "current_now")) if out["usb_online"] else 0
    out["usb_uv"] = read_int(os.path.join(USB, "voltage_now")) if out["usb_online"] else 0
    return out


def read_screen():
    """[(lit, brightness 0..1)] per panel, from the DRM connector and the
    backlight (no duo-wc needed)."""
    out = []
    for _name, conn, bl in PANELS:
        lit = (read(os.path.join(conn, "dpms"), 16) or "").strip() == "On"
        b, m = (
            read_int(os.path.join(bl, "actual_brightness")),
            read_int(os.path.join(bl, "max_brightness")),
        )
        out.append((lit, (b / m) if lit and b is not None and m else 0.0))
    return out


def read_net(iface):
    base = "/sys/class/net/%s" % iface
    st = (read(base + "/operstate", 32) or "").strip()
    if not st:
        return None
    vals = [
        read_int(base + "/statistics/" + k) or 0
        for k in ("rx_bytes", "tx_bytes", "rx_packets", "tx_packets")
    ]
    return {
        "up": st in ("up", "unknown"),
        "rx_bytes": vals[0],
        "tx_bytes": vals[1],
        "rx_pkts": vals[2],
        "tx_pkts": vals[3],
    }


HCIGETDEVINFO = 0x800448D3
HCI_INFO = struct.Struct("<H8s6sIB8sxxxIIIHHHH10I")


def parse_hci_dev_info(buf):
    f = HCI_INFO.unpack(buf[: HCI_INFO.size])
    stats = f[13:]
    return {
        "up": bool(f[3] & 1),
        "bytes": stats[8] + stats[9],
        "pkts": stats[4] + stats[5] + stats[6] + stats[7],
    }


def read_bluetooth(dev=0):
    if not hasattr(socket, "AF_BLUETOOTH"):
        return None
    try:
        with socket.socket(socket.AF_BLUETOOTH, socket.SOCK_RAW, socket.BTPROTO_HCI) as s:
            buf = bytearray(HCI_INFO.size)
            struct.pack_into("<H", buf, 0, dev)
            fcntl.ioctl(s.fileno(), HCIGETDEVINFO, buf)
            return parse_hci_dev_info(bytes(buf))
    except OSError:
        return None


# -- the GPU (msm DRM fdinfo) -------------------------------------------------------


def parse_drm_clients(text):
    """debugfs dri/N/clients -> {client id: opener tgid}."""
    out = {}
    for line in (text or "").splitlines()[1:]:
        f = line.split()
        if len(f) >= 3:
            try:
                out[int(f[-1])] = int(f[1])
            except ValueError:
                pass
    return out


def read_drm_clients():
    out = {}
    for p in DRM_CLIENTS:
        out.update(parse_drm_clients(read(p)))
    return out


def parse_drm_fdinfo(text):
    """-> (client id, engine ns, cycles) or None (not a DRM fd)."""
    cid = ns = cycles = None
    for line in (text or "").splitlines():
        k, _, v = line.partition(":")
        v = v.strip()
        try:
            if k == "drm-client-id":
                cid = int(v)
            elif k == "drm-engine-gpu":
                ns = int(v.split()[0])
            elif k == "drm-cycles-gpu":
                cycles = int(v.split()[0])
        except (ValueError, IndexError):
            pass
    return (cid, ns or 0, cycles or 0) if cid is not None else None


def drm_fds(pid):
    """[(fd, client id)] of a process's DRM files. From /proc/PID/fdinfo,
    which the collector may read (CAP_SYS_PTRACE), not /proc/PID/fd: that
    directory is 0500 of the process's user, and the collector keeps no
    CAP_DAC_READ_SEARCH."""
    out = []
    base = "/proc/%d/fdinfo" % pid
    try:
        fds = os.listdir(base)
    except OSError:
        return out
    for fd in fds:
        info = parse_drm_fdinfo(read(os.path.join(base, fd), 4096))
        if info:
            try:
                out.append((int(fd), info[0]))
            except ValueError:
                pass
    return out


# -- duo-wc (root answers on its admin socket: we only ask get_state) ----------------


def wc_state(timeout=1.0):
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            s.connect(WC_ADMIN)
            s.sendall(b'{"id":1,"request":"get_state"}\n')
            buf = b""
            while not buf.endswith(b"\n") and len(buf) < 1 << 20:
                chunk = s.recv(65536)
                if not chunk:
                    break
                buf += chunk
        return json.loads(buf.decode() or "{}").get("ok")
    except (OSError, ValueError):
        return None


def visible_apps(state):
    """The app ids shown on a lit panel: per output the frontmost window
    that is not minimized (two in a split), a spanning one on every lit panel."""
    if not state:
        return set()
    lit = set(state.get("lit") or [])
    if not lit:
        return set()
    windows = [w for w in state.get("windows") or [] if isinstance(w, dict)]
    by_id = {w.get("id"): w for w in windows}
    shown = set()
    split_shown = set()
    for sp in state.get("splits") or []:
        if isinstance(sp, dict) and sp.get("front") and sp.get("output") in lit:
            split_shown.add(sp["output"])
            for wid in sp.get("windows") or []:
                w = by_id.get(wid)
                if w and not w.get("minimized") and w.get("app_id"):
                    shown.add(w["app_id"])
    front = {}
    for w in windows:  # back to front
        if w.get("minimized"):
            continue
        pl = w.get("placement")
        outs = sorted(lit) if pl == "span" else [pl.get("output")] if isinstance(pl, dict) else []
        for o in outs:
            if o in lit and o not in split_shown:
                front[o] = w.get("app_id")
    shown.update(a for a in front.values() if a)
    return shown
