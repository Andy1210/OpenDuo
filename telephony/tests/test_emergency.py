"""duo2-wwan's emergency-call handling (telephony/README.md "Emergency
calls"), played through with a stand-in ModemManager and a fake clock: no
call is ever placed. python3 -m unittest discover -s tests (telephony/)."""

import enum
import os
import sys
import tempfile
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_wwan import load_wwan  # noqa: E402


class ModemState(enum.IntEnum):
    (
        FAILED,
        UNKNOWN,
        INITIALIZING,
        LOCKED,
        DISABLED,
        DISABLING,
        ENABLING,
        ENABLED,
        SEARCHING,
        REGISTERED,
        DISCONNECTING,
        CONNECTING,
        CONNECTED,
    ) = range(-1, 12)


class PowerState(enum.IntEnum):
    UNKNOWN, OFF, LOW, ON = range(4)


class CallState(enum.IntEnum):
    UNKNOWN, DIALING, RINGING_OUT, RINGING_IN, ACTIVE, HELD, WAITING, TERMINATED = range(8)


class Reason(enum.IntEnum):
    UNKNOWN, OUTGOING_STARTED, INCOMING_NEW, ACCEPTED, TERMINATED, REFUSED_OR_BUSY, ERROR = range(7)


class FailedReason(enum.IntEnum):
    NONE, UNKNOWN, SIM_MISSING, SIM_ERROR = range(4)


def fake_mm():
    mm = types.SimpleNamespace()
    mm.ModemState = ModemState
    mm.ModemPowerState = PowerState
    mm.CallState = CallState
    mm.CallStateReason = Reason
    mm.ModemStateFailedReason = FailedReason
    mm.CallDirection = types.SimpleNamespace(UNKNOWN=0, INCOMING=1, OUTGOING=2)
    mm.modem_state_failed_reason_get_string = lambda r: {2: "sim-missing"}.get(int(r), "unknown")
    mm.call_state_get_string = lambda s: CallState(int(s)).name.lower().replace("_", "-")
    mm.call_state_reason_get_string = lambda r: Reason(int(r)).name.lower().replace("_", "-")

    class Props:
        def __init__(self):
            self.number = None

        def set_number(self, n):
            self.number = n

    mm.CallProperties = types.SimpleNamespace(new=Props)
    return mm


class Clock:
    """GLib's timers on a fake clock (run() fires what is due)."""

    def __init__(self):
        self.now = 1000.0
        self.timers = {}
        self.next = 1
        self.Error = Exception
        self.PRIORITY_DEFAULT = 0

    def timeout_add_seconds(self, s, fn, *args):
        tid = self.next
        self.next += 1
        self.timers[tid] = (self.now + s, fn, args)
        return tid

    def source_remove(self, tid):
        self.timers.pop(tid, None)

    def child_watch_add(self, _prio, pid, fn, data):
        fn(pid, 0, data)

    def advance(self, s):
        end = self.now + s
        while True:
            due = sorted((t, tid) for tid, (t, _f, _a) in self.timers.items() if t <= end)
            if not due:
                break
            t, tid = due[0]
            _t, fn, args = self.timers.pop(tid)
            self.now = max(self.now, t)
            fn(*args)
        self.now = end


class Signals:
    def __init__(self):
        self.handlers = {}
        self.n = 0

    def connect(self, name, fn):
        self.n += 1
        self.handlers[self.n] = (name, fn)
        return self.n

    def handler_disconnect(self, h):
        self.handlers.pop(h, None)

    def emit(self, name, *args):
        for n, fn in list(self.handlers.values()):
            if n == name:
                fn(self, *args)


class Call(Signals):
    def __init__(self, path, number, direction=2):
        super().__init__()
        self.path, self.number, self.direction = path, number, direction
        self.state = CallState.UNKNOWN
        self.started = 0

    def get_path(self):
        return self.path

    def get_number(self):
        return self.number

    def get_direction(self):
        return self.direction

    def get_state(self):
        return self.state

    def set_state(self, new, reason):
        old, self.state = self.state, new
        self.emit("state-changed", old, new, reason)

    def start(self, _c, cb):
        self.started += 1
        cb(self, None)

    def start_finish(self, _res):
        return True


