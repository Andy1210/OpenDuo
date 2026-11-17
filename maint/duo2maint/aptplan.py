"""What an apt upgrade would do on the phone, and whether that is safe
(pure). docs/UPDATES.md.

The phone is Debian trixie with things Debian does not know about: our
kernel and its modules (not a package), the firmware in /usr/lib/firmware
(no package owns any of it: a Debian firmware package would overwrite it
without a word), the ESP (systemd-boot, kernels, DTBs, initrds: written only
by duo2-try / duo2-promote), /usr/local and /opt (our components), our
rebuilt packages (pipewire 1.4.2-1+duo2.N, libcamera 0.7.2-1~duo2.N, which
sort above Debian's security updates of the same release, so apt keeps
ours and the fix is missed), and local edits of package files (Waydroid's,
re-applied after every dpkg run). So before an upgrade:

  - nothing from FORBIDDEN gets installed (a kernel package, a boot loader
    installer, firmware packages; also pinned away: apt/preferences.d/duo2-never),
  - nothing is removed (a protected package never, others only when asked),
  - none of our rebuilt packages is replaced by Debian's,
  - no file that no package owns is overwritten (refused under PROTECTED_PATHS),
  - and it says which rebuilt packages Debian has a newer source for
    (rebuild them), which local edits the upgrade replaces, and what to try
    out afterwards.
"""

import fnmatch
import re

from . import debver

FORBIDDEN = [
    "linux-image-*",
    "linux-headers-*",
    "linux-kbuild-*",
    "flash-kernel",
    "grub*",
    "shim*",
    "systemd-boot",
    "systemd-boot-signed*",
    "u-boot*",
    "raspi-firmware",
    "firmware-*",
]

PROTECTED = [
    "systemd",
    "systemd-sysv",
    "udev",
    "dbus",
    "dbus-daemon",
    "openssh-server",
    "sudo",
    "polkitd",
    "wpasupplicant",
    "iw",
    "wireless-regdb",
    "systemd-resolved",
    "systemd-timesyncd",
    "modemmanager",
    "libmm-glib0",
    "libqmi-glib5",
    "libqmi-proxy",
    "libmbim-glib4",
    "libqrtr-glib0",
    "qrtr-tools",
    "rmtfs",
    "tqftpserv",
    "hexagonrpcd",
    "iio-sensor-proxy",
    "seatd",
    "libseat1",
    "python3",
    "python3-gi",
    "gir1.2-gtk-4.0",
    "gir1.2-gtk4layershell-1.0",
    "pipewire",
    "wireplumber",
    "bluez",
    "upower",
    "waydroid",
    "python3-gbinder",
    "libgbinder",
    "lxc",
    "apparmor",
    "nftables",
    "geoclue-2.0",
    "gnome-calls",
    "callaudiod",
    "feedbackd",
    "chatty",
    "mmsd-tng",
    "plymouth",
    "initramfs-tools",
    "e2fsprogs",
    "dosfstools",
]

PROTECTED_PATHS = [
    "/boot/efi/",
    "/usr/lib/firmware/",
    "/usr/lib/modules/",
    "/usr/local/",
    "/opt/",
    "/etc/duo2/",
    "/var/lib/duo2",
]

# A packaged kernel may take over the unowned modules of its own release:
# the same release string is the same build (scripts/duo2-try or the image
# installed it by hand).
KERNEL_PACKAGE = "openduo-kernel-surface-duo2-"


def adopts(pkg, path):
    if not pkg.startswith(KERNEL_PACKAGE):
        return False
    rel = pkg[len(KERNEL_PACKAGE) :]
    return bool(rel) and path.startswith("/usr/lib/modules/%s/" % rel)


def kernel_esp_file(path):
    """The packaged kernels' own files on the ESP (duo2-boot writes them)."""
    return path.startswith("/boot/efi/openduo/") or path.startswith(
        "/boot/efi/loader/entries/openduo-"
    )


# Local edits a dpkg run is known to undo and something puts back.
REAPPLIED = {
    "/usr/lib/waydroid/tools/": "re-applied by duo2-waydroid-patch after every dpkg run",
}

