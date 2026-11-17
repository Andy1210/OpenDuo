"""check-personal-data's patterns: python3 -m unittest discover -s tests
(from scripts/).

The personal-looking values are put together at run time, so this file
itself stays clean under `check-personal-data --all`."""

import importlib.machinery
import importlib.util
import os
import re
import subprocess
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))


def load(name):
    path = os.path.join(os.path.dirname(HERE), name)
    loader = importlib.machinery.SourceFileLoader(name.replace("-", "_"), path)
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


cpd = load("check-personal-data")
AT = "@"
DOT = "."


def with_luhn(body):
    """body + the Luhn check digit (an IMEI's last digit)."""
    for d in "0123456789":
        if cpd.luhn_ok(body + d):
            return body + d
    raise AssertionError(body)


def ip(*parts):
    return DOT.join(str(p) for p in parts)


def mac(*octets):
    return ":".join("%02x" % o for o in octets)


MAC1 = mac(0xA0, 0x4A, 0x5E, 0x3C, 0x91, 0x07)


class Base(unittest.TestCase):
    def setUp(self):
        self.rules = cpd.Rules()

    def cats(self, line, path="x.md", rules=None):
        return [c for c, _t in (rules or self.rules).scan_line(line, path)]


class Mask(unittest.TestCase):
    def test_mask(self):
        self.assertEqual(cpd.mask("0123456789"), "01…89")
        self.assertEqual(cpd.mask("abc"), "a…")
        self.assertEqual(cpd.mask("  abcdefgh "), "ab…gh")


class Device(Base):
    def test_imei(self):
        imei = with_luhn("35" + "290611" + "736401")
        self.assertEqual(len(imei), 15)
        self.assertEqual(self.cats("IMEI: %s" % imei), ["device"])
        self.assertEqual(self.cats('"imei": "%s"' % imei), ["device"])
        spaced = "%s-%s-%s-%s" % (imei[:2], imei[2:8], imei[8:14], imei[14:])
        self.assertEqual(self.cats(spaced), ["device"])

    def test_iccid_and_eid(self):
        self.assertEqual(
            self.cats("ICCID " + "89" + "4920" + "3175" + "2604" + "8125" + "3"), ["device"]
        )
        self.assertEqual(
            self.cats(
                "EID " + "89" + "049032" + "0071" + "8435" + "2961" + "7340" + "5918" + "2671"
            ),
            ["device"],
        )

    def test_placeholders(self):
        for text in (
            "0" * 15,
            "0" * 14 + "1",
            "8900" + "0" * 15 + "1",
            "89" + "0" * 30,
            "123456789012345",  # a run
            "2026" + "1106" + "2359" + "59123",  # a time stamp, 15 digits
            "1" * 16,
        ):
            self.assertEqual(self.cats("x %s y" % text), [], text)

    def test_not_inside_hex_or_words(self):
        imei = with_luhn("35" + "290611" + "736401")
        self.assertEqual(self.cats("sha a" + imei + "b"), [])
        self.assertEqual(self.cats("v_" + imei), [])
        self.assertEqual(self.cats("12345678901234"), [])  # 14 digits


class Phone(Base):
    def test_phone(self):
        self.assertEqual(self.cats("call +44 7911 " + "36" + "2845"), ["phone"])
        self.assertEqual(self.cats("+3620" + "93" + "84752"), ["phone"])

    def test_placeholders(self):
        for text in (
            "+49 170 1234567",
            "+44 20 7946 0000",
            "+1 555 1111 222",
            "+44 7700 900-311",
            "+1 212 555 0142",
            "+44 20 7946 0817",
        ):
            self.assertEqual(self.cats(text), [], text)
        self.assertEqual(self.cats("version 1.2+3"), [])


class Mac(Base):
    def test_real_looking(self):
        self.assertEqual(self.cats("wlan0 " + MAC1), ["mac"])
        self.assertEqual(self.cats("random " + mac(0x92, 0x1F, 0x8D, 0xBA, 0x21, 0x57)), ["mac"])
        self.assertEqual(self.cats(MAC1.replace(":", "-")), ["mac"])

    def test_placeholders(self):
        for m in (
            "00:11:22:33:44:55",
            "aa:bb:cc:dd:ee:01",
            "02:00:00:00:00:01",
            "ff:ff:ff:ff:ff:ff",
            "02:00:5e:10:00:01",
            "00:00:00:00:5a:ad",
            "00:03:7f:12:20:56",
            "12:34:56:78:9a:bc",
            "a0:4a:5e:xx:xx:xx",
        ):
            self.assertEqual(self.cats("addr %s" % m), [], m)

    def test_not_ipv6_or_longer(self):
        self.assertEqual(self.cats("2001:db8:1:2:aaaa:bbbb:cccc:dddd"), [])
        self.assertEqual(self.cats(MAC1 + ":11"), [])


