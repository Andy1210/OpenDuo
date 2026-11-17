"""The package builder's pure parts: the versions, the manifest's rules, the
control files and maintainer scripts. python3 -m unittest discover -s tests
(from packaging/)."""

import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, os.path.join(HERE, "..", "..", "maint"))

from duo2maint import debver  # noqa: E402
from duo2maint import updates as up  # noqa: E402

from openduo_pkg import build  # noqa: E402
from openduo_pkg import version as v  # noqa: E402

REPO = os.path.abspath(os.path.join(HERE, "..", ".."))


def text(rel):
    with open(os.path.join(REPO, rel)) as f:
        return f.read()


T0 = 1794872280  # 2026-11-16 23:38:00 UTC


class Versions(unittest.TestCase):
    def test_form(self):
        self.assertEqual(
            v.deb_version("0.1.0-dev\n", T0, "0e1a4ac"), "0.1.0~dev+20261116233800.g0e1a4ac"
        )
        self.assertEqual(v.deb_version("0.1.0", T0, "0e1a4ac"), "0.1.0+20261116233800.g0e1a4ac")
        self.assertEqual(
            v.deb_version("0.1.0-dev", T0, "0e1a4ac", dirty=True),
            "0.1.0~dev+20261116233800.g0e1a4ac.dirty",
        )
        for bad in ("", "0.1", "v0.1.0", "0.1.0 dev", "0.1.0-"):
            with self.assertRaises(ValueError):
                v.deb_version(bad, T0, "0e1a4ac")
        with self.assertRaises(ValueError):
            v.deb_version("0.1.0", T0, "xyz")
        self.assertEqual(v.os_version("0.1.0~dev+20261116233800.g0e1a4ac"), "0.1.0-dev")
        self.assertFalse(v.publishable("0.1.0~dev+20261116233800.g0e1a4ac.dirty"))

    def test_order(self):
        """What apt will do with them: dpkg's rules (maint/duo2maint/debver.py)."""
        dev1 = v.deb_version("0.1.0-dev", T0, "fffffff")
        dev2 = v.deb_version("0.1.0-dev", T0 + 1, "0000000")  # one second later, a "smaller" hash
        rel = v.deb_version(
            "0.1.0", T0 - 86400, "abcdef1"
        )  # the release, even from an older commit
        nxt = v.deb_version("0.1.1-dev", T0 - 2 * 86400, "abcdef1")
        rc = v.deb_version("0.2.0-rc1", T0, "abcdef1")
        final = v.deb_version("0.2.0", T0, "abcdef1")
        chain = [dev1, dev2, rel, nxt, rc, final]
        for a, b in zip(chain, chain[1:]):
            self.assertLess(debver.compare(a, b), 0, (a, b))

    def test_kernel(self):
        self.assertEqual(
            v.kernel_release_parts("7.3.4-duo2-g3ba7db140"), ("7.3.4", "3ba7db140", False)
        )
        self.assertEqual(v.kernel_release_parts("7.3.4-duo2-g3ba7db140-dirty")[2], True)
        with self.assertRaises(ValueError):
            v.kernel_release_parts("7.3.4")
        a = v.kernel_version("7.3.4-duo2-g3ba7db140", T0)
        self.assertEqual(a, "7.3.4+20261116233800.g3ba7db140")
        b = v.kernel_version("7.3.4-duo2-g0000000aa", T0 + 60)
        c = v.kernel_version("7.3.5-duo2-g0000000aa", T0 - 3600)
        self.assertLess(debver.compare(a, b), 0)
        self.assertLess(debver.compare(b, c), 0)


class Manifest(unittest.TestCase):
    def setUp(self):
        self.pkgs = build.load_manifest()

    def test_device_names(self):
        self.assertEqual(
            build.check_spec(
                "openduo-touch",
                {
                    "device": True,
                    "arch": "all",
                    "component": "c",
                    "summary": "s",
                    "description": "d",
                },
            ),
            ["a device package's name ends in -surface-duo2"],
        )
        self.assertTrue(
            build.check_spec(
                "openduo-x-surface-duo2",
                {"arch": "all", "component": "c", "summary": "s", "description": "d"},
            )
        )
        self.assertTrue(
            build.check_spec(
                "duo-wc", {"arch": "all", "component": "c", "summary": "s", "description": "d"}
            )
        )
        self.assertTrue(
            build.check_spec(
                "openduo-a", {"arch": "amd64", "component": "c", "summary": "s", "description": "d"}
            )
        )
        self.assertTrue(
            build.check_spec(
                "openduo-a",
                {
                    "arch": "all",
                    "component": "c",
                    "summary": "s",
                    "description": "d",
                    "depends": ["a; rm -rf /"],
                },
            )
        )

    def test_every_component_has_its_installer(self):
        stage = text("scripts/lib/stage-components.sh")
        installers = {
            "duo-wc": "compositor/duo-wc",
            "duo-shell": "shell/duo-shell",
            "duo-apps": "shell/duo-apps",
            "duo-settings": "shell/duo-settings",
            "duo-screencast": "shell/duo-screencast",
            "plymouth": "rootfs/plymouth",
            "nibfair": "apps/nibfair",
        }
        for name, spec in self.pkgs.items():
            c = spec["component"]
            self.assertIn(c, stage, name)
            src = installers.get(c, c)
            self.assertTrue(
                os.path.exists(os.path.join(REPO, src, "install-files.sh")), (name, src)
            )
            for chk in spec.get("check", []):
                self.assertTrue(chk.startswith(("/", "enabled:", "enabled-global:")), (name, chk))

    def test_the_device_packages(self):
        device = sorted(n for n, s in self.pkgs.items() if s.get("device"))
        self.assertIn("openduo-touch-surface-duo2", device)
        self.assertIn("openduo-sensors-surface-duo2", device)
        for n in device:
            self.assertTrue(n.endswith("-surface-duo2"))


