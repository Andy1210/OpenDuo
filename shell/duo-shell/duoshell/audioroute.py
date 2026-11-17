"""A Bluetooth or USB output that connects becomes the output
(audioroutemodel.py has the rules and why WirePlumber needs this).

Event driven: `pactl subscribe` (pipewire-pulse) says when a sink comes or
goes; a burst of events is one look, 300 ms later: `pactl -f json list
sinks` and the default sink, the router decides, `pactl set-default-sink`
does it. The subscription is started again if it ends (PipeWire restarted).
The log names the kind of output only, never a device's name or address.
"""

import json

from gi.repository import Gio, GLib

from .audioroutemodel import Router

SETTLE_MS = 300
RESTART_S = 2


def log(*args):
    print("duo-shell: audio:", *args, flush=True)


def _run(argv, done):
    """argv's stdout to done(text or None), without blocking."""
    try:
        proc = Gio.Subprocess.new(
            argv, Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_SILENCE
        )
    except GLib.Error:
        done(None)
        return

    def finish(p, res):
        try:
            _ok, out, _err = p.communicate_utf8_finish(res)
        except GLib.Error:
            out = None
        done(out if p.get_successful() else None)

    proc.communicate_utf8_async(None, None, finish)


class AudioRoute:
    def __init__(self):
        self.router = Router()
        self.proc = None
        self.stream = None
        self.timer = None
        self.looking = False
        self.again = False
        self.start()

    def start(self):
        try:
            self.proc = Gio.Subprocess.new(
                ["pactl", "subscribe"],
                Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_SILENCE,
            )
        except GLib.Error as e:
            log("no pactl subscribe (%s); trying again in %d s" % (e.message, RESTART_S))
            GLib.timeout_add_seconds(RESTART_S, self._restart)
            return
        self.stream = Gio.DataInputStream.new(self.proc.get_stdout_pipe())
        self.stream.read_line_async(GLib.PRIORITY_DEFAULT, None, self._line)
        self.schedule()  # the first look: what is there now stays as it is

    def _restart(self):
        self.start()
        return False

    def _line(self, stream, res):
        try:
            line, _length = stream.read_line_finish_utf8(res)
        except GLib.Error:
            line = None
        if line is None:
            log("pactl subscribe ended; again in %d s" % RESTART_S)
            self.proc = self.stream = None
            GLib.timeout_add_seconds(RESTART_S, self._restart)
            return
        if " on sink #" in line or " on server" in line:
            self.schedule()
        stream.read_line_async(GLib.PRIORITY_DEFAULT, None, self._line)

    def schedule(self):
        if self.timer is None:
            self.timer = GLib.timeout_add(SETTLE_MS, self._due)

    def _due(self):
        self.timer = None
        if self.looking:
            self.again = True  # one more look when this one is done
        else:
            self.look()
        return False

    def look(self):
        self.looking = True
        got = {}

        def sinks(out):
            try:
                got["sinks"] = json.loads(out) if out else None
            except ValueError:
                got["sinks"] = None
            _run(["pactl", "get-default-sink"], default)

        def default(out):
            self.looking = False
            if isinstance(got.get("sinks"), list):
                self.apply(self.router.update(got["sinks"], (out or "").strip()))
            if self.again:
                self.again = False
                self.schedule()

        _run(["pactl", "-f", "json", "list", "sinks"], sinks)

    def apply(self, choice):
        if choice is None:
            return
        name, kind = choice
        log(
            "a %s output connected: it is the output now"
            % ("Bluetooth" if kind == "bluetooth" else "USB")
        )
        _run(
            ["pactl", "set-default-sink", name],
            lambda out: out is None and log("could not make it the default"),
        )