class Voice(Signals):
    def __init__(self):
        super().__init__()
        self.calls = []
        self.created = []

    def list_calls_sync(self, _c):
        return list(self.calls)

    def add(self, call):
        self.calls.append(call)
        self.emit("call-added", call.path)

    def create_call(self, props, _c, cb, number):
        call = Call(
            "/org/freedesktop/ModemManager1/Call/%d" % (100 + len(self.created)), props.number
        )
        self.created.append(call)
        cb(self, call, number)
        self.add(call)

    def create_call_finish(self, res):
        return res


class Modem(Signals):
    def __init__(self, state, power=PowerState.LOW, failed=FailedReason.NONE):
        super().__init__()
        self.state, self.power, self.failed = state, power, failed
        self.enables = 0
        self.power_requests = []
        self.refuse_on = False

    def get_state(self):
        return self.state

    def get_power_state(self):
        return self.power

    def get_state_failed_reason(self):
        return self.failed

    def dup_sim_path(self):
        return "/"

    def get_sim_sync(self, _c):
        return None

    def list_bearers_sync(self, _c):
        return []

    def enable(self, _c, cb):
        self.enables += 1
        self.state, self.power = ModemState.ENABLED, PowerState.ON
        cb(self, None)

    def enable_finish(self, _res):
        return True

    def disable(self, _c, cb):
        self.state = ModemState.DISABLED
        cb(self, None)

    def disable_finish(self, _res):
        return True

    def set_power_state(self, state, _c, cb, *data):
        self.power_requests.append(state)
        if state == PowerState.ON and self.state == ModemState.LOCKED:
            self.refuse_on = True
        else:
            self.power = state
        cb(self, None, *data)

    def set_power_state_finish(self, _res):
        if self.refuse_on:
            self.refuse_on = False
            raise Exception("Must be in disabled or failed state")
        return True


class M3gpp(Signals):
    def __init__(self, code):
        super().__init__()
        self.code = code

    def get_operator_code(self):
        return self.code


class Obj(Signals):
    def __init__(self, modem, voice, m3gpp=None):
        super().__init__()
        self.modem, self.voice, self.m3gpp = modem, voice, m3gpp

    def get_object_path(self):
        return "/org/freedesktop/ModemManager1/Modem/0"

    def get_modem(self):
        return self.modem

    def get_modem_3gpp(self):
        return self.m3gpp

    def get_modem_simple(self):
        return None

    def get_modem_voice(self):
        return self.voice


class Daemon:
    def __init__(self, wwan, airplane_file, airplane=False):
        self.wwan = wwan
        self.conf = dict(wwan.DEFAULTS)
        self.airplane = airplane
        self.airplane_file = airplane_file
        self.provision_tries = 2  # no duo2-sim runs here
        self.nr = types.SimpleNamespace(evaluate=lambda *a: None, modem_gone=lambda *a: None)
        self.net = types.SimpleNamespace(down=lambda: None)
        self.modems = {}

    def data_on(self):
        return False

    def airplane_changed(self):
        airplane = self.wwan.read_switch(self.airplane_file) is True
        if airplane != self.airplane:
            self.airplane = airplane
            for m in self.modems.values():
                m.airplane_switched()


class Popen:
    """qmicli, recorded (never run)."""

    runs = []

    def __init__(self, argv, **_kw):
        Popen.runs.append(argv)
        self.pid = 4242
        self.returncode = 0
        self.stderr = None

    def wait(self, timeout=None):
        return 0


