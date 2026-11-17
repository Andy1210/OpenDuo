"""scripts/lib/release-audit.py on small made-up images: python3 -m unittest
discover -s tests (from scripts/). The personal-looking values are put
together at run time."""

import io
import os
import subprocess
import sys
import tarfile
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
AUDIT = os.path.join(os.path.dirname(HERE), "lib", "release-audit.py")
USER = "duo"
WPA = "ctrl_interface=DIR=/run/wpa_supplicant GROUP=netdev\nupdate_config=1\n"

GOOD = {
    "etc/machine-id": "",
    "etc/duo2/gui-user": USER + "\n",
    "etc/duo-devmode.conf": "developer_mode = off\nusb_network = off\n",
    "etc/systemd/system/multi-user.target.wants/duo2-stock-import.service": "[Unit]\n",
    "home/duo2admin/.ssh/authorized_keys": "",
    "etc/ssh/authorized_keys.d/" + USER: "",
    "etc/shadow": "root:*:20000:0:99999:7:::\n%s:!:20000:0:99999:7:::\nduo2admin:*:20000::::::\n"
    % USER,
    "etc/group": "sudo:x:27:\nvideo:x:44:%s\n" % USER,
    "etc/sudoers.d/duo2admin": "duo2admin ALL=(ALL:ALL) NOPASSWD: ALL\n",
    "etc/sudoers.d/duo-shell": "%s ALL=(root) NOPASSWD: /usr/local/lib/duo-shell/duo-shell-helper\n"
    % USER,
    "var/lib/dpkg/status": "".join(
        "Package: %s\nStatus: install ok installed\nVersion: 1\n\n" % p
        for p in ("openduo-surface-duo2", "openduo-stock-surface-duo2", "openduo-archive-keyring")
    ),
    "etc/apt/sources.list.d/openduo.sources": "Types: deb\nURIs: https://example.invalid/apt\n",
    "usr/lib/firmware/regulatory.db": "debian",
    "etc/systemd/system/multi-user.target.wants/wpa_supplicant@wlan0.service": "[Unit]\n",
    "etc/wpa_supplicant/wpa_supplicant-wlan0.conf": (WPA, 0o600),
    "etc/systemd/system/wpa_supplicant@.service.d/duo2.conf": "[Service]\nUMask=0077\n",
}
ESP = {
    "openduo/7.3.4-duo2-gabc/Image": "kernel",
    "loader/entries/openduo-7.3.4-duo2-gabc.conf": "title x\n",
}


def tar(path, files, dirs=("boot/efi/",), links=None):
    with tarfile.open(path, "w") as t:
        for d in dirs:
            i = tarfile.TarInfo(d)
            i.type = tarfile.DIRTYPE
            t.addfile(i)
        for name, target in (links or {}).items():
            i = tarfile.TarInfo(name)
            i.type = tarfile.SYMTYPE
            i.linkname = target
            t.addfile(i)
        for name, text in files.items():
            mode = 0o644
            if isinstance(text, tuple):
                text, mode = text
            data = text.encode()
            i = tarfile.TarInfo(name)
            i.size = len(data)
            i.mode = mode
            t.addfile(i, io.BytesIO(data))


