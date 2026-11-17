"""The proximity sensor during calls (proximitymodel.py decides).

Three D-Bus sources, all asynchronous, nothing polled:

- ModemManager (system bus): the calls and their states (Modem.Voice's
  Calls, each Call's State; calls are not in the object manager, so their
  objects are watched by path);
- callaudiod's interface (session bus, duo2-callaudio): SpeakerState, the
  earpiece or the loudspeakers;
- iio-sensor-proxy (system bus): ProximityNear, claimed only while a call
  on the earpiece wants it (ClaimProximity / ReleaseProximity; the proxy
  also releases a claim when our connection goes).

The result goes to duo-wc as `set_proximity` (the trusted control
connection), renewed every RENEW_S while held: duo-wc ends a hold that is
not renewed, so a shell that died with the phone at an ear cannot leave a
panel dark.

Testing without a call or a hand: while root's /etc/duo2/shell-fake-call
exists, the calls and the sensor are read from the SESSION bus instead,
where tests/fake_call.py plays ModemManager's voice part and the sensor
(an app cannot switch this on: the file is root's).
"""

import os

from gi.repository import Gio, GLib

from . import proximitymodel as model
from . import wc

FAKE_SWITCH = "/etc/duo2/shell-fake-call"

MM = "org.freedesktop.ModemManager1"
MM_PATH = "/org/freedesktop/ModemManager1"
VOICE = "org.freedesktop.ModemManager1.Modem.Voice"
CALL = "org.freedesktop.ModemManager1.Call"
OM = "org.freedesktop.DBus.ObjectManager"
PROPS = "org.freedesktop.DBus.Properties"

CA = "org.mobian_project.CallAudio"
CA_PATH = "/org/mobian_project/CallAudio"

SP = "net.hadess.SensorProxy"
SP_PATH = "/net/hadess/SensorProxy"


def log(*args):
    print("duo-shell: proximity:", *args, flush=True)


