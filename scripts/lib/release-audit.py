#!/usr/bin/env python3
"""Check a release image before it is published (scripts/build-release.sh).

    release-audit.py ROOTFS.tar ESP.tar USER [ALLOWED...]

ROOTFS.tar is the root file system (scripts/filter-export.py), ESP.tar what
the kernel package put on the ESP. Fails (exit 1, every problem listed) when
the image carries what may not be redistributed or what is personal, or when
it is not set up the way a published image must be:

- no stock firmware, touch runtime, sensor configuration, camera table or
  power profile (duo2-stock-import brings the phone's own at its first boot);
- no ssh key anywhere, no ssh host key, an empty machine-id (D-Bus's copy
  a link to it), developer mode off and sshd not enabled;
- every password locked, the GUI user USER without sudo rights beyond the
  components' own helper rules;
- the import's first-boot unit enabled, the packages installed, the
  kernel's ESP entry blessed;
- wpa_supplicant@wlan0 enabled, its configuration root's, 0600 and without
  a network, and kept 0600 when Settings saves a network (UMask=0077);
- none of the local personal patterns (local.personal-data, the same list
  scripts/check-personal-data reads) in the text files of /etc, /home,
  /root, /var/lib, /usr/local and /opt. ALLOWED are strings that are public
  on purpose (the repository's URL in the keyring package's sources): they
  are taken out of a file before the patterns are matched.
"""

import os
import re
import subprocess
import sys
import tarfile

STOCK = (
    re.compile(r"^usr/lib/firmware/(qcom|qca|ath11k)/"),
    re.compile(r"^(usr/)?lib/firmware/.*\.(mbn|mdt|b\d\d)$"),
    re.compile(r"^opt/duo2-touch(/|$)"),
    re.compile(r"^opt/duo2-power-profile(/|$)"),
    re.compile(r"^usr/share/qcom/"),
    re.compile(r"libsurfacetouch|com\.qti\.sensormodule|sns_reg|imx48\d-|imx563-"),
)
SCAN = ("etc/", "home/", "root/", "var/lib/", "usr/local/", "opt/")
# a key, not the example "ssh-ed25519 AAAA..." of a hint text
KEY = re.compile(
    rb"\b(ssh-(ed25519|rsa|dss)|ecdsa-sha2-nistp\d+|sk-ssh-ed25519@openssh\.com) AAAA[0-9A-Za-z+/]{20,}"
)
# rootfs/overlay/etc/wpa_supplicant/wpa_supplicant-wlan0.conf, exactly; and
# the drop-in that keeps it 0600 when wpa_supplicant saves a network into it
WPA_CONF_PATH = "etc/wpa_supplicant/wpa_supplicant-wlan0.conf"
WPA_CONF = "ctrl_interface=DIR=/run/wpa_supplicant GROUP=netdev\nupdate_config=1\n"
WPA_DROPIN = "etc/systemd/system/wpa_supplicant@.service.d/duo2.conf"


def local_patterns():
    here = os.path.dirname(os.path.abspath(__file__))
    repo = os.path.dirname(os.path.dirname(here))
    cands = [os.environ.get("DUO2_PERSONAL_DATA", ""), os.path.join(repo, "local.personal-data")]
    try:
        common = subprocess.run(
            ["git", "-C", repo, "rev-parse", "--path-format=absolute", "--git-common-dir"],
            capture_output=True,
            text=True,
        ).stdout.strip()
        if common:
            cands.append(os.path.join(os.path.dirname(common), "local.personal-data"))
    except OSError:
        pass
    for p in cands:
        if p and os.path.isfile(p):
            pats = []
            with open(p) as f:
                for line in f:
                    line = line.rstrip("\n")
                    if line and not line.startswith(("#", "!")):
                        pats.append(re.compile(line.encode()))
            return p, pats
    return None, []


