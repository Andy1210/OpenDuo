"""The wallpaper in the running shell: watches wallpaper.conf and the Bing
directory, decodes the picture off the main thread and hands it whole to the
home screens (HomeScreen.set_wallpaper), which cut their part of it as they
draw (wallpicture.py: a rotation or a pose needs no new cut). No restart of
the shell or of duo-wc: a change of the file shows within a second."""

import os
import threading
import time

import gi

gi.require_version("Gdk", "4.0")

from gi.repository import Gdk, Gio, GLib  # noqa: E402

from . import wallpaper, wallpicture  # noqa: E402
from .safetimer import or_default  # noqa: E402


def log(*a):
    print("duo-shell:", *a, flush=True)


class WallpaperWatch:
    def __init__(self, shell):
        self.shell = shell
        self.key = None  # what is shown now (path, mtime, mode, dim)
        self.timer = None
        self.generation = 0
        # What the home screens show: (texture, dim, mode), or None.
        self.current = None
        self.monitors = []
        for directory in (wallpaper.config_dir(), wallpaper.bing_dir()):
            try:
                GLib.mkdir_with_parents(directory, 0o755)
                m = Gio.File.new_for_path(directory).monitor_directory(
                    Gio.FileMonitorFlags.NONE, None
                )
                m.connect("changed", self._changed)
                self.monitors.append(m)
            except GLib.Error as e:
                log("wallpaper: cannot watch %s: %s" % (directory, e.message))

    def _changed(self, _m, file, _other, _event):
        name = file.get_basename() or ""
        if (
            name in ("wallpaper.conf", "current.json")
            or name.startswith(".tmp-")
            or name.endswith(".jpg")
        ):
            self.schedule()

    def schedule(self, delay_ms=300):
        if self.timer is not None:
            GLib.source_remove(self.timer)
        self.timer = GLib.timeout_add(delay_ms, self._fire)

    def _fire(self):
        self.timer = None
        self.apply()
        return False

    def layout_changed(self):
        """duo-wc's layout changed (a rotation, a pose, the hinge gap): each
        home screen draws its part of the picture anew."""
        panels = wallpicture.layout()
        for home in self.shell.homes.values():
            home.wall.set_layout(panels)

    def apply(self, force=False):
        """Show what the config says on every home screen."""
        want = wallpaper.resolve(wallpaper.read_conf())
        homes = self.shell.homes
        for home in homes.values():
            # The Bing picture's "i" (only while a Bing picture shows).
            home.set_caption(want.get("caption"))
        mtime = None
        if want["path"]:
            try:
                mtime = os.stat(want["path"]).st_mtime
            except OSError:
                want["path"] = None
        key = (want["path"], mtime, want["mode"], want["dim"])
        if key == self.key and not force:
            # A home screen made since (a panel came back) gets it too.
            if self.current is not None:
                for home in homes.values():
                    if home.wall.texture is not self.current[0]:
                        home.set_wallpaper(*self.current)
            return
        self.key = key
        self.generation += 1
        gen = self.generation
        if not want["path"]:
            self.current = None
            for home in homes.values():
                home.set_wallpaper(None, 0)
            return
        path, mode, dim = want["path"], want["mode"], want["dim"]
        started = time.monotonic()

        def work():
            # a decode that raises must still reach done(): None = no picture
            pb = or_default(wallpaper.decode)(path)
            GLib.idle_add(done, pb)

        def done(pb):
            if gen != self.generation:
                return False
            log("wallpaper: decoded in %d ms" % ((time.monotonic() - started) * 1000))
            if pb is None:
                self.current = None
                for home in self.shell.homes.values():
                    home.set_wallpaper(None, 0)
                return False
            self.current = (Gdk.Texture.new_for_pixbuf(pb), dim, mode)
            panels = wallpicture.layout()
            for home in self.shell.homes.values():
                home.wall.set_layout(panels)
                home.set_wallpaper(*self.current)
            return False

        threading.Thread(target=work, daemon=True).start()
