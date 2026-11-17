"""calls.CallWatch under do not disturb (dndmodel.py decides): a call that
rings in is quiet (lights nothing, counts as silenced), a repeated caller
rings through the shell's own feedbackd event, which ends with the ring.
No bus: the D-Bus calls are recorded."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

try:
    from duoshell import calls, callsmodel, dndmodel
except ImportError:  # no PyGObject
    calls = None


class Bus:
    def __init__(self):
        self.sent = []

    def call(self, name, path, iface, method, params, reply, flags, timeout, cancel, cb, data):
        self.sent.append((method, params.unpack() if params is not None else None, data))


class Shell:
    def __init__(self, dnd_on, prefs):
        self.dnd = type("D", (), {"on": dnd_on, "prefs": lambda _s: prefs})()
        self.woken = 0

    def wake_for_call(self):
        self.woken += 1

    def incoming_caller_changed(self, _c):
        pass

    def call_card_changed(self):
        pass


@unittest.skipIf(calls is None, "no PyGObject")
class Dnd(unittest.TestCase):
    def watch(self, dnd_on, prefs=None):
        w = calls.CallWatch.__new__(calls.CallWatch)
        w.shell = Shell(dnd_on, prefs or dict(dndmodel.DEFAULTS))
        w.calls, w.since, w.ringing, w.silenced = {}, {}, set(), set()
        w.ring = w.ring_failed = w.ring_renew = w.strip = w.card = None
        w.speaker = False
        w.history, w.dnd_rings = {}, {}
        w.bus = Bus()
        w.send_ring = lambda: None
        return w

    def ring(self, w, path, number):
        w.calls[path] = {
            "state": callsmodel.INCOMING,
            "inbound": True,
            "id": number,
            "name": "",
            "ui_active": True,
        }
        w.changed()

    def end(self, w, path):
        w.calls.pop(path)
        w.changed()

    def test_off_rings_as_always(self):
        w = self.watch(False)
        self.ring(w, "/org/gnome/Calls/Call/1", "+491511234567")
        self.assertEqual(w.shell.woken, 1)
        self.assertEqual(w.silenced, set())
        self.assertEqual(w.bus.sent, [])

    def test_quiet_then_repeated_caller_rings_through(self):
        w = self.watch(True)
        self.ring(w, "/org/gnome/Calls/Call/1", "+491511234567")
        self.assertEqual(w.shell.woken, 0)  # lights nothing
        self.assertEqual(w.silenced, {"/org/gnome/Calls/Call/1"})  # keys need not silence it
        self.assertEqual(w.ring, "silent")
        self.end(w, "/org/gnome/Calls/Call/1")
        # the same caller again within 15 minutes: it rings, through the shell
        self.ring(w, "/org/gnome/Calls/Call/2", "+49 151 1234567")
        self.assertEqual(w.shell.woken, 1)
        self.assertEqual(w.ring, "loud")
        trig = [s for s in w.bus.sent if s[0] == "TriggerFeedback"]
        self.assertEqual(
            trig,
            [
                (
                    "TriggerFeedback",
                    (calls.SHELL_APP_ID, "phone-incoming-call", {}, 0),
                    "/org/gnome/Calls/Call/2",
                )
            ],
        )
        # the event's id comes back; the ring ending ends it
        w.dnd_rings["/org/gnome/Calls/Call/2"] = 7
        self.end(w, "/org/gnome/Calls/Call/2")
        self.assertIn(("EndFeedback", (7,), "end ring"), w.bus.sent)
        self.assertEqual(w.dnd_rings, {})

    def test_a_key_silences_the_shells_ring(self):
        w = self.watch(True)
        self.ring(w, "/org/gnome/Calls/Call/1", "+491511234567")
        self.end(w, "/org/gnome/Calls/Call/1")
        self.ring(w, "/org/gnome/Calls/Call/2", "+491511234567")
        w.dnd_rings["/org/gnome/Calls/Call/2"] = 9
        self.assertTrue(w.silence("power key"))
        self.assertIn(("EndFeedback", (9,), "end ring"), w.bus.sent)

    def test_without_the_repeat_rule_it_stays_quiet(self):
        w = self.watch(True, {"repeat_callers": False, "contacts": False})
        self.ring(w, "/org/gnome/Calls/Call/1", "+491511234567")
        self.end(w, "/org/gnome/Calls/Call/1")
        self.ring(w, "/org/gnome/Calls/Call/2", "+491511234567")
        self.assertEqual(w.shell.woken, 0)
        self.assertEqual([s for s in w.bus.sent if s[0] == "TriggerFeedback"], [])


if __name__ == "__main__":
    unittest.main()