class Proximity:
    """`shell.wc_state` is read for the windows and the lit panels; call
    `wc_changed()` when it changes."""

    def __init__(self, shell):
        self.shell = shell
        self.fake = os.path.exists(FAKE_SWITCH)
        self.calls = {}  # call path -> state
        self.voices = {}  # modem path -> [call paths]
        self.speaker = None
        self.has_sensor = False
        self.claimed = False
        self.claiming = False
        self.debounce = model.Debounce()
        self.settle_id = None
        self.renew_id = None
        self.sent = None  # the panels last sent to duo-wc
        self.failed = None  # the last error sending them
        self.sys = self.ses = None
        try:
            self.ses = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            self.sys = self.ses if self.fake else Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        except GLib.Error as e:
            log("no bus (%s): no proximity during calls" % e.message)
            return
        if self.fake:
            log("using the session bus (%s): a fake modem and sensor for testing" % FAKE_SWITCH)
        # A hold left by a shell that ran before this one ends now.
        self.send([])
        self.watch_modem()
        self.watch_callaudio()
        self.watch_sensor()

    # -- ModemManager's calls ---------------------------------------------------

    def watch_modem(self):
        bus = self.sys
        bus.signal_subscribe(
            MM, OM, None, MM_PATH, None, Gio.DBusSignalFlags.NONE, lambda *_a: self.read_modems()
        )
        bus.signal_subscribe(
            MM,
            PROPS,
            "PropertiesChanged",
            None,
            VOICE,
            Gio.DBusSignalFlags.NONE,
            lambda *_a: self.read_modems(),
        )
        bus.signal_subscribe(
            MM, VOICE, None, None, None, Gio.DBusSignalFlags.NONE, lambda *_a: self.read_modems()
        )
        bus.signal_subscribe(
            MM, PROPS, "PropertiesChanged", None, CALL, Gio.DBusSignalFlags.NONE, self.call_props
        )
        bus.signal_subscribe(
            MM, CALL, "StateChanged", None, None, Gio.DBusSignalFlags.NONE, self.call_state_signal
        )
        bus.signal_subscribe(
            "org.freedesktop.DBus",
            "org.freedesktop.DBus",
            "NameOwnerChanged",
            None,
            MM,
            Gio.DBusSignalFlags.NONE,
            lambda *_a: self.read_modems(),
        )
        self.read_modems()

    def read_modems(self):
        self.sys.call(
            MM,
            MM_PATH,
            OM,
            "GetManagedObjects",
            None,
            GLib.VariantType("(a{oa{sa{sv}}})"),
            Gio.DBusCallFlags.NO_AUTO_START,
            3000,
            None,
            self._got_modems,
        )

    def _got_modems(self, bus, result):
        try:
            objects = bus.call_finish(result).unpack()[0]
        except GLib.Error:
            objects = {}
        voices = {}
        for path, ifaces in objects.items():
            if VOICE in ifaces:
                voices[path] = list(ifaces[VOICE].get("Calls", []))
        self.voices = voices
        listed = {p for paths in voices.values() for p in paths}
        for path in list(self.calls):
            if path not in listed:
                del self.calls[path]
        for path in listed:
            self.read_call(path)
        self.evaluate()

    def read_call(self, path):
        self.sys.call(
            MM,
            path,
            PROPS,
            "Get",
            GLib.Variant("(ss)", (CALL, "State")),
            GLib.VariantType("(v)"),
            Gio.DBusCallFlags.NO_AUTO_START,
            3000,
            None,
            self._got_call,
            path,
        )

    def _got_call(self, bus, result, path):
        try:
            state = bus.call_finish(result).unpack()[0]
        except GLib.Error:
            self.calls.pop(path, None)
        else:
            self.calls[path] = int(state)
        self.evaluate()

    def call_props(self, _bus, _sender, path, _iface, _signal, params):
        _iface_name, changed, _inv = params.unpack()
        if "State" in changed and path in self.known_calls():
            self.calls[path] = int(changed["State"])
            self.evaluate()

    def call_state_signal(self, _bus, _sender, path, _iface, _signal, params):
        if path in self.known_calls():
            self.calls[path] = int(params.unpack()[1])
            self.evaluate()

    def known_calls(self):
        return {p for paths in self.voices.values() for p in paths}

    # -- callaudiod: the earpiece or the loudspeakers -------------------------------

    def watch_callaudio(self):
        self.ses.signal_subscribe(
            None,
            PROPS,
            "PropertiesChanged",
            CA_PATH,
            CA,
            Gio.DBusSignalFlags.NONE,
            self._callaudio_props,
        )
        self.ses.signal_subscribe(
            "org.freedesktop.DBus",
            "org.freedesktop.DBus",
            "NameOwnerChanged",
            None,
            CA,
            Gio.DBusSignalFlags.NONE,
            lambda *_a: self.read_speaker(),
        )
        self.read_speaker()

    def read_speaker(self):
        self.ses.call(
            CA,
            CA_PATH,
            PROPS,
            "Get",
            GLib.Variant("(ss)", (CA, "SpeakerState")),
            GLib.VariantType("(v)"),
            Gio.DBusCallFlags.NO_AUTO_START,
            3000,
            None,
            self._got_speaker,
        )

    def _got_speaker(self, bus, result):
        try:
            self.speaker = bool(bus.call_finish(result).unpack()[0])
        except GLib.Error:
            self.speaker = None
        self.evaluate()

    def _callaudio_props(self, _bus, _sender, _path, _iface, _signal, params):
        _name, changed, _inv = params.unpack()
        if "SpeakerState" in changed:
            self.speaker = bool(changed["SpeakerState"])
            self.evaluate()

    # -- iio-sensor-proxy -----------------------------------------------------------

    def watch_sensor(self):
        self.sys.signal_subscribe(
            None,
            PROPS,
            "PropertiesChanged",
            SP_PATH,
            SP,
            Gio.DBusSignalFlags.NONE,
            self._sensor_props,
        )
        self.sys.signal_subscribe(
            "org.freedesktop.DBus",
            "org.freedesktop.DBus",
            "NameOwnerChanged",
            None,
            SP,
            Gio.DBusSignalFlags.NONE,
            self._sensor_owner,
        )
        self.read_has_sensor()

    def read_has_sensor(self):
        self.sys.call(
            SP,
            SP_PATH,
            PROPS,
            "Get",
            GLib.Variant("(ss)", (SP, "HasProximity")),
            GLib.VariantType("(v)"),
            Gio.DBusCallFlags.NO_AUTO_START,
            3000,
            None,
            self._got_has_sensor,
        )

    def _got_has_sensor(self, bus, result):
        try:
            self.has_sensor = bool(bus.call_finish(result).unpack()[0])
        except GLib.Error:
            self.has_sensor = False
        self.evaluate()

    def _sensor_owner(self, _bus, _sender, _path, _iface, _signal, params):
        # The proxy (re)started: our claim is gone with the old one.
        self.claimed = self.claiming = False
        self.debounce.reset()
        self.read_has_sensor()

    def _sensor_props(self, _bus, _sender, _path, _iface, _signal, params):
        _name, changed, _inv = params.unpack()
        if "HasProximity" in changed:
            self.has_sensor = bool(changed["HasProximity"])
        if "ProximityNear" in changed and self.claimed:
            self.reading(bool(changed["ProximityNear"]))
            return
        self.evaluate()

    def claim(self, on):
        if on == self.claimed or self.claiming:
            return
        self.claiming = True
        method = "ClaimProximity" if on else "ReleaseProximity"
        self.sys.call(
            SP,
            SP_PATH,
            SP,
            method,
            None,
            None,
            Gio.DBusCallFlags.NO_AUTO_START,
            3000,
            None,
            self._claimed,
            on,
        )

    def _claimed(self, bus, result, on):
        self.claiming = False
        try:
            bus.call_finish(result)
        except GLib.Error as e:
            log("%s: %s" % ("claim" if on else "release", e.message))
            if on:
                return
        self.claimed = on
        log("sensor %s" % ("claimed (a call on the earpiece)" if on else "released"))
        if on:
            self.sys.call(
                SP,
                SP_PATH,
                PROPS,
                "Get",
                GLib.Variant("(ss)", (SP, "ProximityNear")),
                GLib.VariantType("(v)"),
                Gio.DBusCallFlags.NO_AUTO_START,
                3000,
                None,
                self._got_near,
            )
        else:
            self.debounce.reset()
        self.evaluate()

    def _got_near(self, bus, result):
        try:
            near = bool(bus.call_finish(result).unpack()[0])
        except GLib.Error:
            return
        if self.claimed:
            self.reading(near)

    def reading(self, near):
        wait = self.debounce.feed(near, GLib.get_monotonic_time() / 1e6)
        log("near" if near else "far")
        self.schedule_settle(wait)
        self.evaluate()

    def schedule_settle(self, wait):
        if self.settle_id is not None:
            GLib.source_remove(self.settle_id)
            self.settle_id = None
        if wait is not None:
            self.settle_id = GLib.timeout_add(int(wait * 1000) + 10, self._settle)

    def _settle(self):
        self.settle_id = None
        self.schedule_settle(self.debounce.settle(GLib.get_monotonic_time() / 1e6))
        self.evaluate()
        return False

    # -- the decision ------------------------------------------------------------------

    def wc_changed(self):
        self.evaluate()

    def evaluate(self):
        wanted = self.has_sensor and model.call_wants_sensor(self.calls.values(), self.speaker)
        self.claim(wanted)
        st = self.shell.wc_state or {}
        panels = model.hold_panels(
            wanted and self.claimed,
            self.debounce.near,
            st.get("windows"),
            st.get("lit"),
            st.get("proximity"),
        )
        if panels != self.sent:
            if self.failed is None:
                log("hold %s" % (", ".join(panels) or "none"))
            self.send(panels)
        if panels and self.renew_id is None:
            self.renew_id = GLib.timeout_add_seconds(model.RENEW_S, self._renew)
        elif not panels and self.renew_id is not None:
            GLib.source_remove(self.renew_id)
            self.renew_id = None

    def _renew(self):
        if not self.sent:
            self.renew_id = None
            return False
        self.send(self.sent)
        return True

    def send(self, panels):
        try:
            wc.request("set_proximity", panels=panels, ttl_ms=model.TTL_MS)
            self.sent = list(panels)
            self.failed = None
        except (OSError, RuntimeError, ValueError) as e:
            if str(e) != getattr(self, "failed", None):
                log("set_proximity: %s" % e)
            self.failed = str(e)
            # Nothing held for sure only when it said so: try again next time.
            self.sent = None
