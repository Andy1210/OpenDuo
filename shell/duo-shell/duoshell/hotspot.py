"""The hotspot from the shell (hotspot/README.md): duo2-hotspot through sudo
with the shell's one-time grant on its standard input (the helper redeems it
on duo-wc's admin socket: an app without the shell's trusted connection gets
none and the user is asked instead), and its state file watched."""

from gi.repository import Gio, GLib

from . import hotspotmodel

# The helper's words that need a grant, and the grant's action (the same
# words as duo2-hotspot's grant_action).
GRANTED = {
    ("on",): "hotspot on",
    ("on", "--wifi-off"): "hotspot on wifi-off",
    ("wifi-client", "on"): "hotspot wifi-client on",
}


def _grant(words):
    action = GRANTED.get(tuple(words))
    if not action:
        return ""
    from . import wc

    try:
        return (wc.request("grant", action=action) or {}).get("token", "")
    except (OSError, RuntimeError, ValueError) as e:
        print("duo-shell: no grant for %s: %s" % (action, e), flush=True)
        return ""


def run(words, done=None):
    """`sudo -n duo2-hotspot WORDS` without blocking GTK; done(reply dict)."""
    argv = ["sudo", "-n", hotspotmodel.HELPER] + list(words)
    token = _grant(words)
    try:
        proc = Gio.Subprocess.new(
            argv,
            Gio.SubprocessFlags.STDIN_PIPE
            | Gio.SubprocessFlags.STDOUT_PIPE
            | Gio.SubprocessFlags.STDERR_PIPE,
        )
    except GLib.Error as e:
        print("duo-shell: %s: %s" % (" ".join(argv), e.message), flush=True)
        if done:
            done({"ok": False, "error": e.message})
        return

    def finished(p, result):
        try:
            _ok, out, err = p.communicate_utf8_finish(result)
        except GLib.Error as e:
            out, err = "", e.message
        reply = hotspotmodel.parse_reply(out)
        if not reply.get("ok"):
            print(
                "duo-shell: hotspot %s: %s %s"
                % (
                    " ".join(words),
                    reply.get("code") or "",
                    reply.get("error") or (err or "").strip(),
                ),
                flush=True,
            )
        if done:
            done(reply)

    proc.communicate_utf8_async(token + "\n", None, finished)


class Watch:
    """Calls changed() when the hotspot's state file changes."""

    def __init__(self, changed, path=hotspotmodel.STATE):
        self.changed = changed
        try:
            self.monitor = Gio.File.new_for_path(path).monitor_file(Gio.FileMonitorFlags.NONE, None)
            self.monitor.connect("changed", lambda *a: self.changed())
        except GLib.Error as e:
            print("duo-shell: hotspot state not watched: %s" % e.message, flush=True)
            self.monitor = None