HINTS = [
    (
        [
            "modemmanager",
            "libmm-glib*",
            "libqmi*",
            "libmbim*",
            "libqrtr*",
            "qrtr-tools",
            "rmtfs",
            "tqftpserv",
        ],
        "modem: a call, an SMS, mobile data without WiFi, Settings > SIM",
    ),
    (
        [
            "libgl1-mesa-dri",
            "libegl-mesa0",
            "libgbm1",
            "mesa-*",
            "libglx-mesa0",
            "libgles*",
            "libvulkan1",
        ],
        "GPU: both panels draw, a video plays (scripts/duo2-video-test), an Android app opens",
    ),
    (
        ["wpasupplicant", "iw", "wireless-regdb", "systemd", "libnl-*"],
        "WiFi: reconnects after a reboot (keep the USB / rescue path at hand)",
    ),
    (["bluez", "libspa-0.2-bluetooth"], "Bluetooth: a paired device connects"),
    (
        [
            "pipewire*",
            "wireplumber",
            "libwireplumber*",
            "alsa-ucm-conf",
            "alsa-utils",
            "libasound2*",
        ],
        "audio: the speaker, the earpiece in a call, the microphone (scripts/duo2-voice-check)",
    ),
    (
        ["iio-sensor-proxy", "libssc*", "hexagonrpcd"],
        "sensors: rotation, proximity in a call, brightness",
    ),
    (
        [
            "waydroid",
            "lxc",
            "liblxc*",
            "python3-gbinder",
            "libgbinder",
            "libglibutil",
            "apparmor",
            "nftables",
        ],
        "Android: an Android app starts (duo2-waydroid-patch --check is clean)",
    ),
    (
        ["plymouth*", "initramfs-tools*"],
        "boot: the splash on both panels; update_initramfs=no kept",
    ),
    (
        ["systemd", "udev", "dbus*", "libsystemd0", "libudev1"],
        "systemd re-executes itself while upgrading; reboot afterwards and run scripts/duo2-health",
    ),
    (["openssh-server", "openssh-client"], "ssh: log in again (keep the open session until then)"),
    (["geoclue-2.0", "libgeoclue*"], "location: scripts/duo2-location-test"),
    (["squeekboard", "phosh-osk-stub*", "libpresage*"], "the on-screen keyboard"),
    (
        ["gnome-calls", "callaudiod", "feedbackd", "chatty", "mmsd-tng", "gnome-contacts"],
        "calls and messages: Calls rings and vibrates, Chatty sends an SMS",
    ),
    (
        [
            "libgtk-4-*",
            "gir1.2-gtk-4.0",
            "libadwaita*",
            "gir1.2-adw-1",
            "python3-gi*",
            "libgtk4-layer-shell0",
        ],
        "the shell and Settings start (bars, dock, quick menu)",
    ),
    (["chromium*", "firefox-esr*"], "the browsers start"),
    (
        ["libcamera*", "gstreamer1.0-libcamera"],
        "the cameras (cam -l lists them, Snapshot takes a photo)",
    ),
]


def matches(name, patterns):
    return any(fnmatch.fnmatchcase(name, p) for p in patterns)


# -- apt-get -s output ----------------------------------------------------------------------

INST = re.compile(r"^Inst (\S+) (?:\[(\S+)\] )?\((\S+) (.*?)(?: \[(\S+)\])?\)")
REMV = re.compile(r"^Remv (\S+)(?: \[(\S+)\])?")


def parse_simulation(text):
    """`apt-get -s dist-upgrade`: {"install": [(name, old or None, new,
    origin)], "remove": [(name, old)]}."""
    install, remove = [], []
    for line in (text or "").splitlines():
        m = INST.match(line)
        if m:
            name = m.group(1).split(":")[0]
            install.append((name, m.group(2), m.group(3), m.group(4)))
            continue
        m = REMV.match(line)
        if m:
            remove.append((m.group(1).split(":")[0], m.group(2)))
    return {"install": install, "remove": remove}


def is_security(origin):
    return "security" in (origin or "").lower()


# -- apt-cache policy -----------------------------------------------------------------------


def parse_policy(text):
    """`apt-cache policy PKG...`: {pkg: {"installed": v or None,
    "versions": {version: [origin, ...]}}}."""
    out, pkg, cur = {}, None, None
    for line in (text or "").splitlines():
        if line and not line.startswith(" ") and line.endswith(":"):
            pkg = line[:-1].split(":")[0]
            out[pkg] = {"installed": None, "versions": {}}
            cur = None
            continue
        if pkg is None:
            continue
        s = line.strip()
        if s.startswith("Installed:"):
            v = s.split(":", 1)[1].strip()
            out[pkg]["installed"] = None if v == "(none)" else v
            continue
        m = re.match(r"^(?:\*\*\* )?(\S+) (-?\d+)$", s)
        if m and not s.startswith(("Candidate:", "Version table")):
            cur = m.group(1)
            out[pkg]["versions"].setdefault(cur, [])
            continue
        m = re.match(r"^(-?\d+) (\S.*)$", s)
        if m and cur is not None:
            out[pkg]["versions"][cur].append(m.group(2))
    return out