class Emergency(unittest.TestCase):
    def setUp(self):
        self.wwan = load_wwan()
        self.clock = Clock()
        self.wwan.Mm = fake_mm()
        self.wwan.GLib = self.clock
        self.wwan.time = types.SimpleNamespace(monotonic=lambda: self.clock.now)
        Popen.runs = []
        self.wwan.subprocess = types.SimpleNamespace(
            Popen=Popen, DEVNULL=None, PIPE=None, TimeoutExpired=Exception
        )
        self.dir = tempfile.TemporaryDirectory()
        self.wwan.AIRPLANE_SWITCH = os.path.join(self.dir.name, "airplane")

    def tearDown(self):
        self.dir.cleanup()

    def make(self, modem, airplane=False, m3gpp=None, **conf):
        if airplane:
            with open(self.wwan.AIRPLANE_SWITCH, "w") as f:
                f.write("on\n")
        d = Daemon(self.wwan, self.wwan.AIRPLANE_SWITCH, airplane)
        d.conf.update(conf)
        voice = Voice()
        m = self.wwan.Modem(d, Obj(modem, voice, m3gpp))
        d.modems[m.path] = m
        return d, m, voice

    def dial(self, voice, number="112"):
        call = Call("/org/freedesktop/ModemManager1/Call/1", number)
        voice.add(call)
        return call

    def test_no_sim_radio_on_at_once(self):
        modem = Modem(ModemState.FAILED, failed=FailedReason.SIM_MISSING)
        _d, m, _v = self.make(modem)
        self.assertEqual(modem.power_requests, [PowerState.ON])  # ModemManager's own "on"
        self.assertIsNotNone(m.radio_online)

    def test_no_sim_radio_stays_off_when_configured(self):
        modem = Modem(ModemState.FAILED, failed=FailedReason.SIM_MISSING)
        self.make(modem, EMERGENCY_RADIO="no")
        self.assertEqual(modem.power_requests, [])

    def test_locked_sim_radio_on_over_qmi(self):
        modem = Modem(ModemState.LOCKED)
        _d, m, _v = self.make(modem)
        self.assertEqual(modem.power_requests, [])
        self.assertEqual(
            Popen.runs, [[self.wwan.QMICLI, "-d", "qrtr://0", "--dms-set-operating-mode=online"]]
        )
        self.assertIsNotNone(m.radio_online)

    def test_airplane_mode_keeps_radio_off_until_an_emergency_call(self):
        modem = Modem(ModemState.DISABLED)
        d, m, voice = self.make(modem, airplane=True)
        self.assertEqual(modem.enables, 0)
        # an ordinary number changes nothing
        voice.add(Call("/org/freedesktop/ModemManager1/Call/0", "0301234567"))
        self.assertTrue(d.airplane)
        call = self.dial(voice)
        # airplane mode off (the switch file too), the modem enabled
        self.assertFalse(d.airplane)
        with open(self.wwan.AIRPLANE_SWITCH) as f:
            self.assertEqual(f.read().strip(), "off")
        self.assertEqual(modem.enables, 1)
        # the first attempt failed for the radio: dialled once more when it is up
        call.set_state(CallState.DIALING, Reason.OUTGOING_STARTED)
        call.set_state(CallState.TERMINATED, Reason.UNKNOWN)
        self.assertEqual(voice.created, [])
        self.clock.advance(self.wwan.RADIO_SETTLE + 1)
        self.assertEqual([c.number for c in voice.created], ["112"])
        again = voice.created[0]
        # Calls starts an outgoing call it sees; here nobody does, so we do
        self.clock.advance(self.wwan.START_GRACE + 1)
        self.assertEqual(again.started, 1)
        # and that one is never dialled again, even if it fails too
        again.set_state(CallState.TERMINATED, Reason.UNKNOWN)
        self.clock.advance(30)
        self.assertEqual(len(voice.created), 1)

    def test_national_number_waits_for_the_registration(self):
        # 110 (Germany) with a SIM in: ModemManager dials it only registered
        modem = Modem(ModemState.DISABLED)
        _d, _m, voice = self.make(modem, airplane=True, m3gpp=M3gpp("26201"))
        call = self.dial(voice, "110")
        self.assertEqual(modem.enables, 1)
        call.set_state(CallState.TERMINATED, Reason.UNKNOWN)
        self.clock.advance(self.wwan.RADIO_SETTLE + 5)
        self.assertEqual(voice.created, [])  # enabled, not registered yet
        modem.state = ModemState.REGISTERED
        self.clock.advance(2)
        self.assertEqual([c.number for c in voice.created], ["110"])

    def test_the_test_number_takes_the_emergency_path(self):
        # EMERGENCY_TEST_NUMBER: an ordinary number (the owner's other phone)
        # that duo2-wwan alone treats as an emergency one, to try the
        # airplane-mode path without dialling 112. ModemManager refuses it
        # unregistered, so the redial waits for the registration.
        modem = Modem(ModemState.DISABLED)
        _d, _m, voice = self.make(modem, airplane=True, EMERGENCY_TEST_NUMBER="0170 1234567")
        call = self.dial(voice, "01701234567")
        self.assertEqual(modem.enables, 1)
        call.set_state(CallState.TERMINATED, Reason.UNKNOWN)
        self.clock.advance(self.wwan.RADIO_SETTLE + 2)
        self.assertEqual(voice.created, [])
        modem.state = ModemState.REGISTERED
        self.clock.advance(2)
        self.assertEqual([c.number for c in voice.created], ["01701234567"])

    def test_an_ordinary_number_in_airplane_mode_changes_nothing(self):
        modem = Modem(ModemState.DISABLED)
        d, _m, voice = self.make(modem, airplane=True)
        call = self.dial(voice, "01701234567")
        call.set_state(CallState.TERMINATED, Reason.UNKNOWN)
        self.clock.advance(60)
        self.assertTrue(d.airplane)
        self.assertEqual(modem.enables, 0)
        self.assertEqual(voice.created, [])

    def test_no_redial_after_a_hang_up(self):
        modem = Modem(ModemState.DISABLED)
        _d, _m, voice = self.make(modem, airplane=True)
        call = self.dial(voice)
        call.set_state(CallState.DIALING, Reason.OUTGOING_STARTED)
        call.set_state(CallState.TERMINATED, Reason.TERMINATED)
        self.clock.advance(60)
        self.assertEqual(voice.created, [])

    def test_no_redial_for_a_call_that_got_through(self):
        modem = Modem(ModemState.DISABLED)
        _d, _m, voice = self.make(modem, airplane=True)
        call = self.dial(voice)
        call.set_state(CallState.DIALING, Reason.OUTGOING_STARTED)
        call.set_state(CallState.ACTIVE, Reason.ACCEPTED)
        call.set_state(CallState.TERMINATED, Reason.UNKNOWN)
        self.clock.advance(60)
        self.assertEqual(voice.created, [])

    def test_radio_on_needs_no_help(self):
        modem = Modem(ModemState.REGISTERED, power=PowerState.ON)
        d, m, voice = self.make(modem)
        call = self.dial(voice)
        self.assertFalse(m.attempt["forced"])
        call.set_state(CallState.TERMINATED, Reason.UNKNOWN)
        self.clock.advance(60)
        self.assertEqual(voice.created, [])
        self.assertFalse(d.airplane)

    def test_disabled_by_hand_is_enabled_for_an_emergency(self):
        modem = Modem(ModemState.DISABLED)
        _d, m, voice = self.make(modem, ENABLE="no")
        self.assertEqual(modem.enables, 0)
        self.dial(voice)
        self.assertEqual(modem.enables, 1)

    def test_locked_sim_emergency_without_emergency_radio(self):
        modem = Modem(ModemState.LOCKED)
        _d, m, voice = self.make(modem, EMERGENCY_RADIO="no")
        self.assertEqual(Popen.runs, [])
        call = self.dial(voice)
        self.assertEqual(len(Popen.runs), 1)  # online now, for the call
        call.set_state(CallState.TERMINATED, Reason.UNKNOWN)
        self.clock.advance(self.wwan.RADIO_SETTLE + 1)
        self.assertEqual([c.number for c in voice.created], ["112"])

    def test_the_radio_cycle_does_not_make_an_emergency_call_wait(self):
        modem = Modem(ModemState.REGISTERED, power=PowerState.ON)
        _d, m, voice = self.make(modem)
        m.radio_cycle()  # disable, low power, 3 s, enable
        self.assertTrue(m.cycling)
        self.dial(voice)
        self.assertFalse(m.cycling)
        self.assertEqual(modem.enables, 1)


if __name__ == "__main__":
    unittest.main()
