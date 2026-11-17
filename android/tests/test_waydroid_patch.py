"""duo2-waydroid-patch across its own versions: a Waydroid patched by an
earlier version of the script (whose comments were worded differently) counts
as patched, gets the current wording, and passes --check; a replacement that
is reworded must keep its earlier wordings (python3 -m unittest discover -s
tests, in android/)."""

import hashlib
import importlib.machinery
import importlib.util
import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
ANDROID = os.path.dirname(HERE)

loader = importlib.machinery.SourceFileLoader(
    "duo2_waydroid_patch", os.path.join(ANDROID, "duo2-waydroid-patch")
)
spec = importlib.util.spec_from_loader(loader.name, loader)
patch = importlib.util.module_from_spec(spec)
loader.exec_module(patch)


def h(text):
    return hashlib.sha256(text.encode()).hexdigest()[:16]


# Every replacement that has shipped, by its upstream code (the first 16 hex
# of their SHA-256): a phone may still carry any of them. A reworded
# replacement adds its new hash here and keeps the old text in its edit's
# earlier wordings.
SHIPPED = {
    ("helpers/lxc.py", "4f89233c5e827de4"): {"0732f0b99ced06a0"},
    ("helpers/lxc.py", "a10aefedd47696b2"): {"1a4574dd9765494b"},
    ("helpers/lxc.py", "00ccfc1e8772f875"): {"1ff7f9c0b2f003a7"},
    ("helpers/lxc.py", "93946a35335792be"): {"3d427b985e4d6588"},
    ("helpers/lxc.py", "c81a2552e068b7f9"): {"a5548121e41857f6"},
    ("actions/container_manager.py", "a3c9ae689263ecad"): {"43c395e8cc33df77"},
    ("actions/app_manager.py", "73386524768c6a88"): {"8c13121e754e13ef"},
    ("actions/app_manager.py", "54ff9eca53b8812b"): {"78fbf09a44847d4e"},
    ("interfaces/IHardware.py", "b12642a8a3bb898a"): {"248b628f89d04b6c"},
    ("services/hardware_manager.py", "1f420c833964e7ae"): {"57afd0b1ae30a92f"},
    ("services/hardware_manager.py", "7b32495d0d4f9a09"): {"be89060d892d4ede"},
}


# A reworded edit, as one would look (no shipped edit has been reworded since
# the public release): its replacement plus the wording an earlier version wrote.
SAMPLE = (
    '    for n in glob.glob("/dev/sample*"):\n        make_entry(n)\n',
    "    " + patch.MARK + "no sample nodes (new wording)\n",
    1,
    ("    " + patch.MARK + "no sample nodes (old wording)\n",),
)


def edits():
    for rel, file_edits in patch.PATCHES:
        for edit in file_edits:
            old, repl, count, *rest = edit
            yield rel, edit, old, repl, count, (rest[0] if rest else ())


class Wordings(unittest.TestCase):
    def test_every_shipped_wording_is_kept(self):
        for rel, _edit, old, repl, _count, earlier in edits():
            key = (rel, h(old))
            self.assertIn(key, SHIPPED, "a new edit: add its replacement to SHIPPED")
            self.assertIn(
                h(repl), SHIPPED[key], "%s: a reworded replacement: add it to SHIPPED" % rel
            )
            self.assertEqual(
                SHIPPED[key] - {h(repl)},
                {h(e) for e in earlier},
                "%s: every earlier wording that shipped stays in the edit" % rel,
            )

    def test_earlier_wording_counts_as_patched_and_is_renewed(self):
        n = 0
        sample = ("sample.py", SAMPLE, SAMPLE[0], SAMPLE[1], SAMPLE[2], SAMPLE[3])
        for rel, edit, old, repl, count, earlier in [*edits(), sample]:
            for e in earlier:
                n += 1
                text = "before\n" + e * count + "after\n"
                new, todo = patch.apply(text, [edit])
                self.assertEqual(todo, [], rel)
                self.assertEqual(new, "before\n" + repl * count + "after\n", rel)
        self.assertGreater(n, 0)

    def test_upstream_code_is_still_patched(self):
        for rel, edit, old, repl, count, _earlier in edits():
            text = "before\n" + old * count + "after\n"
            new, todo = patch.apply(text, [edit])
            self.assertEqual(todo, [old], rel)
            self.assertEqual(new.count(repl), count, rel)
            self.assertEqual(patch.apply(new, [edit]), (new, []), rel)

    def test_changed_upstream_code_is_an_error(self):
        for rel, edit, old, _repl, _count, _earlier in edits():
            self.assertEqual(patch.apply("other code\n", [edit]), (None, old), rel)


class Main(unittest.TestCase):
    def run_main(self, base, *args):
        saved = patch.BASE, patch.PATCHES, sys.argv
        patch.BASE, patch.PATCHES, sys.argv = (
            base,
            [("helpers/lxc.py", [SAMPLE])],
            ["duo2-waydroid-patch", *args],
        )
        out, err = io.StringIO(), io.StringIO()
        try:
            with redirect_stdout(out), redirect_stderr(err), self.assertRaises(SystemExit) as cm:
                patch.main()
        finally:
            patch.BASE, patch.PATCHES, sys.argv = saved
        return cm.exception.code, out.getvalue() + err.getvalue()

    def test_check_passes_on_an_earlier_wording_and_a_run_renews_it(self):
        old, repl, _count, earlier = SAMPLE
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "helpers", "lxc.py")
            os.makedirs(os.path.dirname(path))
            with open(path, "w") as f:
                f.write("def f():\n" + earlier[0] + "    pass\n")
            self.assertEqual(self.run_main(d + "/", "--check")[0], 0)
            with open(path) as f:
                self.assertIn(earlier[0], f.read(), "--check writes nothing")
            code, said = self.run_main(d + "/")
            self.assertEqual(code, 0)
            self.assertIn("comments renewed", said)
            with open(path) as f:
                self.assertEqual(f.read(), "def f():\n" + repl + "    pass\n")
            self.assertEqual(self.run_main(d + "/", "--check")[0], 0)
            with open(path, "w") as f:
                f.write("def f():\n" + old + "    pass\n")
            self.assertEqual(self.run_main(d + "/", "--check")[0], 1)


if __name__ == "__main__":
    unittest.main()