def archive_versions(entry):
    """The versions a repository offers (not just the installed one's
    /var/lib/dpkg/status line)."""
    return [
        v
        for v, origins in entry["versions"].items()
        if any(o.split()[0].startswith(("http://", "https://", "file:", "cdrom:")) for o in origins)
    ]


def rebuilds_needed(policy):
    """Our rebuilt packages whose Debian source has moved past the one we
    built from: [(pkg, installed, base, newest Debian version)]."""
    out = []
    for pkg, entry in sorted(policy.items()):
        inst = entry.get("installed")
        if not inst or not debver.is_local(inst):
            continue
        base = debver.local_base(inst)
        offered = [v for v in archive_versions(entry) if not debver.is_local(v)]
        if not offered:
            continue
        best = offered[0]
        for v in offered[1:]:
            if debver.newer(v, best):
                best = v
        if debver.newer(best, base):
            out.append((pkg, inst, base, best))
    return out


# -- the verdict ------------------------------------------------------------------------------


def review(plan, installed, allow_removals=False):
    """Problems that stop an upgrade ("refuse") and things to know
    ("note"). `installed`: {pkg: version} now."""
    refuse, note = [], []
    for name, old, new, _origin in plan["install"]:
        if matches(name, FORBIDDEN):
            refuse.append(
                "%s would be installed (never on this phone: apt/preferences.d/duo2-never)" % name
            )
        cur = installed.get(name)
        if cur and debver.is_local(cur) and not debver.is_local(new):
            refuse.append(
                "%s %s is our rebuild; apt would replace it with %s (rebuild instead)"
                % (name, cur, new)
            )
        if old is None:
            note.append("new package: %s %s" % (name, new))
    for name, _old in plan["remove"]:
        if matches(name, PROTECTED):
            refuse.append("%s would be removed (the phone needs it)" % name)
        elif not allow_removals:
            refuse.append("%s would be removed (allow with --allow-removals after a look)" % name)
        else:
            note.append("removed: %s" % name)
    return refuse, note


def hints(names):
    out = []
    for patterns, text in HINTS:
        if any(matches(n, patterns) for n in names) and text not in out:
            out.append(text)
    return out


# -- files ------------------------------------------------------------------------------------


def usr_aliases(links):
    """{"/lib/": "/usr/lib/", ...} for the top-level directories that are
    symlinks into /usr (merged /usr). `links`: {"/lib": "usr/lib", ...}."""
    out = {}
    for path, target in links.items():
        target = target if target.startswith("/") else "/" + target
        out[path.rstrip("/") + "/"] = target.rstrip("/") + "/"
    return out


def normalize(path, aliases):
    """A path from a .deb ("./usr/lib/x") or dpkg's lists ("/lib/x") as
    the file it is on this system ("/usr/lib/x")."""
    p = path[1:] if path.startswith("./") else path
    p = re.sub(r"/+", "/", "/" + p.lstrip("/"))
    for alias, real in aliases.items():
        if p.startswith(alias):
            return real + p[len(alias) :]
    return p


def protected_path(path):
    return any(path.startswith(p) for p in PROTECTED_PATHS)


def overwrites(new_files, owned, exists, aliases):
    """Files the new packages would write over that no package owns now
    (ours): [(pkg, path, protected)]. `new_files`: {pkg: [paths of regular
    files and links in its .deb]}, `owned`: the set of paths dpkg knows
    (any package), `exists(path)`: whether something is there now."""
    owned_n = {normalize(p, aliases) for p in owned}
    out = []
    for pkg, paths in sorted(new_files.items()):
        for p in paths:
            n = normalize(p, aliases)
            if n in owned_n or not exists(n):
                continue
            out.append((pkg, n, protected_path(n)))
    return out


def parse_verify(text):
    """`dpkg --verify` lines: [(path, is_conffile)] of changed files."""
    out = []
    for line in (text or "").splitlines():
        m = re.match(r"^(\S{9}) (c| ) (/.*)$", line)
        if m:
            out.append((m.group(3), m.group(2) == "c"))
    return out


def edits_replaced(changed):
    """What an upgrade does to locally changed package files: conffiles
    stay ours (--force-confold; Debian's new one lands as .dpkg-dist), other
    files are replaced. [(path, what happens)]."""
    out = []
    for path, conf in changed:
        if conf:
            out.append((path, "kept (ours); the new default is saved as %s.dpkg-dist" % path))
            continue
        for prefix, why in REAPPLIED.items():
            if path.startswith(prefix):
                out.append((path, "replaced, " + why))
                break
        else:
            out.append((path, "REPLACED by the package's version: the local change is lost"))
    return out
