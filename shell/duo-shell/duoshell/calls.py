"""Incoming calls: the screen lights, the lock screen shows the call
(callsmodel.py decides, lockclient.py draws the card).

GNOME Calls (`gnome-calls --daemon`, the user's duo-calls-daemon.service)
rings (feedbackd) and shows its call window by itself, but it sends no
notification for a call that rings in and it cannot light the screen; a
locked session shows nothing but the lock. So the shell watches Calls' own
call objects on the session bus (what Phosh's lock screen reads), nothing
polled:

- a call that starts ringing lights the panels (Shell.wake_for_call: a
  closed phone shows the glance strip; a phone woken from suspend by the
  call is the same case, the shell runs on as soon as the kernel is up);
- the lock screen gets the call card (Shell.call_card_changed), and its
  buttons come back here: Accept / Hangup on the call, the speaker through
  duo2-callaudio (callaudiod's interface);
- the glance and peek strips say who calls while it rings
  (Shell.incoming_caller_changed);
- the power key or a volume key silences a call that rings aloud
  (silence): Calls' own Silence, whose ringer then ends feedbackd's
  phone-incoming-call event (the ringtone and the vibration together); the
  call goes on ringing silently. duo-wc is told how it rings (`set_ring`,
  renewed while it does): while `loud` its power key silences instead of
  switching the screen (`ring_silence` comes back here), and while a call
  rings at all a glance stays lit and the screen does not blank by itself.

Do not disturb (dndmodel.py): Calls' ringer is silenced then (dnd.py);
a call that rings in is taken as silenced (it lights nothing, the keys need
not silence it), unless an exception lets it through (a repeated caller, a
contact): then the shell rings it itself (feedbackd's phone-incoming-call,
the same ringtone and sound mode) until it stops ringing or a key silences
it, and lights the screen as usual.

An SMS lights nothing: it is a notification (notifications.py), not a call.
The caller's number and name never go into the log.
"""

import time

from gi.repository import Gio, GLib

from . import callsmodel as model
from . import dndmodel, wc

OM = "org.freedesktop.DBus.ObjectManager"
FB = ("org.sigxcpu.Feedback", "/org/sigxcpu/Feedback", "org.sigxcpu.Feedback")
SHELL_APP_ID = "org.duo2os.Shell"
PROPS = "org.freedesktop.DBus.Properties"
CA = "org.mobian_project.CallAudio"
CA_PATH = "/org/mobian_project/CallAudio"


def log(*args):
    print("duo-shell: calls:", *args, flush=True)


def _plain(value):
    return value.unpack() if isinstance(value, GLib.Variant) else value


