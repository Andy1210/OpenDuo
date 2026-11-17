"""The SIM card's PIN lock, for the SIM card page.

ModemManager on the system bus, as the GUI user (polkit's Device.Control,
telephony/50-duo2-modemmanager.rules): the lock is Modem3gpp's
EnabledFacilityLocks ("sim"), the attempts left the modem's UnlockRetries
(sim-pin), the switches Sim.EnablePin / DisablePin / ChangePin. A PIN goes
to ModemManager and is dropped: never logged, stored or shown. A SIM that
is waiting for its PIN or PUK is the shell's (its PIN dialog), not this
page's.

lock_view() is pure (tests/test_settings.py). Testing without a SIM: with
root's /etc/duo2/shell-fake-modem the session bus is used, where
shell/duo-shell/tests/fake_mm.py plays ModemManager.
"""

import os

PIN_MIN, PIN_MAX = 4, 8
FAKE_SWITCH = "/etc/duo2/shell-fake-modem"
AIRPLANE = "/var/lib/duo2/airplane"


def valid_sim_pin(pin):
    return (
        isinstance(pin, str) and pin.isascii() and pin.isdigit() and PIN_MIN <= len(pin) <= PIN_MAX
    )


def lock_view(info):
    """What the page shows for `info` (SimLock.info()):

        modem     a modem is there         state    ModemManager's state name
        present   a SIM is in it           lock     what it waits for (sim-pin ...)
        facility  the PIN lock is on (None: unknown)
        left      PIN attempts left (None: unknown)

    -> {"status": a short state, "lock_on", "can_toggle", "can_change",
        "left"}. The lock can be switched with a ready SIM whose lock state
    is known and that has an attempt left."""
    info = info or {}
    view = {
        "status": "No modem",
        "lock_on": False,
        "can_toggle": False,
        "can_change": False,
        "left": None,
    }
    if not info.get("modem"):
        return view
    if not info.get("present"):
        view["status"] = "No SIM"
        return view
    left = info.get("left")
    view["left"] = left if isinstance(left, int) and left >= 0 else None
    facility = info.get("facility")
    view["lock_on"] = bool(facility)
    lock = info.get("lock")
    if info.get("state") == "locked" or lock in ("sim-pin", "sim-puk"):
        view["status"] = "Waiting for its PUK" if lock == "sim-puk" else "Waiting for its PIN"
        if lock in ("sim-pin", "sim-puk"):
            view["lock_on"] = True
        return view
    if info.get("state") in ("failed", "unknown", "initializing", None):
        view["status"] = "Not ready"
        return view
    view["status"] = "Ready"
    ok = facility is not None and (view["left"] is None or view["left"] > 0)
    view["can_toggle"] = ok
    view["can_change"] = ok and bool(facility)
    return view


def wrong_pin(message):
    """Whether ModemManager's error says the PIN was wrong."""
    m = (message or "").lower()
    return "incorrectpassword" in m or "incorrect password" in m


class SimLock:
    """The first modem of ModemManager; changed() when what the page shows
    may have changed. Never raises into GTK."""

    def __init__(self, changed):
        import gi

        gi.require_version("ModemManager", "1.0")
        from gi.repository import Gio, GLib
        from gi.repository import ModemManager as Mm

        self.Mm, self.GLib = Mm, GLib
        self.changed = changed
        self.manager = None
        self.obj = None
        self.handlers = []
        self.fake = os.path.exists(FAKE_SWITCH)
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SESSION if self.fake else Gio.BusType.SYSTEM, None)
        except GLib.Error:
            return
        Mm.Manager.new(bus, Gio.DBusObjectManagerClientFlags.DO_NOT_AUTO_START, None, self._made)

    def _made(self, _src, res):
        try:
            self.manager = self.Mm.Manager.new_finish(res)
        except self.GLib.Error:
            return
        for sig in ("object-added", "object-removed", "notify::name-owner"):
            self.manager.connect(sig, lambda *_a: self._pick())
        self._pick()

    def _pick(self):
        objs = []
        if self.manager is not None and self.manager.get_name_owner():
            objs = [o for o in self.manager.get_objects() if o.get_modem() is not None]
        obj = objs[0] if objs else None
        if obj is not self.obj:
            for target, h in self.handlers:
                try:
                    target.handler_disconnect(h)
                except TypeError:
                    pass
            self.handlers = []
            self.obj = obj
            if obj is not None:
                modem = obj.get_modem()
                for prop in ("state", "unlock-required", "unlock-retries", "sim"):
                    self.handlers.append(
                        (modem, modem.connect("notify::" + prop, lambda *_a: self.changed()))
                    )
                m3gpp = obj.get_modem_3gpp()
                if m3gpp is not None:
                    self.handlers.append(
                        (
                            m3gpp,
                            m3gpp.connect(
                                "notify::enabled-facility-locks", lambda *_a: self.changed()
                            ),
                        )
                    )
        self.changed()

    def close(self):
        self.manager = None
        self._pick()

    def info(self):
        Mm = self.Mm
        if self.obj is None:
            return {"modem": False}
        modem = self.obj.get_modem()
        states = {
            Mm.ModemState.FAILED: "failed",
            Mm.ModemState.UNKNOWN: "unknown",
            Mm.ModemState.INITIALIZING: "initializing",
            Mm.ModemState.LOCKED: "locked",
        }
        state = states.get(modem.get_state(), "ready")
        failed = modem.get_state_failed_reason() if state == "failed" else None
        present = not (state == "failed" and failed == Mm.ModemStateFailedReason.SIM_MISSING)
        required = modem.get_unlock_required()
        lock = Mm.modem_lock_get_string(required) if required else None
        left = None
        retries = modem.get_unlock_retries()
        if retries is not None:
            n = retries.get(Mm.ModemLock.SIM_PIN)
            if n != Mm.UNLOCK_RETRIES_UNKNOWN:
                left = n
        facility = None
        m3gpp = self.obj.get_modem_3gpp()
        if m3gpp is not None and state != "failed":
            facility = bool(m3gpp.get_enabled_facility_locks() & Mm.Modem3gppFacility.SIM)
        code = (m3gpp.get_operator_code() or "") if m3gpp is not None else ""
        return {
            "modem": True,
            "present": present,
            "state": state,
            "lock": lock,
            "facility": facility,
            "left": left,
            "network_code": code,
        }

    def _with_sim(self, then, done):
        if self.obj is None:
            done(False, "no modem")
            return
        GLib = self.GLib

        def got(m, res):
            try:
                sim = m.get_sim_finish(res)
            except GLib.Error as e:
                done(False, e.message)
                return
            if sim is None:
                done(False, "no SIM")
                return
            then(sim)

        self.obj.get_modem().get_sim(None, got)

    def _finish(self, method, done):
        GLib = self.GLib

        def finished(sim, res):
            try:
                getattr(sim, method + "_finish")(res)
            except GLib.Error as e:
                # ModemManager's message names the error, never the digits.
                print("duo-settings: SIM %s refused (%s)" % (method, e.message), flush=True)
                done(False, e.message)
                return
            done(True, None)
            self.changed()

        return finished

    def set_lock(self, pin, on, done):
        """The PIN lock on or off, with the current PIN; done(ok, error)."""
        method = "enable_pin" if on else "disable_pin"
        self._with_sim(
            lambda sim: getattr(sim, method)(pin, None, self._finish(method, done)), done
        )

    def change_pin(self, old, new, done):
        self._with_sim(
            lambda sim: sim.change_pin(old, new, None, self._finish("change_pin", done)), done
        )