class Ip(Base):
    def test_private(self):
        self.assertEqual(self.cats("ssh admin" + AT + ip(192, 168, 1, 77)), ["ip"])
        self.assertEqual(self.cats("route via " + ip(10, 0, 0, 1)), ["ip"])
        self.assertEqual(self.cats(ip(172, 16, 4, 2) + "/24"), ["ip"])

    def test_allowed(self):
        for a in (
            ip(10, 15, 19, 1),
            ip(10, 15, 43, 12),
            ip(192, 168, 240, 1),
            ip(172, 20, 44, 0),
            "192.0.2.50",
            "198.51.100.7",
            "203.0.113.1",
            "8.8.8.8",
            "127.0.0.1",
            "100.64.1.2",
            ip(172, 32, 0, 1),
        ):
            self.assertEqual(self.cats("addr %s" % a), [], a)

    def test_not_versions(self):
        self.assertEqual(self.cats("kernel " + ip(10, 1, 2, 3) + ".4"), [])
        self.assertEqual(self.cats("1" + ip(10, 1, 2, 3)), [])
        self.assertEqual(self.cats("999.168.1.1"), [])


class Home(Base):
    def test_home(self):
        self.assertEqual(self.cats("cd /home/" + "alice" + "/src"), ["home"])
        self.assertEqual(self.cats("/home/" + "bob42/.cache/x.lock"), ["home"])

    def test_placeholders(self):
        for text in (
            "/home/user/x",
            "/home/$USER/x",
            "/home/*/.cache/",
            "/home/%s",
            "/home/<user>/",
            "/home/duo2admin/.ssh",
            "/home/x/.local",
            "/home/u/mine.png",
            "/home/USER/x",
        ):
            self.assertEqual(self.cats(text), [], text)


class Email(Base):
    def test_email(self):
        self.assertEqual(self.cats("mail me: alice" + AT + "gmail" + DOT + "com"), ["email"])

    def test_not_email(self):
        for text in (
            "noreply" + AT + "anthropic.com",
            "x" + AT + "example.org",
            "a" + AT + "b.example",
            "getty" + AT + "tty1.service",
            "serial-getty" + AT + "ttyGS0.service.d/",
            "backup" + AT + "nas.lan:",
            "alice" + AT + "duo2",
            "https://lore.kernel.org/all/2023060114-1-x" + AT + "quicinc.com/T/",
        ):
            self.assertEqual(self.cats(text), [], text)

    def test_authorship(self):
        line = "From: Alice Doe <alice" + AT + "gmail.com>"
        self.assertEqual(self.cats(line, "kernel/patches/0001-x.patch"), [])
        self.assertEqual(self.cats("Signed-off-by: A <a" + AT + "kernel.org>", "a.patch"), [])
        self.assertEqual(self.cats("+  - Bjorn A <b" + AT + "kernel.org>", "a.patch"), [])
        self.assertEqual(self.cats(line, "docs/x.md"), ["email"])
        self.assertEqual(self.cats(" * Copyright (c) 2024 A <a" + AT + "linaro.org>", "x.c"), [])


class Secret(Base):
    def test_secrets(self):
        self.assertEqual(self.cats("-----BEGIN OPENSSH " + "PRIVATE KEY-----"), ["secret"])
        self.assertEqual(self.cats("-----BEGIN " + "PRIVATE KEY-----"), ["secret"])
        key = "ssh-ed25519 " + "AAAA" + "C3NzaC1lZDI1NTE5AAAAI" + "Kq" * 20 + " a@b"
        self.assertIn("secret", self.cats(key))
        self.assertEqual(self.cats("psk=" + "3f" * 32), ["secret"])
        self.assertEqual(self.cats("gh" + "p_" + "A1b2" * 9), ["secret"])

    def test_not_secrets(self):
        self.assertEqual(
            self.cats('line = authorized_keys_line("ssh-ed25519 AAAAC3x duo2-backup@duo2")'), []
        )
        self.assertEqual(self.cats("psk=%s"), [])
        self.assertEqual(self.cats('psk="$P"'), [])


