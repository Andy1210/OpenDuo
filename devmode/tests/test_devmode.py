"""duo-devmode's parts: keys, settings, the commands against a stand-in
system, masking, the bug report, the USB gadget script on a stand-in
configfs.  python3 -m unittest discover -s tests  (from devmode/)"""

import base64
import importlib.machinery
import importlib.util
import io
import json
import os
import shutil
import struct
import subprocess
import sys
import tarfile
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOP = os.path.dirname(HERE)
REPO = os.path.dirname(TOP)
sys.path.insert(0, TOP)

from duodevmode import bugreport, conf, devtools, keys, redact  # noqa: E402


def load_tool():
    loader = importlib.machinery.SourceFileLoader("duo_devmode", os.path.join(TOP, "duo-devmode"))
    spec = importlib.util.spec_from_loader("duo_devmode", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


dm = load_tool()


def sshstr(b):
    return struct.pack(">I", len(b)) + b


def ed25519_line(seed=1, comment="dev@example.org"):
    blob = sshstr(b"ssh-ed25519") + sshstr(bytes([seed]) * 32)
    return "ssh-ed25519 %s %s" % (base64.b64encode(blob).decode(), comment)


def rsa_line(bits):
    n = b"\x00" + b"\xc5" + b"\x11" * (bits // 8 - 1)
    blob = sshstr(b"ssh-rsa") + sshstr(b"\x01\x00\x01") + sshstr(n)
    return "ssh-rsa %s" % base64.b64encode(blob).decode()


class Keys(unittest.TestCase):
    def test_parse(self):
        k = keys.parse_key(ed25519_line(comment="dev@example.org  laptop\t\x1b[31m"))
        self.assertEqual(k.kind, "ssh-ed25519")
        self.assertEqual(k.comment, "dev@example.org laptop [31m")
        self.assertTrue(keys.is_fingerprint(k.fingerprint))
        self.assertEqual(keys.parse_key(rsa_line(3072)).kind, "ssh-rsa")

    def test_refused(self):
        for line, why in (
            ("", "empty"),
            ('command="rm -rf /" ' + ed25519_line(), "options"),
            ("restrict " + ed25519_line(), "options"),
            ("ssh-dss AAAAB3NzaC1kc3M=", "known type"),
            ("ssh-ed25519 not*base64", "base64"),
            ("ssh-ed25519", "missing"),
            (rsa_line(1024), "2048"),
            ("ecdsa-sha2-nistp256 " + ed25519_line().split()[1], "not a ecdsa"),
        ):
            with self.assertRaises(keys.BadKey, msg=line) as c:
                keys.parse_key(line)
            self.assertIn(why, str(c.exception), line)

    def test_fingerprint_matches_ssh_keygen(self):
        if not shutil.which("ssh-keygen"):
            self.skipTest("no ssh-keygen")
        with tempfile.TemporaryDirectory() as d:
            subprocess.run(
                ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", d + "/k"], check=True
            )
            with open(d + "/k.pub") as f:
                line = f.read()
            want = subprocess.run(
                ["ssh-keygen", "-lf", d + "/k.pub"], capture_output=True, text=True, check=True
            ).stdout.split()[1]
        self.assertEqual(keys.parse_key(line).fingerprint, want)

    def test_input(self):
        ks, problems = keys.parse_input("# my keys\n\n" + ed25519_line(1) + "\n" + ed25519_line(2))
        self.assertEqual((len(ks), problems), (2, []))
        ks, problems = keys.parse_input(ed25519_line(1) + "\nhello\n")
        self.assertEqual(len(problems), 1)
        self.assertIn("line 2", problems[0])
        self.assertTrue(keys.parse_input("")[1])
        self.assertTrue(keys.parse_input("x" * (keys.MAX_INPUT + 1))[1])

    def test_add_list_remove(self):
        existing = 'from="192.0.2.0/24" ' + ed25519_line(7, "old") + "\n# a comment\n"
        listed = keys.listed(existing)
        self.assertEqual([k.comment for k in listed], ["old"])
        a, b = keys.parse_key(ed25519_line(1)), keys.parse_key(ed25519_line(7))
        text, added = keys.with_added(existing, [a, b])
        self.assertEqual(added, [a])  # b is the old one
        self.assertTrue(text.startswith(existing))
        self.assertEqual(len(keys.listed(text)), 2)
        text2, gone = keys.without(text, listed[0].fingerprint)
        self.assertEqual(gone, 1)
        self.assertIn("# a comment", text2)
        self.assertEqual([k.fingerprint for k in keys.listed(text2)], [a.fingerprint])
        self.assertEqual(keys.with_added(text2, [a]), (text2, []))
        many = [keys.parse_key(ed25519_line(i)) for i in range(40)]
        with self.assertRaises(keys.BadKey):
            keys.with_added("", many)

    def test_fingerprint_pattern(self):
        fp = keys.parse_key(ed25519_line()).fingerprint
        self.assertTrue(keys.is_fingerprint(fp))
        for bad in (
            "",
            "SHA256:short",
            fp + "x",
            "MD5:" + fp[7:],
            fp.replace("A", "-", 1) + "/../",
        ):
            self.assertFalse(keys.is_fingerprint(bad), bad)


class Settings(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(conf.parse(None), {"developer_mode": True, "usb_network": True})
        self.assertEqual(conf.parse(""), {"developer_mode": False, "usb_network": False})
        c = conf.parse("developer_mode = on # x\nusb_network=on\nusb_network = maybe\n")
        self.assertEqual(c, {"developer_mode": True, "usb_network": True})
        self.assertEqual(conf.parse(conf.render(c)), c)
        self.assertFalse(
            conf.effective({"developer_mode": False, "usb_network": True})["usb_network"]
        )

    def test_image_default_is_off(self):
        with open(os.path.join(TOP, "devmode.conf")) as f:
            self.assertEqual(conf.parse(f.read()), {"developer_mode": False, "usb_network": False})


class FakeSystem:
    """The phone as duo-devmode sees it."""

    def __init__(self, caller=(1000, 1000), answer=True, gadget_rc=0, conf_text=""):
        self.files = {"/etc/duo-devmode.conf": conf_text}
        self.conf_path = "/etc/duo-devmode.conf"
        self.keys = ""
        self.enabled = {"ssh.service": False, "ssh.socket": False}
        self.calls = []
        self.asked = []
        self._caller = caller
        self.answer = answer
        self.gadget_rc = gadget_rc
        self.touch = False
        self.synced = []

    def run(self, argv, input=None, timeout=60):
        self.calls.append(argv)
        if argv[:2] == ["systemctl", "is-enabled"]:
            return 0, "enabled\n" if self.enabled.get(argv[2]) else "disabled\n", ""
        if argv[:2] == ["systemctl", "is-active"]:
            return (0 if self.enabled["ssh.service"] else 3), "", ""
        if argv[:2] == ["systemctl", "enable"]:
            self.enabled[argv[-1]] = True
            return 0, "", ""
        if argv[:2] == ["systemctl", "disable"]:
            self.enabled[argv[-1]] = False
            return 0, "", ""
        if argv == [dm.USB_GADGET, "apply"]:
            return self.gadget_rc, "", ""
        if argv[:3] == ["ip", "-j", "addr"]:
            return 0, json.dumps(LINKS), ""
        return 1, "", "unexpected"

    def read(self, path):
        return self.files.get(path)

    def write_root_file(self, path, text, mode=0o644):
        self.files[path] = text

    def admin(self):
        class Pw:
            pw_uid = 1001

        return Pw()

    def read_keys(self):
        return self.keys

    def write_keys(self, text):
        self.keys = text

    def confirm(self, detail):
        self.asked.append(detail)
        return self.answer

    def caller(self):
        return self._caller

    def touch_log(self):
        return self.touch

    def set_touch_log(self, on):
        self.touch = on

    def devtools_sync(self, on):
        self.synced.append(on)
        return {"adopted": [], "installed": [], "removed": [], "missing": []}

    def devtools_state(self):
        return {"installed": 0, "listed": 0, "available": 0}


LINKS = [
    {"ifname": "lo", "addr_info": [{"family": "inet", "local": "127.0.0.1", "scope": "host"}]},
    {
        "ifname": "wlan0",
        "addr_info": [
            {"family": "inet", "local": "192.0.2.50", "scope": "global"},
            {"family": "inet6", "local": "2001:db8::50", "scope": "global"},
            {"family": "inet6", "local": "2001:db8::77", "scope": "global", "temporary": True},
            {"family": "inet6", "local": "fe80::50", "scope": "link"},
        ],
    },
    {"ifname": "usb0", "addr_info": [{"family": "inet", "local": "10.15.19.1", "scope": "global"}]},
    {"ifname": "ap0", "addr_info": [{"family": "inet", "local": "10.15.43.1", "scope": "global"}]},
]


def run_main(system, *argv, stdin=""):
    out = io.StringIO()
    old = sys.stdout
    sys.stdout = out
    try:
        code = dm.main(list(argv), system=system, stdin=io.StringIO(stdin))
    finally:
        sys.stdout = old
    return code, (json.loads(out.getvalue()) if out.getvalue() else None)


class Commands(unittest.TestCase):
    def test_status(self):
        s = FakeSystem(conf_text="developer_mode = on\n")
        s.keys = ed25519_line()
        code, st = run_main(s, "status")
        self.assertEqual(code, 0)
        self.assertEqual((st["developer_mode"], st["ssh"], st["usb_network"]), (True, False, False))
        self.assertEqual(st["account"], "duo2admin")
        self.assertEqual(len(st["keys"]), 1)
        self.assertEqual(
            [a["address"] for a in st["addresses"]],
            ["192.0.2.50", "2001:db8::50", "10.15.19.1", "fe80::50%wlan0"],
        )
        self.assertTrue(st["asks"])

    def test_ssh_needs_developer_mode_and_a_yes(self):
        s = FakeSystem()
        code, r = run_main(s, "ssh", "on")
        self.assertEqual((code, r["ok"]), (1, False))
        self.assertFalse(s.enabled["ssh.service"])
        s = FakeSystem(conf_text="developer_mode = on\n", answer=False)
        code, r = run_main(s, "ssh", "on")
        self.assertEqual((code, r["error"]), (4, "not confirmed"))
        self.assertEqual(len(s.asked), 1)
        self.assertFalse(s.enabled["ssh.service"])
        s.answer = True
        self.assertEqual(run_main(s, "ssh", "on")[0], 0)
        self.assertTrue(s.enabled["ssh.service"])
        self.assertEqual(run_main(s, "ssh", "off")[0], 0)
        self.assertFalse(s.enabled["ssh.service"])
        self.assertEqual(len(s.asked), 2)  # off asks nothing

    def test_root_and_admin_are_not_asked(self):
        for caller in ((0, 0), (1001, 1001)):
            s = FakeSystem(caller=caller, conf_text="developer_mode = on\n", answer=False)
            self.assertEqual(run_main(s, "ssh", "on")[0], 0)
            self.assertEqual(s.asked, [])

    def test_mode_off_turns_everything_off(self):
        s = FakeSystem(conf_text="developer_mode = on\nusb_network = on\n")
        s.enabled["ssh.service"] = s.enabled["ssh.socket"] = True
        code, _r = run_main(s, "mode", "off")
        self.assertEqual(code, 0)
        self.assertEqual(
            conf.parse(s.files[s.conf_path]), {"developer_mode": False, "usb_network": False}
        )
        self.assertFalse(s.enabled["ssh.service"] or s.enabled["ssh.socket"])
        self.assertIn([dm.USB_GADGET, "apply"], s.calls)
        code, _r = run_main(s, "mode", "on")
        self.assertEqual(conf.parse(s.files[s.conf_path])["developer_mode"], True)
        self.assertFalse(s.enabled["ssh.service"])  # on again: ssh stays off

    def test_mode_drives_the_devtools_and_the_touch_log(self):
        s = FakeSystem(conf_text="developer_mode = off\n")
        self.assertEqual(run_main(s, "mode", "on")[0], 0)
        self.assertEqual(s.synced, [True])
        s.touch = True
        self.assertEqual(run_main(s, "mode", "off")[0], 0)
        self.assertEqual(s.synced, [True, False])
        self.assertFalse(s.touch)
        code, r = run_main(s, "devtools", "sync")  # follows the switch, asks nothing
        self.assertEqual((code, r["developer_mode"], s.synced[-1]), (0, False, False))
        self.assertEqual(s.asked, [])

    def test_touch_log(self):
        s = FakeSystem(conf_text="developer_mode = off\n")
        code, r = run_main(s, "touchlog", "on")
        self.assertEqual((code, r["error"]), (1, "developer mode is off"))
        self.assertFalse(s.touch)
        s = FakeSystem(conf_text="developer_mode = on\n", answer=False)
        self.assertEqual(run_main(s, "touchlog", "on")[0], 4)
        self.assertFalse(s.touch)
        self.assertIn("PIN", s.asked[0])
        s.answer = True
        self.assertEqual(run_main(s, "touchlog", "on")[0], 0)
        self.assertTrue(s.touch)
        self.assertTrue(run_main(s, "status")[1]["touch_log"])
        self.assertEqual(run_main(s, "touchlog", "off")[0], 0)
        self.assertFalse(s.touch)
        self.assertEqual(len(s.asked), 2)  # off asks nothing
        # Developer mode off: the status never says on, whatever the file says
        s = FakeSystem(conf_text="developer_mode = off\n")
        s.touch = True
        self.assertFalse(run_main(s, "status")[1]["touch_log"])
        # root and the administrator are not asked
        s = FakeSystem(caller=(0, 0), conf_text="developer_mode = on\n", answer=False)
        self.assertEqual(run_main(s, "touchlog", "on")[0], 0)

    def test_usb(self):
        s = FakeSystem(conf_text="developer_mode = off\n")
        self.assertEqual(run_main(s, "usb", "on")[0], 1)
        s = FakeSystem(conf_text="developer_mode = on\n", gadget_rc=3)
        code, r = run_main(s, "usb", "on")
        self.assertEqual(code, 1)
        self.assertIn("tethering", r["error"])
        self.assertFalse(conf.parse(s.files[s.conf_path])["usb_network"])  # rolled back
        s.gadget_rc = 0
        self.assertEqual(run_main(s, "usb", "on")[0], 0)
        self.assertTrue(conf.parse(s.files[s.conf_path])["usb_network"])

    def test_keys(self):
        s = FakeSystem(answer=False)
        code, r = run_main(s, "key", "add", stdin=ed25519_line(3, "laptop"))
        self.assertEqual(code, 4)
        self.assertEqual(s.keys, "")
        self.assertIn("SHA256:", s.asked[0])
        self.assertIn("laptop", s.asked[0])
        s.answer = True
        code, r = run_main(s, "key", "add", stdin=ed25519_line(3, "laptop"))
        self.assertEqual(code, 0)
        self.assertEqual(len(keys.listed(s.keys)), 1)
        fp = r["added"][0]["fingerprint"]
        code, r = run_main(s, "key", "add", stdin=ed25519_line(3, "laptop"))
        self.assertEqual((code, r["added"]), (0, []))  # there already: nothing asked
        self.assertEqual(len(s.asked), 2)
        code, r = run_main(s, "key", "add", stdin='command="id" ' + ed25519_line(4))
        self.assertEqual(code, 1)
        self.assertEqual(run_main(s, "key", "remove", fp)[0], 0)
        self.assertEqual(keys.listed(s.keys), [])
        self.assertEqual(run_main(s, "key", "remove", fp)[0], 1)

    def test_usage(self):
        s = FakeSystem()
        for argv in (
            [],
            ["ssh"],
            ["ssh", "yes"],
            ["mode", "on", "x"],
            ["key", "remove", "../../etc/shadow"],
            ["key", "add", "/etc/shadow"],
            ["bugreport", "/tmp"],
            ["touchlog", "yes"],
            ["touchlog"],
            ["devtools"],
            ["devtools", "sync", "x"],
            ["devtools", "install"],
            ["frobnicate"],
        ):
            err = io.StringIO()
            old = sys.stderr
            sys.stderr = err
            try:
                self.assertEqual(run_main(s, *argv)[0], 2, argv)
            finally:
                sys.stderr = old
        self.assertEqual(s.calls, [])

    def test_bugreport_asks(self):
        s = FakeSystem(answer=False)
        self.assertEqual(run_main(s, "bugreport")[0], 4)

    def test_write_as_self(self):
        with tempfile.TemporaryDirectory() as d:
            path = dm.write_as(os.getuid(), os.getgid(), d, "x.tar.gz", b"data")
            self.assertEqual(path, os.path.join(d, "x.tar.gz"))
            with open(path, "rb") as f:
                self.assertEqual(f.read(), b"data")
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
            with self.assertRaises(OSError):  # never over an existing file
                dm.write_as(os.getuid(), os.getgid(), d, "x.tar.gz", b"other")
            os.symlink("/nonexistent", os.path.join(d, "y.tar.gz"))
            with self.assertRaises(OSError):
                dm.write_as(os.getuid(), os.getgid(), d, "y.tar.gz", b"other")


class Masking(unittest.TestCase):
    def setUp(self):
        self.r = redact.Redactor(literals=["Kitchen Net", "duo", "Jane's Buds", "R5CT12AB34"])

    def test_lines(self):
        m = self.r.mask
        self.assertEqual(
            m("wlan0: Trying to associate with SSID 'Corner Cafe 5G'"),
            "wlan0: Trying to associate with SSID '[ssid-1]'",
        )
        self.assertNotIn("Kitchen Net", m("joined Kitchen Net at last"))
        self.assertIn("duo-wc", m("duo-wc started for duo"))  # generic names stay
        self.assertNotIn("Jane's Buds", m("bluetoothd: Jane's Buds connected"))
        self.assertNotIn("R5CT12AB34", m("serial R5CT12AB34"))
        dhcp = "DHCPv4 address 192.168.178.37/24 via 192.168.178.1"  # personal-data: ok
        line = m(dhcp + ", dns 9.9.9.9")
        for v in ("192.168.178.37", "192.168.178.1", "9.9.9.9"):  # personal-data: ok
            self.assertNotIn(v, line)
        line = m(
            "bearer: 2a00:1450:4001::5 and fe80::1c2a:3bff:fe4d:5e6f%wlan0, lo ::1"
        )  # personal-data: ok
        self.assertNotIn("2a00:1450", line)  # personal-data: ok
        self.assertNotIn("fe4d:5e6f", line)
        self.assertIn("::1", line)
        kept = "usb0 10.15.19.1, waydroid 192.168.240.112, loopback 127.0.0.1"
        self.assertEqual(m(kept), kept)
        self.assertNotIn("51.477811", m("geoclue: new location lat 51.477811 lon -0.001475"))
        self.assertIn("12.345678", m("[   12.345678] usb 1-1: new high-speed USB device"))
        self.assertNotIn("30 987 6543", m("incoming call from 06 30 987 6543"))
        line = m("account jane.doe@mailbox.org and /home/janedoe/x")  # personal-data: ok
        self.assertNotIn("jane.doe", line)
        self.assertNotIn("janedoe", m("/home/janedoe/x"))  # personal-data: ok

    def test_device_numbers_and_macs(self):
        line = self.r.mask("IMEI 490154203237518 on 5e:a1:3c:7b:2f:91")  # personal-data: ok
        self.assertNotIn("490154203237518", line)  # personal-data: ok
        self.assertNotIn("5e:a1:3c:7b:2f:91", line)  # personal-data: ok

    def test_same_value_same_placeholder(self):
        a = self.r.mask("from 9.9.9.9")  # personal-data: ok
        b = self.r.mask("to 9.9.9.9 and 9.9.9.10")  # personal-data: ok
        self.assertEqual(a.split()[-1], b.split()[1])
        self.assertNotEqual(b.split()[1], b.split()[-1])


class TouchPositions(unittest.TestCase):
    def test_dropped(self):
        text = (
            "[ 12.0] duo2-touchd[812]: touch down id=3 region=1 panel=right raw=120,4400 "
            "local=60,2200 geom=split xoff=0\n"
            "[ 12.1] duo2-touchd[812]: touch up id=3 region=1 panel=right down=120,4400 raw x 1..2\n"
            "[ 12.2] duo2-touchd[812]: the firmware's own pen report (0x01): bits 0x01 x 4000 y 300 "
            "pressure 0 tilt 0,0 (16 bytes)\n"
            "[ 12.3] duo2-touchd[812]: scripted tap r 0.50 0.50\n"
            "[ 12.4] duo2-touchd[812]: contact log on\n"
            "[ 12.5] kernel: usb 1-1: new device at raw=3\n"
        )
        kept, n = bugreport.drop_touch_positions(text)
        self.assertEqual(n, 4)
        self.assertIn("contact log on", kept)
        self.assertIn("kernel: usb", kept)
        self.assertNotIn("120,4400", kept)

    def test_collect_leaves_them_out(self):
        line = "duo2-touchd[1]: touch down id=1 region=1 panel=left raw=5,6 local=7,8\n"
        texts = bugreport.collect(
            redact.Redactor(),
            generic=[("journal-this-boot.txt", [["journalctl"]])],
            device=[],
            state="{}",
            runner=lambda argv: line + "ok\n",
        )
        self.assertNotIn("raw=5,6", texts["journal-this-boot.txt"])
        self.assertIn("Touch positions left out: 1 lines", texts["README.txt"])


class BugReport(unittest.TestCase):
    def test_device_conf(self):
        with open(os.path.join(TOP, "bugreport.d", "surface-duo2.conf")) as f:
            items = bugreport.parse_device_conf(f.read())
        self.assertIn(("build.txt", ("file", "/etc/duo2-release")), items)
        self.assertIn(("modem.txt", ("run", ["mmcli", "-m", "any"])), items)
        bad = bugreport.parse_device_conf(
            "../x: id\n.hidden: id\nok.txt < relative/path\nfine.txt: echo 'a b'\nnoname: \n"
        )
        self.assertEqual(bad, [("fine.txt", ("run", ["echo", "a b"]))])

    def test_scrub_state(self):
        s = bugreport.scrub_state(
            {"windows": [{"app_id": "a", "title": "Secret doc"}], "title": "x"}
        )
        self.assertEqual(s, {"windows": [{"app_id": "a", "title": "[title]"}], "title": "[title]"})

    def test_collect_and_pack(self):
        r = redact.Redactor(literals=["Kitchen Net"])

        def runner(argv):
            return "$ %s\nconnected to Kitchen Net from 9.9.9.9\n" % " ".join(
                argv
            )  # personal-data: ok

        texts = bugreport.collect(
            r,
            generic=[("a.txt", [["one"], ["two"]])],
            device=[("b.txt", ("run", ["three"])), ("b.txt", ("file", "/x"))],
            state='{"title": "kept out"}\n',
            runner=runner,
            reader=lambda path: "file %s with 5e:a1:3c:7b:2f:91\n" % path,  # personal-data: ok
        )
        self.assertEqual(sorted(texts), ["README.txt", "a.txt", "b.txt", "duo-wc-state.json"])
        self.assertEqual(texts["a.txt"].count("$ "), 2)
        for t in texts.values():
            self.assertNotIn("Kitchen Net", t)
            self.assertNotIn("9.9.9.9", t)  # personal-data: ok
            self.assertNotIn("5e:a1:3c:7b:2f:91", t)  # personal-data: ok
        self.assertIn("1 mac", texts["README.txt"])
        name = bugreport.archive_name(0)
        self.assertTrue(name.startswith("openduo-bugreport-") and name.endswith(".tar.gz"))
        data = bugreport.pack(texts, "top")
        with tarfile.open(fileobj=io.BytesIO(data)) as tar:
            self.assertEqual(
                sorted(tar.getnames()),
                ["top/README.txt", "top/a.txt", "top/b.txt", "top/duo-wc-state.json"],
            )


GADGET = os.path.join(REPO, "rootfs", "overlay", "usr", "local", "sbin", "duo2-usb-gadget")


class DevTools(unittest.TestCase):
    LIST = """# tools
/usr/local/sbin/duo2-tone
/usr/local/lib/duo-wc/tools/touch.py   # a comment
/usr/local/sbin/duo2-tone
/etc/shadow
/usr/local/sbin/../../etc/passwd
/usr/local/lib/openduo-devtools/files/x
/usr/local/bin/
relative/path
/usr/local/bin/a b
/usr/local/bin/.hidden
/usr/local/bin/duo2-pose-check
"""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root)

    def at(self, path):
        return os.path.join(self.root, path.lstrip("/"))

    def put(self, path, data=b"#!/bin/sh\n", mode=0o755):
        os.makedirs(os.path.dirname(self.at(path)), exist_ok=True)
        with open(self.at(path), "wb") as f:
            f.write(data)
        os.chmod(self.at(path), mode)

    def sync(self, on):
        return devtools.sync(on, root=self.root, list_text=self.LIST)

    def test_list(self):
        self.assertEqual(
            devtools.parse_list(self.LIST),
            [
                "/usr/local/sbin/duo2-tone",
                "/usr/local/lib/duo-wc/tools/touch.py",
                "/usr/local/bin/duo2-pose-check",
            ],
        )
        # what install-files.sh installs: devtools.list and the lists beside it
        shipped = ""
        for name in ["devtools.list"] + sorted(
            n for n in os.listdir(TOP) if n.startswith("devtools-") and n.endswith(".list")
        ):
            with open(os.path.join(TOP, name)) as f:
                shipped += f.read()
        paths = devtools.parse_list(shipped)
        listed = [
            line.split("#", 1)[0].strip()
            for line in shipped.splitlines()
            if line.split("#", 1)[0].strip()
        ]
        self.assertEqual(paths, listed)  # every line of the shipped list is valid
        self.assertIn("/usr/local/sbin/duo2-tone", paths)

    def test_adopt_remove_install(self):
        self.put("/usr/local/sbin/duo2-tone", b"tone 1\n", 0o755)
        self.put("/usr/local/lib/duo-wc/tools/touch.py", b"touch\n", 0o750)
        r = self.sync(False)
        self.assertEqual(
            sorted(r["adopted"]),
            ["/usr/local/lib/duo-wc/tools/touch.py", "/usr/local/sbin/duo2-tone"],
        )
        self.assertEqual(sorted(r["removed"]), sorted(r["adopted"]))
        self.assertFalse(os.path.exists(self.at("/usr/local/sbin/duo2-tone")))
        store = self.at(devtools.PAYLOAD)
        self.assertEqual(os.stat(store).st_mode & 0o777, 0o700)
        self.assertEqual(devtools.state(root=self.root, list_text=self.LIST)["installed"], 0)
        # on: installed again, the same bytes and mode
        r = self.sync(True)
        self.assertEqual(
            sorted(r["installed"]),
            ["/usr/local/lib/duo-wc/tools/touch.py", "/usr/local/sbin/duo2-tone"],
        )
        self.assertEqual(r["missing"], ["/usr/local/bin/duo2-pose-check"])
        with open(self.at("/usr/local/sbin/duo2-tone"), "rb") as f:
            self.assertEqual(f.read(), b"tone 1\n")
        self.assertEqual(
            os.stat(self.at("/usr/local/lib/duo-wc/tools/touch.py")).st_mode & 0o777, 0o750
        )
        self.assertEqual(self.sync(True)["installed"], [])  # nothing to do
        # a newer install of a tool is adopted, then removed with the switch off
        self.put("/usr/local/sbin/duo2-tone", b"tone 2\n", 0o755)
        r = self.sync(False)
        self.assertEqual(r["adopted"], ["/usr/local/sbin/duo2-tone"])
        self.sync(True)
        with open(self.at("/usr/local/sbin/duo2-tone"), "rb") as f:
            self.assertEqual(f.read(), b"tone 2\n")
        self.assertEqual(
            devtools.state(root=self.root, list_text=self.LIST),
            {"installed": 2, "listed": 3, "available": 2},
        )

    def test_links_and_directories_are_not_ours(self):
        os.makedirs(self.at("/usr/local/sbin"))
        os.makedirs(self.at("/etc"))
        self.put("/etc/precious", b"keep\n", 0o644)
        os.symlink(self.at("/etc/precious"), self.at("/usr/local/sbin/duo2-tone"))
        os.makedirs(self.at("/usr/local/bin/duo2-pose-check"))
        r = self.sync(False)
        self.assertEqual((r["adopted"], r["removed"]), ([], []))
        self.assertTrue(os.path.islink(self.at("/usr/local/sbin/duo2-tone")))
        self.assertTrue(os.path.isdir(self.at("/usr/local/bin/duo2-pose-check")))
        # a payload copy does not go over a link either
        self.put("/usr/local/lib/openduo-devtools/files/usr/local/sbin/duo2-tone", b"t\n")
        self.assertEqual(self.sync(True)["installed"], [])
        with open(self.at("/etc/precious"), "rb") as f:
            self.assertEqual(f.read(), b"keep\n")

    def test_shipped_unit(self):
        with open(os.path.join(TOP, "duo-devmode-sync.service")) as f:
            unit = f.read()
        self.assertIn("ExecStart=/usr/local/sbin/duo-devmode devtools sync", unit)


class Gadget(unittest.TestCase):
    """The gadget script's `apply` on a directory standing in for configfs."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.g = os.path.join(self.d, "duo2")
        os.makedirs(os.path.join(self.g, "configs", "c.1"))
        os.makedirs(os.path.join(self.g, "functions", "acm.GS0"))
        os.symlink(
            os.path.join(self.g, "functions", "acm.GS0"),
            os.path.join(self.g, "configs", "c.1", "acm.GS0"),
        )
        with open(os.path.join(self.g, "UDC"), "w") as f:
            f.write("a600000.usb\n")
        self.conf = os.path.join(self.d, "devmode.conf")

    def tearDown(self):
        shutil.rmtree(self.d)

    def apply(self, conf_text):
        if conf_text is None:
            if os.path.exists(self.conf):
                os.unlink(self.conf)
        else:
            with open(self.conf, "w") as f:
                f.write(conf_text)
        env = dict(os.environ, DUO2_GADGET=self.g, DUO_DEVMODE_CONF=self.conf)
        return subprocess.run(["sh", GADGET, "apply"], env=env, capture_output=True, text=True)

    def usb0(self):
        return os.path.islink(os.path.join(self.g, "configs", "c.1", "ncm.usb0"))

    def test_on_off(self):
        self.assertEqual(self.apply("developer_mode = on\nusb_network = on\n").returncode, 0)
        self.assertTrue(self.usb0())
        with open(os.path.join(self.g, "UDC")) as f:
            self.assertEqual(f.read(), "a600000.usb\n")  # bound again
        self.assertEqual(self.apply("developer_mode = off\nusb_network = on\n").returncode, 0)
        self.assertFalse(self.usb0())
        self.assertEqual(self.apply(None).returncode, 0)  # no file: on, as before
        self.assertTrue(self.usb0())
        self.assertEqual(self.apply("usb_network = on\n").returncode, 0)
        self.assertFalse(self.usb0())

    def test_tethering_blocks_a_change(self):
        os.makedirs(os.path.join(self.g, "functions", "ncm.tether"))
        os.symlink(
            os.path.join(self.g, "functions", "ncm.tether"),
            os.path.join(self.g, "configs", "c.1", "ncm.tether"),
        )
        r = self.apply("developer_mode = on\nusb_network = on\n")
        self.assertEqual(r.returncode, 3)
        self.assertFalse(self.usb0())

    def test_image_conf_is_read(self):
        with open(os.path.join(TOP, "devmode.conf")) as f:
            self.assertEqual(self.apply(f.read()).returncode, 0)
        self.assertFalse(self.usb0())


if __name__ == "__main__":
    unittest.main()