def main(argv):
    if len(argv) < 3:
        sys.exit(__doc__)
    rootfs, esp, user = argv[:3]
    allowed = [a.encode() for a in argv[3:] if a]
    bad = []
    members = {}
    _, pats = local_patterns()
    if not pats:
        bad.append("no local.personal-data: the personal patterns were not checked")
    with tarfile.open(rootfs) as t:
        for m in t:
            name = m.name.removeprefix("./")
            members[name] = m
            # files only: an empty directory (a mount or install point) is harmless
            if not m.isdir() and any(rx.search(name) for rx in STOCK):
                bad.append("stock file in the image: %s" % name)
            if m.isfile() and name.startswith(SCAN) and m.size < (1 << 20):
                data = t.extractfile(m).read()
                if b"\0" in data[:4096]:
                    continue
                if KEY.search(data):
                    bad.append("an ssh key in %s" % name)
                for a in allowed:
                    data = data.replace(a, b"")
                for rx in pats:
                    if rx.search(data):
                        bad.append("a personal pattern in %s" % name)
                        break

        def read(name):
            m = members.get(name)
            if m is None or not m.isfile():
                return None
            return t.extractfile(m).read().decode(errors="replace")

        def exists(name):
            return name in members or name + "/" in members

        for n in [n for n in members if n.startswith("etc/ssh/ssh_host_")]:
            bad.append("an ssh host key: %s" % n)
        if (read("etc/machine-id") or "").strip():
            bad.append("etc/machine-id is not empty")
        dbus_id = members.get("var/lib/dbus/machine-id")
        if dbus_id is not None and not (
            (dbus_id.issym() and dbus_id.linkname in ("/etc/machine-id", "../../../etc/machine-id"))
            or (dbus_id.isfile() and not (read("var/lib/dbus/machine-id") or "").strip())
        ):
            bad.append("var/lib/dbus/machine-id holds an id (every phone would share it)")
        if (read("etc/duo2/gui-user") or "").strip() != user:
            bad.append("etc/duo2/gui-user is not %s" % user)
        devmode = read("etc/duo-devmode.conf") or ""
        if not re.search(r"^developer_mode\s*=\s*off\s*$", devmode, re.M):
            bad.append("developer mode is not off (etc/duo-devmode.conf)")
        for unit in ("multi-user.target.wants/ssh.service", "sockets.target.wants/ssh.socket"):
            if exists("etc/systemd/system/" + unit):
                bad.append("sshd is enabled (%s)" % unit)
        if not exists("etc/systemd/system/multi-user.target.wants/duo2-stock-import.service"):
            bad.append("duo2-stock-import.service is not enabled")
        # WiFi from the first start: wpa_supplicant@wlan0 with an empty,
        # root-only configuration that Settings > WiFi saves networks into.
        if not exists("etc/systemd/system/multi-user.target.wants/wpa_supplicant@wlan0.service"):
            bad.append("wpa_supplicant@wlan0.service is not enabled")
        m = members.get(WPA_CONF_PATH)
        if m is None or not m.isfile():
            bad.append("no %s" % WPA_CONF_PATH)
        else:
            if (m.mode & 0o7777, m.uid, m.gid) != (0o600, 0, 0):
                bad.append("%s is not root's with mode 0600" % WPA_CONF_PATH)
            if read(WPA_CONF_PATH) != WPA_CONF:
                bad.append("%s is not the release's empty configuration" % WPA_CONF_PATH)
        if not re.search(r"^UMask=0077$", read(WPA_DROPIN) or "", re.M):
            bad.append(
                "no UMask=0077 for wpa_supplicant@ (%s): a saved network's key would be readable"
                % WPA_DROPIN
            )
        for n in ("home/duo2admin/.ssh/authorized_keys", "etc/ssh/authorized_keys.d/" + user):
            m = members.get(n)
            if m is not None and m.size:
                bad.append("%s is not empty" % n)
        shadow = read("etc/shadow") or ""
        for line in shadow.splitlines():
            f = line.split(":")
            if len(f) > 1 and f[1] and not f[1].startswith(("!", "*")):
                bad.append("a password is set in etc/shadow for %s" % f[0])
        group = read("etc/group") or ""
        for line in group.splitlines():
            f = line.split(":")
            if (
                len(f) > 3
                and f[0] in ("sudo", "wheel", "admin", "root")
                and user in f[3].split(",")
            ):
                bad.append("%s is in the group %s" % (user, f[0]))
        for n in [n for n in members if n.startswith("etc/sudoers.d/") and members[n].isfile()]:
            for line in (read(n) or "").splitlines():
                s = line.strip()
                if s.startswith(user + " ") and re.search(r"NOPASSWD:\s*ALL\b", s):
                    bad.append("%s may run anything as root (%s)" % (user, n))
        status = read("var/lib/dpkg/status") or ""
        for pkg in (
            "openduo-surface-duo2",
            "openduo-stock-surface-duo2",
            "openduo-archive-keyring",
        ):
            if not re.search(
                r"^Package: %s\nStatus: install ok installed$" % re.escape(pkg), status, re.M
            ):
                bad.append("%s is not installed" % pkg)
        src = read("etc/apt/sources.list.d/openduo.sources") or ""
        if not re.search(r"^URIs: https://", src, re.M):
            bad.append("no https openduo.sources")
        if [n for n in members if n.startswith("boot/efi/") and n != "boot/efi/"]:
            bad.append("files under boot/efi/ in the root (they belong on the ESP)")

    with tarfile.open(esp) as t:
        names = [m.name.removeprefix("./") for m in t]
    entries = [n for n in names if re.match(r"^loader/entries/openduo-.*\.conf$", n)]
    if not entries:
        bad.append("no packaged kernel entry on the ESP")
    for n in entries:
        if "+" in os.path.basename(n):
            bad.append("the kernel entry is not blessed: %s" % n)
    if not [n for n in names if re.match(r"^openduo/[^/]+/Image$", n)]:
        bad.append("no packaged kernel Image on the ESP")

    for b in bad:
        print("release-audit: FAIL: %s" % b)
    if bad:
        return 1
    print("release-audit: ok (%d files, user %s, %s)" % (len(members), user, ", ".join(entries)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