class Local(Base):
    def test_local_and_allow(self):
        rules = cpd.Rules(local=[r"(?i)\bmyhost\b", r"MyNas"], allow=[r"^Copyright "])
        self.assertEqual(self.cats("built on MyHost tonight", rules=rules), ["local"])
        self.assertEqual(self.cats("/mnt/MyNas/backups", rules=rules), ["local"])
        self.assertEqual(self.cats("Copyright 2026 myhost", rules=rules), [])
        self.assertEqual(self.cats("From: myhost <x>", "a.patch", rules=rules), [])

    def test_skip_mark(self):
        line = "ssh admin" + AT + ip(192, 168, 1, 77) + "   # personal-data: ok"
        self.assertEqual(self.cats(line), [])

    def test_load(self):
        with tempfile.TemporaryDirectory() as d:
            lst = os.path.join(d, "list")
            env = os.path.join(d, "env")
            with open(lst, "w") as f:
                f.write("# comment\n\nsecretplace\n!^ok \n")
            with open(env, "w") as f:
                f.write(
                    "DUO2_HOST=duo2admin" + AT + ip(10, 9, 8, 7) + "\nDUO2_USER=someone\n"
                    "DUO2_MAC=" + MAC1.upper() + "\n#DUO2_MAC=x\n"
                )
            rules = cpd.Rules.load(lst, env)
            self.assertEqual(len(rules.local), 3)
            self.assertEqual(self.cats("at secretplace", rules=rules), ["local"])
            self.assertEqual(self.cats("ok secretplace", rules=rules), [])
            self.assertIn("local", self.cats("phone at " + ip(10, 9, 8, 7), rules=rules))
            self.assertEqual(self.cats("phone at " + ip(10, 9, 8, 71) + ".5", rules=rules), [])
            self.assertIn("local", self.cats("mac " + MAC1, rules=rules))
            with open(lst, "w") as f:
                f.write("broken(\n")
            with self.assertRaises(re.error):
                cpd.Rules.load(lst, env)

    def test_env_examples_ignored(self):
        with tempfile.TemporaryDirectory() as d:
            env = os.path.join(d, "env")
            with open(env, "w") as f:
                f.write(
                    "DUO2_HOST=duo2admin@192.0.2.50\nDUO2_MAC=00:11:22:33:44:55\n"
                    'DUO2_SSH_OPTS="-o UserKnownHostsFile=$R/out/known_hosts"\n'
                )
            self.assertEqual(cpd.local_env_patterns(env), [])
            self.assertEqual(cpd.local_env_patterns(os.path.join(d, "missing")), [])


class Diff(unittest.TestCase):
    def test_added_lines(self):
        diff = (
            "commit " + "a" * 40 + "\n"
            "diff --git a/x.md b/x.md\n--- a/x.md\n+++ b/x.md\n"
            "@@ -3,0 +4,2 @@\n+one\n+two\n"
            "@@ -9 +11 @@\n-old\n+three\n"
            "diff --git a/gone b/gone\n--- a/gone\n+++ /dev/null\n@@ -1 +0,0 @@\n-x\n"
        )
        self.assertEqual(
            cpd.added_lines(diff),
            [
                ("x.md", 4, "one", "a" * 12),
                ("x.md", 5, "two", "a" * 12),
                ("x.md", 11, "three", "a" * 12),
            ],
        )


class Files(unittest.TestCase):
    def test_file_lines_and_blobs(self):
        with tempfile.TemporaryDirectory() as d:
            text = os.path.join(d, "a.txt")
            blob = os.path.join(d, "b.bin")
            with open(text, "w") as f:
                f.write("one\ntwo\n")
            with open(blob, "wb") as f:
                f.write(b"\x7fELF\0\0\1")
            os.symlink(text, os.path.join(d, "link"))
            lines, blobs = cpd.file_lines(
                [text, blob, os.path.join(d, "link"), os.path.join(d, "missing")]
            )
            self.assertEqual([(n, t) for _p, n, t, _c in lines], [(1, "one"), (2, "two")])
            self.assertEqual(blobs, [(blob, 7, True)])

    def test_report(self):
        import io

        out = io.StringIO()
        n = cpd.report(
            cpd.Rules(local=["needle"]),
            [("a.md", 3, "a needle here", ""), ("a.md", 4, "a needle here", "")],
            [("b.bin", 9, True)],
            dedupe=True,
            out=out,
        )
        self.assertEqual(n, 2)
        self.assertEqual(out.getvalue(), "a.md:3: local: n…\nb.bin: blob: binary, 9 bytes\n")


class Staged(unittest.TestCase):
    """The hook's mode, in a throwaway repository."""

    def test_staged(self):
        with tempfile.TemporaryDirectory() as d:

            def git(*a):
                subprocess.run(
                    ["git", "-C", d] + list(a),
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )

            git("init", "-q")
            git("config", "user.email", "t@example.org")
            git("config", "user.name", "t")
            with open(os.path.join(d, "a.md"), "w") as f:
                f.write("clean\n")
            git("add", "a.md")
            git("commit", "-qm", "a")
            with open(os.path.join(d, "a.md"), "w") as f:
                f.write("clean\nssh admin" + AT + ip(192, 168, 1, 77) + "\n")
            with open(os.path.join(d, "b.bin"), "wb") as f:
                f.write(b"\0\1\2")
            git("add", "a.md", "b.bin")
            old = cpd.ROOT
            cpd.ROOT = d
            try:
                lines, blobs = cpd.staged()
            finally:
                cpd.ROOT = old
            self.assertEqual([(p, n) for p, n, _t, _c in lines], [("a.md", 2)])
            self.assertEqual(blobs, [("b.bin", 3, True)])


if __name__ == "__main__":
    unittest.main()
