"""ModemManager for the shell: the SIM's lock, the signal, the operator,
the PIN (mobilemodel.py decides what is shown).

The ModemManager GI on the system bus (gir1.2-modemmanager-1.0, as
duo2-wwan); the GUI user may send a PIN (polkit's Device.Control,
telephony/50-duo2-modemmanager.rules). Event driven: ModemManager's
properties and its object manager, nothing polled. A PIN is passed to
Sim.SendPin / SendPuk and dropped: never logged, stored or shown.

Testing without a SIM: when /etc/duo2/shell-fake-modem exists (root's
file: an app cannot switch it on), the session bus is used instead, where
tests/fake_mm.py plays ModemManager.
"""

import os

import gi

gi.require_version("ModemManager", "1.0")
from gi.repository import Gio, GLib  # noqa: E402
from gi.repository import ModemManager as Mm  # noqa: E402

FAKE_SWITCH = "/etc/duo2/shell-fake-modem"
START_GRACE = 3  # s: GNOME Calls starts an outgoing call it sees
DATA_SWITCH = "/var/lib/duo2/mobile-data"
AIRPLANE_SWITCH = "/var/lib/duo2/airplane"
WWAN_CONF = "/etc/duo2/wwan.conf"

STATES = {
    Mm.ModemState.FAILED: "failed",
    Mm.ModemState.UNKNOWN: "unknown",
    Mm.ModemState.INITIALIZING: "initializing",
    Mm.ModemState.LOCKED: "locked",
    Mm.ModemState.DISABLED: "disabled",
    Mm.ModemState.DISABLING: "disabling",
    Mm.ModemState.ENABLING: "enabling",
    Mm.ModemState.ENABLED: "enabled",
    Mm.ModemState.SEARCHING: "searching",
    Mm.ModemState.REGISTERED: "registered",
    Mm.ModemState.DISCONNECTING: "disconnecting",
    Mm.ModemState.CONNECTING: "connecting",
    Mm.ModemState.CONNECTED: "connected",
}
R = Mm.Modem3gppRegistrationState
REGISTRATION = {
    R.HOME: "home",
    R.HOME_CSFB_NOT_PREFERRED: "home",
    R.HOME_SMS_ONLY: "home",
    R.ROAMING: "roaming",
    R.ROAMING_CSFB_NOT_PREFERRED: "roaming",
    R.ROAMING_SMS_ONLY: "roaming",
    R.SEARCHING: "searching",
    R.IDLE: "idle",
    R.DENIED: "denied",
    R.UNKNOWN: "unknown",
}
TECHS = [
    (Mm.ModemAccessTechnology.LTE, "lte"),
    (Mm.ModemAccessTechnology.UMTS, "umts"),
    (Mm.ModemAccessTechnology.HSPA_PLUS, "hspa-plus"),
    (Mm.ModemAccessTechnology.HSPA, "hspa"),
    (Mm.ModemAccessTechnology.HSDPA, "hsdpa"),
    (Mm.ModemAccessTechnology.EDGE, "edge"),
    (Mm.ModemAccessTechnology.GPRS, "gprs"),
    (Mm.ModemAccessTechnology.GSM, "gsm"),
]
_NR = getattr(Mm.ModemAccessTechnology, "5GNR", None)
if _NR is not None:
    TECHS.insert(0, (_NR, "5gnr"))


def log(*args):
    print("duo-shell: mobile:", *args, flush=True)


def read_airplane():
    try:
        with open(AIRPLANE_SWITCH) as f:
            return f.read().strip().lower() == "on"
    except OSError:
        return False


def read_data_switch():
    """on / off: the switch file (the quick menu's), else wwan.conf's DATA."""
    try:
        with open(DATA_SWITCH) as f:
            value = f.read().strip().lower()
            if value in ("on", "off"):
                return value
    except OSError:
        pass
    try:
        with open(WWAN_CONF) as f:
            for line in f:
                line = line.split("#", 1)[0].strip()
                if line.startswith("DATA="):
                    value = line.split("=", 1)[1].strip().strip("'\"").lower()
                    return "on" if value in ("1", "yes", "true", "on") else "off"
    except OSError:
        pass
    return "on"


