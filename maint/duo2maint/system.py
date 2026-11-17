"""What duo2-update, duo2-snapshot and duo2-backup read from the running
system (the impure helpers they share)."""

import glob
import hashlib
import os
import subprocess
import tarfile

ENV = dict(
    os.environ,
    DEBIAN_FRONTEND="noninteractive",
    APT_LISTCHANGES_FRONTEND="none",
    NEEDRESTART_MODE="l",
    LC_ALL="C.UTF-8",
    LANG="C.UTF-8",
)

PROTECTED_TREES = ["/boot/efi", "/usr/lib/firmware"]


def run(argv, timeout=None, env=None, cwd=None):
    try:
        r = subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout, env=env or ENV, cwd=cwd
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return 127, "", str(e)
    return r.returncode, r.stdout, r.stderr


def stream(argv, log_path, env=None):
    """Run a command, its output to the terminal and to log_path."""
    with open(log_path, "a") as log:
        p = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=env or ENV,
            bufsize=1,
        )
        for line in p.stdout:
            print(line, end="", flush=True)
            log.write(line)
        return p.wait()


def dpkg_packages_text():
    rc, out, _ = run(
        ["dpkg-query", "-W", "-f", "${Package}\t${Architecture}\t${Version}\t${db:Status-Abbrev}\n"]
    )
    return out if rc == 0 else ""


def kernel():
    return os.uname().release


def protected_files(root="/"):
    """The files duo2-update must find unchanged after an upgrade: the ESP,
    the firmware, the running kernel's modules."""
    trees = [os.path.join(root, t.lstrip("/")) for t in PROTECTED_TREES]
    trees.append(os.path.join(root, "usr/lib/modules", kernel()))
    out = []
    for tree in trees:
        for d, dirs, files in os.walk(tree):
            dirs.sort()
            for f in sorted(files):
                if f == "random-seed":  # systemd-boot's, rewritten by the boot loader every boot
                    continue
                out.append(os.path.join(d, f))
    return out


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def protected_sums(root="/"):
    out = {}
    for path in protected_files(root):
        try:
            if os.path.islink(path):
                out[path] = hashlib.sha256(("link:" + os.readlink(path)).encode()).hexdigest()
            else:
                out[path] = sha256(path)
        except OSError:
            continue
    return out


def sums_text(sums):
    return "".join("%s  %s\n" % (h, p) for p, h in sorted(sums.items()))


def usr_links(root="/"):
    out = {}
    for d in ("bin", "sbin", "lib", "lib64", "lib32", "libx32"):
        path = os.path.join(root, d)
        if os.path.islink(path):
            out["/" + d] = os.readlink(path)
    return out


def owned_paths(info="/var/lib/dpkg/info"):
    owned = set()
    for path in glob.glob(os.path.join(info, "*.list")):
        try:
            with open(path, errors="replace") as f:
                owned.update(line.rstrip("\n") for line in f if line.strip())
        except OSError:
            continue
    return owned


def deb_paths(deb):
    """The regular files and links a .deb would write (paths as in it)."""
    p = subprocess.Popen(["dpkg-deb", "--fsys-tarfile", deb], stdout=subprocess.PIPE)
    out = []
    try:
        with tarfile.open(fileobj=p.stdout, mode="r|") as tar:
            for member in tar:
                if member.isfile() or member.issym() or member.islnk():
                    out.append(member.name)
    except tarfile.TarError:
        pass
    finally:
        p.stdout.close()
        p.wait()
    return out


def gui_user():
    """The GUI user: duo-wc.service's User= ("" when there is none)."""
    rc, out, _err = run(["systemctl", "show", "-P", "User", "duo-wc.service"], timeout=10)
    return out.strip() if rc == 0 else ""


def power_supplies(base="/sys/class/power_supply"):
    out = []
    for d in sorted(glob.glob(os.path.join(base, "*"))):
        entry = {}
        for key in ("type", "online", "status", "capacity"):
            try:
                with open(os.path.join(d, key)) as f:
                    entry[key] = f.read().strip()
            except OSError:
                pass
        out.append(entry)
    return out


def battery_percent(supplies):
    levels = [
        int(s["capacity"])
        for s in supplies
        if s.get("type") == "Battery" and str(s.get("capacity", "")).isdigit()
    ]
    return min(levels) if levels else None


def default_route_dev():
    rc, out, _ = run(["ip", "-o", "route", "get", "1.1.1.1"], timeout=10)
    if rc != 0:
        return None
    parts = out.split()
    return parts[parts.index("dev") + 1] if "dev" in parts[:-1] else None


def free_bytes(path="/"):
    st = os.statvfs(path)
    return st.f_bavail * st.f_frsize


def failed_units():
    rc, out, _ = run(["systemctl", "--failed", "--plain", "--no-legend", "--no-pager"], timeout=30)
    return sorted(line.split()[0] for line in out.splitlines() if line.strip()) if rc == 0 else []


def deleted_maps():
    """Processes still running code from files an upgrade replaced: [(pid,
    command, unit)]: they run the old version until restarted."""
    out = []
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            with open("/proc/%s/maps" % pid) as f:
                maps = f.read()
        except OSError:
            continue
        stale = any(
            m.endswith("(deleted)") and (" /usr/" in m or " /lib" in m) for m in maps.splitlines()
        )
        if not stale:
            continue
        try:
            with open("/proc/%s/comm" % pid) as f:
                comm = f.read().strip()
            with open("/proc/%s/cgroup" % pid) as f:
                cg = f.read().strip().rsplit("/", 1)[-1]
        except OSError:
            continue
        out.append((int(pid), comm, cg))
    return sorted(out)