class Audit(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", self.d]))
        self.patterns = os.path.join(self.d, "patterns")
        with open(self.patterns, "w") as f:
            f.write("# test\n(?i)" + "secret" + "home" + "net\n")

    def audit(self, files=None, esp=None, links=None):
        root, esp_tar = os.path.join(self.d, "root.tar"), os.path.join(self.d, "esp.tar")
        tar(root, GOOD if files is None else files, links=links)
        tar(esp_tar, ESP if esp is None else esp, dirs=())
        env = dict(os.environ, DUO2_PERSONAL_DATA=self.patterns)
        return subprocess.run(
            [sys.executable, "-I", AUDIT, root, esp_tar, USER],
            capture_output=True,
            text=True,
            env=env,
        )

    def bad(self, change, want, esp=None):
        files = dict(GOOD)
        files.update(change)
        files = {k: v for k, v in files.items() if v is not None}
        r = self.audit(files, esp)
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn(want, r.stdout)

    def test_a_clean_image_passes(self):
        r = self.audit()
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_stock_files(self):
        self.bad({"usr/lib/firmware/qcom/sm8350/microsoft/adsp.mbn": "x"}, "stock file")
        self.bad({"opt/duo2-touch/lib64/libsurfacetouch_c1.so": "x"}, "stock file")
        self.bad(
            {"usr/share/qcom/sm8350/Microsoft/surfaceduo2/sensors/sns_reg.conf": "x"}, "stock file"
        )

    def test_keys(self):
        key = "ssh-ed25519 AAAA" + "C3NzaC1lZDI1NTE5AAAAIExampleExampleExample x"
        self.bad({"home/duo2admin/.ssh/authorized_keys": key}, "ssh key")
        self.bad({"usr/local/share/x.txt": key}, "ssh key")
        self.bad({"etc/ssh/ssh_host_ed25519_key": "k"}, "host key")
        self.bad({"etc/machine-id": "0123456789abcdef0123456789abcdef\n"}, "machine-id")
        self.bad(
            {"var/lib/dbus/machine-id": "0123456789abcdef0123456789abcdef\n"},
            "var/lib/dbus/machine-id holds an id",
        )

    def test_dbus_machine_id_as_a_link_passes(self):
        r = self.audit(links={"var/lib/dbus/machine-id": "/etc/machine-id"})
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_access(self):
        self.bad({"etc/duo-devmode.conf": "developer_mode = on\n"}, "developer mode")
        self.bad({"etc/systemd/system/multi-user.target.wants/ssh.service": "x"}, "sshd is enabled")
        self.bad({"etc/shadow": "root:$6$abc$def:20000::::::\n"}, "password is set")
        self.bad({"etc/group": "sudo:x:27:%s\n" % USER}, "group sudo")
        self.bad({"etc/sudoers.d/x": "%s ALL=(ALL) NOPASSWD: ALL\n" % USER}, "may run anything")
        self.bad({"etc/duo2/gui-user": "someone\n"}, "gui-user")

    def test_setup(self):
        self.bad(
            {"etc/systemd/system/multi-user.target.wants/duo2-stock-import.service": None},
            "not enabled",
        )
        self.bad({"var/lib/dpkg/status": ""}, "is not installed")
        self.bad({"boot/efi/loader/entries/duo2.conf": "x"}, "boot/efi")
        self.bad(
            {}, "not blessed", esp={"openduo/r/Image": "k", "loader/entries/openduo-r+3.conf": "x"}
        )

    def test_wifi(self):
        conf = "etc/wpa_supplicant/wpa_supplicant-wlan0.conf"
        self.bad(
            {"etc/systemd/system/multi-user.target.wants/wpa_supplicant@wlan0.service": None},
            "wpa_supplicant@wlan0.service is not enabled",
        )
        self.bad({conf: None}, "no " + conf)
        self.bad({conf: WPA}, "mode 0600")
        self.bad({conf: (WPA + 'network={\n\tssid="x"\n}\n', 0o600)}, "empty configuration")
        self.bad({"etc/systemd/system/wpa_supplicant@.service.d/duo2.conf": None}, "UMask=0077")

    def test_hint_texts_and_the_public_url_pass(self):
        files = dict(GOOD)
        files["usr/local/lib/x/page.py"] = 'hint = "ssh-ed25519 AAAA\u2026 name"\n'
        files["etc/apt/sources.list.d/openduo.sources"] = (
            "URIs: https://" + "secret" + "home" + "net.example.invalid/apt\n"
        )
        root, esp_tar = os.path.join(self.d, "root.tar"), os.path.join(self.d, "esp.tar")
        tar(root, files)
        tar(esp_tar, ESP, dirs=())
        env = dict(os.environ, DUO2_PERSONAL_DATA=self.patterns)
        allowed = "secret" + "home" + "net.example.invalid/apt"
        r = subprocess.run(
            [sys.executable, "-I", AUDIT, root, esp_tar, USER, allowed],
            capture_output=True,
            text=True,
            env=env,
        )
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_personal_patterns(self):
        self.bad({"etc/hosts.extra": "a " + "Secret" + "Home" + "Net" + " b\n"}, "personal pattern")


if __name__ == "__main__":
    unittest.main()