class CallWatch:
    def __init__(self, shell):
        self.shell = shell
        self.calls = {}  # path -> callsmodel call
        self.since = {}  # path -> wall-clock time it came up
        self.ringing = set()
        self.silenced = set()  # paths silenced here (Calls does not say)
        self.ring = None  # what duo-wc was told: "loud", "silent", None
        self.ring_failed = None
        self.ring_renew = None
        self.strip = None  # who calls, for the strips
        self.speaker = False
        self.card = None
        self.history = {}  # caller -> when it last rang (do not disturb's repeat rule; memory only)
        self.dnd_rings = {}  # path -> the shell's own feedbackd event (a call let through)
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        bus = self.bus
        bus.signal_subscribe(
            model.BUS,
            OM,
            "InterfacesAdded",
            model.PATH,
            None,
            Gio.DBusSignalFlags.NONE,
            self._added,
        )
        bus.signal_subscribe(
            model.BUS,
            OM,
            "InterfacesRemoved",
            model.PATH,
            None,
            Gio.DBusSignalFlags.NONE,
            self._removed,
        )
        bus.signal_subscribe(
            model.BUS,
            PROPS,
            "PropertiesChanged",
            None,
            model.IFACE,
            Gio.DBusSignalFlags.NONE,
            self._props,
        )
        bus.signal_subscribe(
            "org.freedesktop.DBus",
            "org.freedesktop.DBus",
            "NameOwnerChanged",
            None,
            model.BUS,
            Gio.DBusSignalFlags.NONE,
            lambda *_a: self.read(),
        )
        bus.signal_subscribe(
            None,
            PROPS,
            "PropertiesChanged",
            CA_PATH,
            CA,
            Gio.DBusSignalFlags.NONE,
            self._callaudio_props,
        )
        self.read()
        self.read_speaker()
        # A hold left by a shell before this one (it would run out by itself).
        self._request_ring()

    # -- Calls' objects -----------------------------------------------------------

    def read(self):
        self.bus.call(
            model.BUS,
            model.PATH,
            OM,
            "GetManagedObjects",
            None,
            GLib.VariantType("(a{oa{sa{sv}}})"),
            Gio.DBusCallFlags.NO_AUTO_START,
            3000,
            None,
            self._got,
        )

    def _got(self, bus, result):
        try:
            objects = bus.call_finish(result).unpack()[0]
        except GLib.Error:
            objects = {}  # Calls is not running (yet)
        calls = {}
        for path, ifaces in objects.items():
            if model.IFACE in ifaces:
                calls[path] = model.call_from_props(ifaces[model.IFACE])
        self.calls = calls
        self.changed()

    def _added(self, _bus, _sender, _path, _iface, _signal, params):
        path, ifaces = params.unpack()
        if model.IFACE in ifaces:
            self.calls[path] = model.call_from_props(ifaces[model.IFACE])
            self.changed()

    def _removed(self, _bus, _sender, _path, _iface, _signal, params):
        path, ifaces = params.unpack()
        if model.IFACE in ifaces and self.calls.pop(path, None) is not None:
            self.changed()

    def _props(self, _bus, _sender, path, _iface, _signal, params):
        iface, changed, _invalidated = params.unpack()
        if iface != model.IFACE or path not in self.calls:
            return
        self.calls[path] = model.call_from_props(
            {k: _plain(v) for k, v in changed.items()}, self.calls[path]
        )
        self.changed()

    def changed(self):
        model.note_since(self.calls, self.since, time.time())
        model.forget_silenced(self.calls, self.silenced)
        ring = set(model.ringing(self.calls))
        new = ring - self.ringing
        self.ringing = ring
        quiet = self.do_not_disturb(new)
        for path in list(self.dnd_rings):
            if path not in ring:
                self.end_ring(path)
        # duo-wc's ring hold and the strips' caller first: the glance that
        # the wake below starts is held for the ring and shows who calls.
        state = model.ring_state(self.calls, self.silenced)
        if state != self.ring:
            self.ring = state
            self.send_ring()
        strip = model.strip_caller(self.calls)
        if strip != self.strip:
            self.strip = strip
            self.shell.incoming_caller_changed(strip)
        if new - quiet:
            log("a call rings in: lighting the screen")
            self.shell.wake_for_call()
        card = model.card(self.calls, self.since)
        if card != self.card:
            self.card = card
            self.shell.call_card_changed()

    # -- do not disturb ----------------------------------------------------------------------

    def do_not_disturb(self, new):
        """The calls that started ringing: noted for the repeat rule; while
        do not disturb is on, each either rings through the shell (an
        exception) or is taken as silenced. Returns the quiet ones."""
        quiet = set()
        dnd = getattr(self.shell, "dnd", None)
        now = time.time()
        prefs = dnd.prefs() if dnd is not None and dnd.on else None
        for path in sorted(new):
            call = self.calls[path]
            if prefs is not None:
                why = dndmodel.allowed(call, self.history, prefs, now)
                if why:
                    log("do not disturb: a call rings through (%s)" % why)
                    self.start_ring(path)
                else:
                    log("do not disturb: a call rings in silently")
                    self.silenced.add(path)
                    quiet.add(path)
            dndmodel.remember(self.history, call, now)
        return quiet

    def start_ring(self, path):
        """The shell rings a call itself (Calls' ringer is silenced):
        feedbackd loops the event until it is ended (timeout 0)."""
        self.dnd_rings[path] = None
        self.bus.call(
            *FB,
            "TriggerFeedback",
            GLib.Variant("(ssa{sv}i)", (SHELL_APP_ID, "phone-incoming-call", {}, 0)),
            GLib.VariantType("(u)"),
            Gio.DBusCallFlags.NONE,
            3000,
            None,
            self._ring_started,
            path,
        )

    def _ring_started(self, bus, result, path):
        try:
            event = bus.call_finish(result).unpack()[0]
        except GLib.Error as e:
            log("ringing through do not disturb: %s" % e.message)
            self.dnd_rings.pop(path, None)
            return
        if path in self.dnd_rings:
            self.dnd_rings[path] = event
        else:
            self._end_event(event)  # it stopped ringing meanwhile

    def end_ring(self, path):
        if path in self.dnd_rings:
            event = self.dnd_rings.pop(path)
            if event is not None:
                self._end_event(event)

    def _end_event(self, event):
        self.bus.call(
            *FB,
            "EndFeedback",
            GLib.Variant("(u)", (event,)),
            None,
            Gio.DBusCallFlags.NONE,
            3000,
            None,
            self._done,
            "end ring",
        )

    # -- silencing the ringing -----------------------------------------------------------

    def loud(self):
        """Whether a key would silence a call that rings now."""
        return bool(model.loud(self.calls, self.silenced))

    def silence(self, why):
        """The power or a volume key while a call rings aloud: Calls stops
        the ringtone and the vibration (the call goes on ringing silently,
        it can still be answered). Whether there was one to silence."""
        paths = model.loud(self.calls, self.silenced)
        if not paths:
            return False
        log("silencing the ringing (%s)" % why)
        for path in paths:
            self.silenced.add(path)
            self.end_ring(path)
            self.bus.call(
                model.BUS,
                path,
                model.IFACE,
                "Silence",
                None,
                None,
                Gio.DBusCallFlags.NO_AUTO_START,
                5000,
                None,
                self._done,
                "silence",
            )
        self.changed()
        return True

    def send_ring(self):
        """Tell duo-wc how a call rings now; renewed while one does (its
        hold runs out by itself RING_TTL_MS after the last renewal)."""
        self._request_ring()
        if self.ring and self.ring_renew is None:
            self.ring_renew = GLib.timeout_add_seconds(model.RING_RENEW_S, self._renew_ring)
        elif not self.ring and self.ring_renew is not None:
            GLib.source_remove(self.ring_renew)
            self.ring_renew = None

    def _renew_ring(self):
        if not self.ring:
            self.ring_renew = None
            return False
        self._request_ring()
        return True

    def _request_ring(self):
        try:
            wc.request("set_ring", ring=self.ring or "off", ttl_ms=model.RING_TTL_MS)
            if self.ring_failed is not None:
                log("set_ring works again")
            self.ring_failed = None
        except (OSError, RuntimeError, ValueError) as e:
            # An older duo-wc: the volume keys still silence, the power key
            # switches the screen as before.
            if str(e) != self.ring_failed:
                log("set_ring: %s" % e)
            self.ring_failed = str(e)

    # -- the speaker (duo2-callaudio) --------------------------------------------------

    def read_speaker(self):
        self.bus.call(
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
            speaker = bool(bus.call_finish(result).unpack()[0])
        except GLib.Error:
            speaker = False
        self.set_speaker_state(speaker)

    def _callaudio_props(self, _bus, _sender, _path, _iface, _signal, params):
        _name, changed, _inv = params.unpack()
        if "SpeakerState" in changed:
            self.set_speaker_state(bool(_plain(changed["SpeakerState"])))

    def set_speaker_state(self, on):
        if on != self.speaker:
            self.speaker = on
            if self.card is not None:
                self.shell.call_card_changed()

    # -- what the lock screen's buttons do ------------------------------------------------

    def message(self):
        """What the lock is sent: the card and the speaker."""
        return {"card": self.card, "speaker": self.speaker}

    def act(self, action, path, on=False):
        if path not in self.calls:
            return
        if action == "speaker":
            log("speaker %s (lock screen)" % ("on" if on else "off"))
            self.bus.call(
                CA,
                CA_PATH,
                CA,
                "EnableSpeaker",
                GLib.Variant("(b)", (bool(on),)),
                None,
                Gio.DBusCallFlags.NO_AUTO_START,
                5000,
                None,
                self._done,
                action,
            )
            return
        method = {"accept": "Accept", "hangup": "Hangup"}.get(action)
        if method is None:
            return
        log("%s (lock screen)" % method)
        self.bus.call(
            model.BUS,
            path,
            model.IFACE,
            method,
            None,
            None,
            Gio.DBusCallFlags.NO_AUTO_START,
            10000,
            None,
            self._done,
            action,
        )

    def _done(self, bus, result, action):
        try:
            bus.call_finish(result)
        except GLib.Error as e:
            log("%s failed: %s" % (action, e.message))


class LockChannel:
    """The shell's end of the call card's socketpair with one duo-lock: the
    card goes out as a line, the lock's buttons come back as lines
    (`on_line`). The other end is passed to duo-lock (pass_fds, its number in
    ENV) and closed here once it runs (child_started)."""

    ENV = "DUO_LOCK_CALL_FD"
    MAX_LINE = 4096

    def __init__(self, on_line):
        import socket

        self.on_line = on_line
        self.sock, child = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.setblocking(False)
        self.child = child
        self.child_fd = child.fileno()  # pass_fds makes it inheritable in duo-lock
        self.buf = b""
        self.watch = GLib.io_add_watch(
            self.sock.fileno(),
            GLib.PRIORITY_DEFAULT,
            GLib.IO_IN | GLib.IO_HUP | GLib.IO_ERR,
            self._readable,
        )

    def child_started(self):
        if self.child is not None:
            self.child.close()
            self.child = None

    def send(self, message):
        if self.sock is None:
            return
        try:
            self.sock.send(model.encode(message))
        except (BlockingIOError, InterruptedError):
            pass  # the lock is not reading: the next change goes again
        except OSError:
            self.close()

    def _readable(self, _fd, condition):
        try:
            data = self.sock.recv(self.MAX_LINE)
        except (BlockingIOError, InterruptedError):
            return True
        except OSError:
            data = b""
        if not data:
            self.watch = None
            self.close()
            return False
        self.buf += data
        while b"\n" in self.buf:
            line, self.buf = self.buf.split(b"\n", 1)
            try:
                self.on_line(line.decode("utf-8", "replace"))
            except Exception as e:  # a bad line must not break the shell
                log("lock line: %r" % e)
        if len(self.buf) > self.MAX_LINE:
            self.buf = b""
        return True

    def close(self):
        self.child_started()
        if self.watch is not None:
            GLib.source_remove(self.watch)
            self.watch = None
        if self.sock is not None:
            self.sock.close()
            self.sock = None