class Mobile:
    """Follows the first modem; `changed()` is called when what the shell
    shows may have changed."""

    def __init__(self, changed):
        self.changed = changed
        self.manager = None
        self.obj = None
        self.handlers = []
        self.sim_operator = ""
        self.sim_mcc = ""
        self.sim_numbers = ()
        self.data = read_data_switch()
        self.fake = os.path.exists(FAKE_SWITCH)
        bus_type = Gio.BusType.SESSION if self.fake else Gio.BusType.SYSTEM
        if self.fake:
            log("using the session bus (%s): a fake ModemManager for testing" % FAKE_SWITCH)
        try:
            bus = Gio.bus_get_sync(bus_type, None)
        except GLib.Error as e:
            log("no bus: %s" % e.message)
            return
        Mm.Manager.new(bus, Gio.DBusObjectManagerClientFlags.DO_NOT_AUTO_START, None, self._made)
        self.monitor = None
        try:
            self.monitor = Gio.File.new_for_path(DATA_SWITCH).monitor_file(
                Gio.FileMonitorFlags.NONE, None
            )
            self.monitor.connect("changed", lambda *_a: self._data_changed())
        except GLib.Error:
            pass

    def _made(self, _src, res):
        try:
            self.manager = Mm.Manager.new_finish(res)
        except GLib.Error as e:
            log("ModemManager: %s" % e.message)
            return
        self.manager.connect("object-added", lambda *_a: self._pick())
        self.manager.connect("object-removed", lambda *_a: self._pick())
        self.manager.connect("notify::name-owner", lambda *_a: self._pick())
        self._pick()

    def _data_changed(self):
        self.data = read_data_switch()
        self.changed()

    # -- the modem ----------------------------------------------------------------------

    def _pick(self):
        objs = []
        if self.manager is not None and self.manager.get_name_owner():
            objs = [o for o in self.manager.get_objects() if o.get_modem() is not None]
        obj = objs[0] if objs else None
        if obj is not self.obj:
            self._forget()
            self.obj = obj
            if obj is not None:
                self._watch(obj)
        self.changed()

    def _forget(self):
        for target, handler in self.handlers:
            try:
                target.disconnect(handler)
            except TypeError:
                pass
        self.handlers = []
        self.sim_operator = ""
        self.sim_mcc = ""
        self.sim_numbers = ()

    def _watch(self, obj):
        modem = obj.get_modem()
        for prop in (
            "state",
            "unlock-required",
            "unlock-retries",
            "signal-quality",
            "access-technologies",
            "sim",
            "state-failed-reason",
        ):
            self.handlers.append(
                (modem, modem.connect("notify::" + prop, lambda *_a: self._modem_changed()))
            )
        m3gpp = obj.get_modem_3gpp()
        if m3gpp is not None:
            for prop in ("operator-name", "operator-code", "registration-state"):
                self.handlers.append(
                    (m3gpp, m3gpp.connect("notify::" + prop, lambda *_a: self.changed()))
                )
        self._read_sim()

    def _modem_changed(self):
        self._read_sim()
        self.changed()

    def _read_sim(self):
        if self.obj is None:
            return
        modem = self.obj.get_modem()
        path = modem.dup_sim_path()
        if not path or path == "/":
            return
        target = self.obj

        def got(m, res):
            try:
                sim = m.get_sim_finish(res)
            except GLib.Error:
                return
            if target is self.obj and sim is not None:
                # For emergency calls: the SIM's country and its own list.
                self.sim_mcc = (sim.get_operator_identifier() or "")[:3]
                dup = getattr(sim, "dup_emergency_numbers", None)
                self.sim_numbers = tuple(dup() or ()) if dup else ()
                name = sim.get_operator_name() or ""
                if name != self.sim_operator:
                    self.sim_operator = name
                    self.changed()

        modem.get_sim(None, got)

    def info(self):
        """The modem as mobilemodel.py takes it."""
        if self.obj is None:
            return {"present": False}
        modem = self.obj.get_modem()
        state = STATES.get(modem.get_state(), "unknown")
        failed = None
        if state == "failed":
            failed = Mm.modem_state_failed_reason_get_string(modem.get_state_failed_reason())
        lock = (
            Mm.modem_lock_get_string(modem.get_unlock_required())
            if modem.get_unlock_required()
            else None
        )
        retries = {}
        unlock = modem.get_unlock_retries()
        if unlock is not None:
            for kind in (
                Mm.ModemLock.SIM_PIN,
                Mm.ModemLock.SIM_PUK,
                Mm.ModemLock.SIM_PIN2,
                Mm.ModemLock.SIM_PUK2,
            ):
                n = unlock.get(kind)
                if n != Mm.UNLOCK_RETRIES_UNKNOWN:
                    retries[Mm.modem_lock_get_string(kind)] = n
        quality, _recent = modem.get_signal_quality()
        techs = modem.get_access_technologies()
        tech = next((name for bit, name in TECHS if techs & bit), "")
        m3gpp = self.obj.get_modem_3gpp()
        operator = code = ""
        registration = "unknown"
        if m3gpp is not None:
            operator = m3gpp.get_operator_name() or ""
            code = m3gpp.get_operator_code() or ""
            registration = REGISTRATION.get(m3gpp.get_registration_state(), "unknown")
        return {
            "present": not (state == "failed" and failed == "sim-missing"),
            "state": state,
            "failed": failed,
            "lock": lock,
            "retries": retries,
            "quality": quality
            if state in ("registered", "connected", "connecting", "disconnecting")
            else None,
            "operator": operator,
            "sim_operator": self.sim_operator,
            "code": code,
            "registration": registration,
            "tech": tech,
            "data": self.data,
            "connected": state == "connected",
        }

    # -- the PIN ----------------------------------------------------------------------

    def send(self, request, done):
        """Send the dialog's request: ("pin", PIN) or ("puk", PUK, NEW_PIN);
        done(ok, error-message-or-None). The digits go to ModemManager only."""
        if self.obj is None:
            done(False, "no modem")
            return
        modem = self.obj.get_modem()

        def with_sim(m, res):
            try:
                sim = m.get_sim_finish(res)
            except GLib.Error as e:
                done(False, e.message)
                return
            if request[0] == "pin":
                sim.send_pin(request[1], None, finished)
            else:
                sim.send_puk(request[1], request[2], None, finished)

        def finished(sim, res):
            try:
                if request[0] == "pin":
                    sim.send_pin_finish(res)
                else:
                    sim.send_puk_finish(res)
            except GLib.Error as e:
                # ModemManager's message names the error, never the digits.
                log("the SIM refused the %s (%s)" % (request[0].upper(), e.message))
                done(False, e.message)
                return
            log("SIM unlocked with its %s" % request[0].upper())
            done(True, None)

        modem.get_sim(None, with_sim)

    # -- emergency calls ----------------------------------------------------------------

    def emergency_context(self):
        """What emergencymodel.py needs: whether a SIM is in, its own
        emergency numbers, the network's and the SIM's country codes."""
        info = self.info()
        registered = info.get("state") in ("registered", "connecting", "connected", "disconnecting")
        airplane = read_airplane()
        return {
            "sim_present": info.get("present", False) and info.get("failed") != "sim-missing",
            "sim_numbers": self.sim_numbers,
            "network_mcc": (info.get("code") or "")[:3],
            "sim_mcc": self.sim_mcc,
            "emergency_only": not registered and not airplane,
        }

    def dial(self, number, done=None):
        """An emergency call from the lock screen or the SIM PIN dialog,
        straight to ModemManager (the GUI user may: polkit's Voice). GNOME
        Calls sees the call, starts it and shows it (the lock screen's call
        card follows Calls); if nobody started it after START_GRACE s (no
        Calls), it is started here. With the radio off (airplane mode, a
        SIM-less modem) duo2-wwan switches it on and dials once more if
        this attempt failed for it (telephony/README.md "Emergency calls")."""
        done = done or (lambda ok: None)
        voice = self.obj.get_modem_voice() if self.obj is not None else None
        if voice is None:
            log("emergency call to %s: no modem with voice" % number)
            done(False)
            return
        props = Mm.CallProperties.new()
        props.set_number(number)

        def created(v, res):
            try:
                call = v.create_call_finish(res)
            except GLib.Error as e:
                log("emergency call to %s: %s" % (number, e.message))
                done(False)
                return
            log("emergency call to %s" % number)
            done(True)
            GLib.timeout_add_seconds(START_GRACE, lambda: start(call))

        def start(call):
            if call.get_state() == Mm.CallState.UNKNOWN:
                log("emergency call to %s: nobody started it, starting it" % number)
                call.start(None, started)
            return False

        def started(call, res):
            try:
                call.start_finish(res)
            except GLib.Error as e:
                log("emergency call to %s: %s" % (number, e.message))

        voice.create_call(props, None, created)
