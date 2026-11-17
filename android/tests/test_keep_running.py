"""Keep Android apps running in the background (Duo Settings > Android apps,
KEEP_RUNNING in waydroid.conf): duo2-waydroid-idle does not stop the session,
and the hardware_manager.py patch of duo2-waydroid-patch skips Waydroid's
freeze. The patch is applied to waydroid 1.6.3's own hardware_manager.py
(tests/fixtures, unchanged from the package) and the result run with stand-in
`tools` modules (python3 -m unittest discover -s tests, in android/)."""

import importlib.machinery
import importlib.util
import os
import pwd
import sys
import tempfile
import types
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ANDROID = os.path.dirname(HERE)
FIXTURE = os.path.join(HERE, "fixtures", "waydroid-1.6.3", "hardware_manager.py")


def load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, path)
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


idle = load("duo2_waydroid_idle", os.path.join(ANDROID, "duo2-waydroid-idle"))
patch = load("duo2_waydroid_patch", os.path.join(ANDROID, "duo2-waydroid-patch"))


def write(d, name, text):
    path = os.path.join(d, name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)
    return path


class IdleConf(unittest.TestCase):
    def test_defaults(self):
        conf = idle.read_conf(["/nonexistent/a", "/nonexistent/b"])
        self.assertEqual(idle.idle_minutes(conf), 5)
        self.assertFalse(idle.keep_running(conf))

    def test_user_file_wins_and_comments(self):
        with tempfile.TemporaryDirectory() as d:
            etc = write(d, "etc.conf", "# comment\nIDLE_MIN=5\nKEEP_RUNNING=no\n")
            user = write(
                d, "user.conf", "KEEP_RUNNING = yes  # Duo Settings\nIDLE_MIN = 2 # minutes\n"
            )
            conf = idle.read_conf([etc, user])
            self.assertTrue(idle.keep_running(conf))
            self.assertEqual(idle.idle_minutes(conf), 2)
            write(d, "user.conf", "KEEP_RUNNING = no\n")
            self.assertFalse(idle.keep_running(idle.read_conf([etc, user])))

    def test_bad_values(self):
        self.assertEqual(idle.idle_minutes({"IDLE_MIN": "x"}), 5)
        self.assertEqual(idle.idle_minutes({"IDLE_MIN": "0"}), 1)
        self.assertFalse(idle.keep_running({"KEEP_RUNNING": "maybe"}))
        self.assertTrue(idle.keep_running({"KEEP_RUNNING": "On"}))


def edits_for(rel):
    for path, edits in patch.PATCHES:
        if path == rel:
            return edits
    raise KeyError(rel)


def stand_in_tools(freezes, stops, cfg):
    """sys.modules entries for the `tools` imports of hardware_manager.py."""
    tools = types.ModuleType("tools")
    actions = types.ModuleType("tools.actions")
    cm = types.ModuleType("tools.actions.container_manager")
    cm.freeze = lambda args: freezes.append(args)
    sm = types.ModuleType("tools.actions.session_manager")
    sm.stop = lambda args: stops.append(args)
    config = types.ModuleType("tools.config")
    config.load = lambda args: cfg
    helpers = types.ModuleType("tools.helpers")
    interfaces = types.ModuleType("tools.interfaces")
    ihw = types.ModuleType("tools.interfaces.IHardware")
    tools.actions, tools.config, tools.helpers, tools.interfaces = (
        actions,
        config,
        helpers,
        interfaces,
    )
    actions.container_manager, actions.session_manager = cm, sm
    interfaces.IHardware = ihw
    return {
        "tools": tools,
        "tools.actions": actions,
        "tools.actions.container_manager": cm,
        "tools.actions.session_manager": sm,
        "tools.config": config,
        "tools.helpers": helpers,
        "tools.interfaces": interfaces,
        "tools.interfaces.IHardware": ihw,
    }


