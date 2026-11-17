"""The volume keys: the default sink's volume, and its display.

duo-wc announces the keys (volume.py has the rules). Each press changes
the volume at once in the shell's own copy of it, so the display follows
the finger, and wpctl is called in the background, one call at a time (a
held key queues only its latest value). The copy is read back from wpctl
on a press that comes more than 2 s after the last one.

The display is a pill on the overlay layer below the status bar of the
panel duo-wc names (the active one), icon, bar and per cent; it goes 1.5 s
after the last change, and its input region is empty (touches pass
through). While the screen is dark duo-wc names no panel: the volume
changes, nothing shows, nothing wakes (as on a phone).
"""

import subprocess
import time

import gi

gi.require_foreign("cairo")  # python3-gi-cairo: Gdk.Surface.set_input_region

import cairo  # noqa: E402
from gi.repository import Gio, GLib, Gtk  # noqa: E402
from gi.repository import Gtk4LayerShell as LayerShell  # noqa: E402

from . import volume  # noqa: E402

SHOW_MS = 1500
REREAD_AFTER_S = 2.0


def log(*args):
    print("duo-shell:", *args, flush=True)


class VolumeOsd(Gtk.Window):
    """One panel's volume display."""

    def __init__(self, app, monitor):
        super().__init__(application=app)
        self.monitor = monitor
        self.timer = None
        self.add_css_class("duo-volume")
        self.set_decorated(False)
        LayerShell.init_for_window(self)
        LayerShell.set_namespace(self, "duo-volume")
        LayerShell.set_layer(self, LayerShell.Layer.OVERLAY)
        LayerShell.set_monitor(self, monitor)
        LayerShell.set_anchor(self, LayerShell.Edge.TOP, True)
        LayerShell.set_margin(self, LayerShell.Edge.TOP, 60)
        LayerShell.set_keyboard_mode(self, LayerShell.KeyboardMode.NONE)
        LayerShell.set_exclusive_zone(self, -1)
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        box.add_css_class("volume-pill")
        self.icon = Gtk.Image()
        self.bar = Gtk.LevelBar()
        self.bar.set_min_value(0.0)
        self.bar.set_max_value(1.0)
        self.bar.set_hexpand(True)
        self.bar.set_valign(Gtk.Align.CENTER)
        # No coloured "low / high / full" offsets: one colour, the level.
        for name in ("low", "high", "full"):
            self.bar.remove_offset_value(name)
        self.label = Gtk.Label()
        self.label.add_css_class("percent")
        self.label.set_width_chars(4)
        self.label.set_xalign(1.0)
        box.append(self.icon)
        box.append(self.bar)
        box.append(self.label)
        self.set_child(box)
        self.set_default_size(260, -1)
        self.connect("realize", self._see_through)

    def _see_through(self, *_args):
        surface = self.get_surface()
        if surface is not None:
            surface.set_input_region(cairo.Region())

    def show_level(self, level, muted):
        self.icon.set_from_icon_name(volume.icon_name(level, muted))
        self.bar.set_value(0.0 if muted else level)
        self.label.set_text("%d %%" % volume.percent(level) if not muted else "0 %")
        if muted:
            self.add_css_class("muted")
        else:
            self.remove_css_class("muted")
        self.present()
        if self.timer is not None:
            GLib.source_remove(self.timer)
        self.timer = GLib.timeout_add(SHOW_MS, self._expired)

    def _expired(self):
        self.timer = None
        self.set_visible(False)
        return False

    def hide_osd(self):
        if self.timer is not None:
            GLib.source_remove(self.timer)
            self.timer = None
        self.set_visible(False)


class VolumeKeys:
    """The keys' state: the shell's copy of the volume, the repeat timer,
    the one wpctl call in flight. `osd_for(name)` gives a panel's display
    (None for an unknown panel)."""

    def __init__(self, osd_for):
        self.osd_for = osd_for
        self.level = None  # (volume, muted)
        self.read_at = 0.0
        self.repeat = None
        self.repeats = 0
        self.key = None
        self.output = None
        self.busy = None  # the Gio.Subprocess running
        self.busy_target = None  # what the sink has once it is done
        self.queued = None  # (before, after) waiting for it

    def read(self):
        try:
            out = subprocess.run(
                ["wpctl", "get-volume", volume.SINK], capture_output=True, text=True, timeout=1.5
            ).stdout
        except (OSError, subprocess.SubprocessError) as e:
            log("volume: wpctl: %s" % e)
            return None
        level = volume.parse_get_volume(out)
        if level is None:
            log("volume: no default sink (%s)" % out.strip())
        return level

    def key_event(self, key, pressed, output):
        if not pressed:
            if key == self.key:
                self.stop_repeat()
            return
        self.stop_repeat()
        if self.level is None or time.monotonic() - self.read_at > REREAD_AFTER_S:
            if self.busy is None:
                self.level = self.read()
        self.read_at = time.monotonic()
        if self.level is None:
            return
        self.key = key
        self.output = output
        self.press()
        if key in ("up", "down"):
            self.repeat = GLib.timeout_add(volume.REPEAT_DELAY_MS, self._first_repeat)

    def press(self):
        before = self.level
        after = volume.step(before[0], before[1], self.key)
        self.level = after
        self.read_at = time.monotonic()
        self.apply(before, after)
        if self.output is not None:
            osd = self.osd_for(self.output)
            if osd is not None:
                osd.show_level(*after)

    def _first_repeat(self):
        self.repeat = GLib.timeout_add(volume.REPEAT_EVERY_MS, self._again)
        self._again()
        return False

    def _again(self):
        self.repeats += 1
        if self.repeats > volume.REPEAT_MAX or self.level is None:
            self.repeat = None
            self.stop_repeat()
            return False
        self.press()
        return True

    def stop_repeat(self):
        if self.repeat is not None:
            GLib.source_remove(self.repeat)
        self.repeat = None
        self.repeats = 0

    def screen_off(self):
        self.stop_repeat()

    # -- wpctl, one call at a time ------------------------------------------------------

    def apply(self, before, after):
        if self.busy is not None:
            # Keep the first "before" of the queue: it is what the sink has.
            start = self.queued[0] if self.queued is not None else self.busy_target
            self.queued = (start, after)
            return
        self._run(before, after)

    def _run(self, before, after):
        cmds = volume.commands(before, after)
        if not cmds:
            return
        self.busy_target = after
        argv = cmds[0]
        rest = cmds[1:]
        try:
            self.busy = Gio.Subprocess.new(argv, Gio.SubprocessFlags.STDOUT_SILENCE)
        except GLib.Error as e:
            log("volume: %s: %s" % (" ".join(argv), e.message))
            self.busy = None
            return
        self.busy.wait_async(None, self._done, rest)

    def _done(self, proc, result, rest):
        try:
            proc.wait_finish(result)
            if not proc.get_successful():
                log("volume: wpctl exited %d" % proc.get_exit_status())
        except GLib.Error as e:
            log("volume: wpctl: %s" % e.message)
        self.busy = None
        if rest:
            # The second call of the same step (mute after the volume).
            argv = rest[0]
            try:
                self.busy = Gio.Subprocess.new(argv, Gio.SubprocessFlags.STDOUT_SILENCE)
                self.busy.wait_async(None, self._done, rest[1:])
                return
            except GLib.Error as e:
                log("volume: %s: %s" % (" ".join(argv), e.message))
                self.busy = None
        if self.queued is not None:
            before, after = self.queued
            self.queued = None
            self._run(before, after)