class ControlFiles(unittest.TestCase):
    def test_description(self):
        d = build.description("One line", "first para\nline two\n\nsecond para")
        self.assertEqual(d, "One line\n first para\n line two\n .\n second para")

    def test_control(self):
        t = build.control_text(
            [
                ("Package", "openduo-x"),
                ("Depends", ["a", "b (>= 1)"]),
                ("Recommends", []),
                ("OpenDuo-Changes", "\n ## [0.1.0]\n .\n - x"),
                ("Description", "s\n l"),
            ]
        )
        self.assertEqual(
            t,
            "Package: openduo-x\nDepends: a, b (>= 1)\nOpenDuo-Changes:\n ## [0.1.0]\n .\n - x\n"
            "Description: s\n l\n",
        )

    def test_changes_field_round_trip(self):
        md = text("CHANGELOG.md")
        field = build.changes_field(md)
        back = up.changelog_sections(up.decode_field(field), limit=10)
        self.assertEqual(
            [s["version"] for s in back], [s["version"] for s in up.changelog_sections(md)]
        )

    def test_sources_and_pins(self):
        s = build.sources_text("https://example.org/openduo/apt")
        self.assertIn(
            "URIs: https://example.org/openduo/apt\nSuites: stable\nComponents: main\n", s
        )
        self.assertIn("Signed-By: /usr/share/keyrings/openduo-archive-keyring.asc", s)
        self.assertEqual(up.testing_sources(s).count("Suites: testing"), 1)
        p = build.preferences_text()
        self.assertIn("Pin: release o=OpenDuo\nPin-Priority: 990", p)
        self.assertIn("libcamera0.7", p)


class Scripts(unittest.TestCase):
    def test_component_scripts_render_and_parse(self):
        shell = (
            "dash"
            if subprocess.run(["sh", "-c", "command -v dash"], capture_output=True).returncode == 0
            else "sh"
        )
        for name, spec in build.load_manifest().items():
            text = build.component_scripts(name, spec)["postinst"]
            self.assertNotRegex(text, r"@[A-Z_]+@", name)
            self.assertIn("install-files.sh", text)
            if spec.get("user"):
                self.assertIn('install-files.sh "$U"', text)
            with tempfile.NamedTemporaryFile("w", suffix=".sh") as f:
                f.write(text)
                f.flush()
                r = subprocess.run([shell, "-n", f.name], capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, (name, r.stderr))
        dev = build.component_scripts("openduo-devmode", build.load_manifest()["openduo-devmode"])[
            "postinst"
        ]
        self.assertIn("env DEVELOPER_MODE=off sh ./install-files.sh", dev)

    def test_kernel_scripts(self):
        for n in ("postinst", "prerm", "postrm"):
            text = build.render(
                "kernel." + n,
                {
                    "PKG": "openduo-kernel-surface-duo2-7.3.4-duo2-gabc1234",
                    "RELEASE": "7.3.4-duo2-gabc1234",
                },
            )
            self.assertNotRegex(text, r"@[A-Z_]+@")
            r = subprocess.run(["sh", "-n"], input=text, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, (n, r.stderr))
        self.assertIn(
            'duo2-boot add "$REL"', build.render("kernel.postinst", {"PKG": "p", "RELEASE": "r"})
        )
        self.assertIn(
            '"$(uname -r)" = "$REL"', build.render("kernel.prerm", {"PKG": "p", "RELEASE": "r"})
        )

    def test_unfilled_placeholder_is_an_error(self):
        with self.assertRaises(ValueError):
            build.render("kernel.postinst", {"PKG": "p"})


if __name__ == "__main__":
    unittest.main()