class HardwareManagerPatch(unittest.TestCase):
    def setUp(self):
        with open(FIXTURE) as f:
            self.upstream = f.read()

    def test_applies_once_and_is_idempotent(self):
        new, todo = patch.apply(self.upstream, edits_for("services/hardware_manager.py"))
        self.assertIsNotNone(new, todo)
        self.assertEqual(len(todo), 2)
        again, todo2 = patch.apply(new, edits_for("services/hardware_manager.py"))
        self.assertEqual((again, todo2), (new, []))
        compile(new, "hardware_manager.py", "exec")

    def test_unknown_code_is_an_error(self):
        broken = self.upstream.replace("        cfg = tools.config.load(args)\n", "        pass\n")
        new, missing = patch.apply(broken, edits_for("services/hardware_manager.py"))
        self.assertIsNone(new)
        self.assertIn("def suspend", missing)

    def run_suspend(self, home_text, etc_text=None, suspend_action="freeze"):
        """Start the patched module with stand-ins, call Android's suspend()
        once; (freezes, session stops)."""
        new, _todo = patch.apply(self.upstream, edits_for("services/hardware_manager.py"))
        freezes, stops, captured = [], [], {}
        mods = stand_in_tools(freezes, stops, {"waydroid": {"suspend_action": suspend_action}})
        with tempfile.TemporaryDirectory() as d:
            etc = (
                write(d, "etc/waydroid.conf", etc_text)
                if etc_text is not None
                else os.path.join(d, "none")
            )
            home = os.path.join(d, "home")
            os.makedirs(home)
            if home_text is not None:
                write(home, ".config/duo2/waydroid.conf", home_text)
            new = new.replace('"/etc/duo2/waydroid.conf"', repr(etc))
            ns = {"__name__": "hardware_manager_under_test"}
            saved = {k: sys.modules.get(k) for k in mods}
            sys.modules.update(mods)
            real_getpwuid = pwd.getpwuid
            try:
                pwd.getpwuid = lambda uid: types.SimpleNamespace(pw_dir=home)

                def add_service(args, nfc, bt, suspend, reboot, upgrade, shutdown):
                    captured["suspend"] = suspend
                    ns["stopping"] = True

                mods["tools.interfaces.IHardware"].add_service = add_service
                exec(compile(new, "hardware_manager.py", "exec"), ns)
                args = types.SimpleNamespace(session={"user_id": "1000"})
                ns["start"](args)
                args.hardware_manager.join(5)
                captured["suspend"]()
            finally:
                pwd.getpwuid = real_getpwuid
                for k, v in saved.items():
                    if v is None:
                        sys.modules.pop(k, None)
                    else:
                        sys.modules[k] = v
        return len(freezes), len(stops)

    def test_off_freezes_as_upstream(self):
        self.assertEqual(self.run_suspend(None), (1, 0))
        self.assertEqual(self.run_suspend("KEEP_RUNNING = no\n"), (1, 0))
        self.assertEqual(self.run_suspend(None, suspend_action="stop"), (0, 1))

    def test_on_skips_the_freeze(self):
        self.assertEqual(self.run_suspend("KEEP_RUNNING = yes\n"), (0, 0))
        self.assertEqual(self.run_suspend(None, etc_text="KEEP_RUNNING=yes\n"), (0, 0))
        self.assertEqual(
            self.run_suspend("KEEP_RUNNING = no\n", etc_text="KEEP_RUNNING=yes\n"), (1, 0)
        )

    def test_symlinked_user_file_is_not_followed(self):
        with tempfile.TemporaryDirectory() as d:
            target = write(d, "secret", "KEEP_RUNNING=yes\n")
            new, _todo = patch.apply(self.upstream, edits_for("services/hardware_manager.py"))
            ns = {"os": os}
            src = new[new.index("def _duo2_keep_running") : new.index("def start(args):")]
            src = src.replace('"/etc/duo2/waydroid.conf"', repr(os.path.join(d, "none")))
            exec(src, ns)
            home = os.path.join(d, "home")
            os.makedirs(os.path.join(home, ".config/duo2"))
            os.symlink(target, os.path.join(home, ".config/duo2/waydroid.conf"))
            real_getpwuid = pwd.getpwuid
            try:
                pwd.getpwuid = lambda uid: types.SimpleNamespace(pw_dir=home)
                self.assertFalse(
                    ns["_duo2_keep_running"](types.SimpleNamespace(session={"user_id": "1000"}))
                )
                self.assertFalse(ns["_duo2_keep_running"](types.SimpleNamespace()))
            finally:
                pwd.getpwuid = real_getpwuid


if __name__ == "__main__":
    unittest.main()
