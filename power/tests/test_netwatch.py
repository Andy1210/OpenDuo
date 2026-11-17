"""duo2-try-netwatch's decision: python3 -m unittest discover -s tests
(from power/)."""

import importlib.machinery
import importlib.util
import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))


def load(name):
    path = os.path.join(os.path.dirname(HERE), name)
    loader = importlib.machinery.SourceFileLoader(name.replace("-", "_"), path)
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


nw = load("duo2-try-netwatch")


def run(watch, samples):
    """samples: (time, ok) -> the first reboot's time, or None."""
    for t, ok in samples:
        action, _ = watch.step(t, ok)
        if action == "reboot":
            return t
    return None


class TestWatch(unittest.TestCase):
    def test_never_up_reboots_at_boot_grace(self):
        w = nw.Watch(300, 600)
        self.assertEqual(run(w, [(t, False) for t in range(10, 1000, 15)]), 310)

    def test_up_in_time_keeps_running(self):
        w = nw.Watch(300, 600)
        s = [(t, t >= 280) for t in range(10, 5000, 15)]
        self.assertIsNone(run(w, s))

    def test_loss_after_up_reboots_after_loss_limit(self):
        w = nw.Watch(300, 600)
        s = [(t, t < 1000) for t in range(10, 3000, 15)]
        # samples at 10, 25, ...: the last ok is 985, the first t >= 985 + 600
        self.assertEqual(run(w, s), 1585)

    def test_short_losses_are_tolerated(self):
        w = nw.Watch(300, 600)
        s = [(t, not (1000 <= t < 1500) and not (2000 <= t < 2580)) for t in range(10, 4000, 15)]
        self.assertIsNone(run(w, s))

    def test_late_loss_does_not_use_boot_grace(self):
        # once up, a loss is judged by the loss limit, not the boot grace
        w = nw.Watch(300, 600)
        self.assertEqual(w.step(100, True)[0], "ok")
        self.assertEqual(w.step(400, False)[0], "wait")
        self.assertEqual(w.step(699, False)[0], "wait")
        self.assertEqual(w.step(700, False)[0], "reboot")

    def test_long_sleep_alone_does_not_reboot(self):
        # ok at 100 s, then a 1800 s sleep: the first checks after the resume
        # fail (WiFi still coming back) with little awake time behind them
        w = nw.Watch(300, 600)
        self.assertEqual(w.step(100, True, 100)[0], "ok")
        self.assertEqual(w.step(1905, False, 105)[0], "wait")
        self.assertEqual(w.step(1920, False, 120)[0], "wait")
        self.assertEqual(w.step(1935, True, 135)[0], "ok")

    def test_loss_across_sleeps_still_reboots(self):
        # short awake windows between sleeps without WiFi: the awake time
        # adds up, and the loss is judged once there is enough of it
        w = nw.Watch(300, 600)
        w.step(100, True, 100)
        t, m, action = 100, 100, "wait"
        while action == "wait" and t < 20000:
            t += 300  # asleep, then 15 s awake
            m += 15
            action = w.step(t, False, m)[0]
        self.assertEqual(action, "reboot")
        self.assertGreaterEqual(m - 100, 60)

    def test_cmdline_loss(self):
        self.assertIsNone(nw.cmdline_loss("root=PARTLABEL=linux duo2.try=x"))
        self.assertEqual(nw.cmdline_loss("duo2.try=x duo2.netwatch.loss=1800"), 1800)
        self.assertEqual(nw.cmdline_loss("duo2.netwatch.loss=60"), nw.LOSS_MIN)
        self.assertEqual(nw.cmdline_loss("duo2.netwatch.loss=99999"), nw.LOSS_MAX)
        self.assertIsNone(nw.cmdline_loss("duo2.netwatch.loss=soon"))
        self.assertTrue(nw.cmdline_off("a duo2.netwatch=off b"))
        self.assertFalse(nw.cmdline_off("duo2.netwatch.loss=1800"))

    def test_parse_gateway(self):
        self.assertEqual(
            nw.parse_gateway("default via 192.0.2.1 proto dhcp src 192.0.2.203 metric 1024\n"),
            "192.0.2.1",
        )
        self.assertIsNone(nw.parse_gateway(""))
        self.assertIsNone(nw.parse_gateway("default dev qmapmux0.0 scope link metric 4096\n"))


if __name__ == "__main__":
    unittest.main()
