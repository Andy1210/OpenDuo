#!/usr/bin/env python3
"""Edge and corner targets for refining duo2-touchd's per-panel calibration.

    touch-edge-targets.py OUTPUT-NAME LOGFILE

Like compositor/duo-wc/tools/touch-calib.py, but the targets reach the
panel's edges: 2 % / 98 % corners and edge midpoints, 10 % / 90 % corners and
the centre, two taps each. Run inside the duo-wc session
(`. /run/duo-wc/session.env`, `duoctl expect_window OUTPUT`) while
duo2-touchd logs raw contacts (Developer mode on, then `sudo duo-devmode touchlog on`,
which also sends "log on" to /run/duo2-touchd.ctl).
A tap anywhere on the panel moves on, so a target the touch cannot reach
still gets a record (the fit pairs it with duo2-touchd's raw contact).
Each log line: `unix_time target_fx target_fy tap_x tap_y width height`.
"""

import sys
import time

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

E, M = 0.02, 0.10
TARGETS = [
    (E, E),
    (0.5, E),
    (1 - E, E),
    (1 - E, 0.5),
    (1 - E, 1 - E),
    (0.5, 1 - E),
    (E, 1 - E),
    (E, 0.5),
    (M, M),
    (1 - M, M),
    (0.5, 0.5),
    (M, 1 - M),
    (1 - M, 1 - M),
]
TAPS_PER_TARGET = 2


class Targets(Gtk.ApplicationWindow):
    def __init__(self, app, output, log):
        super().__init__(application=app, title=f"touch edges {output}")
        self.log = log
        self.index = 0
        self.taps = 0
        self.last_tap = None
        self.area = Gtk.DrawingArea()
        self.area.set_draw_func(self.draw)
        self.set_child(self.area)
        click = Gtk.GestureClick()
        click.set_touch_only(False)
        click.connect("pressed", self.pressed)
        self.area.add_controller(click)
        self.fullscreen()

    def target(self):
        w, h = self.area.get_width(), self.area.get_height()
        fx, fy = TARGETS[self.index]
        return fx * w, fy * h, w, h

    def draw(self, _area, cr, w, h):
        cr.set_source_rgb(0, 0, 0)
        cr.paint()
        if self.index >= len(TARGETS):
            return
        tx, ty, _, _ = self.target()
        cr.set_source_rgb(1, 1, 1)
        cr.set_line_width(1)
        cr.move_to(tx - 30, ty)
        cr.line_to(tx + 30, ty)
        cr.move_to(tx, ty - 30)
        cr.line_to(tx, ty + 30)
        cr.stroke()
        cr.arc(tx, ty, 8, 0, 6.2832)
        cr.stroke()
        if self.last_tap:
            cr.set_source_rgb(1, 0.2, 0.2)
            cr.arc(*self.last_tap, 4, 0, 6.2832)
            cr.fill()
        cr.set_source_rgb(0.7, 0.7, 0.7)
        cr.set_font_size(18)
        cr.move_to(w * 0.2, h / 2 + 60 if ty < h / 2 else h / 2 - 60)
        cr.show_text(
            f"tap the cross ({self.index + 1}/{len(TARGETS)}, tap {self.taps + 1}/{TAPS_PER_TARGET})"
        )

    def pressed(self, _gesture, _n, x, y):
        _, _, w, h = self.target()
        fx, fy = TARGETS[self.index]
        with open(self.log, "a") as f:
            f.write(f"{time.time():.3f} {fx:.3f} {fy:.3f} {x:.1f} {y:.1f} {w} {h}\n")
        self.last_tap = (x, y)
        self.taps += 1
        if self.taps >= TAPS_PER_TARGET:
            self.taps = 0
            self.index += 1
            self.last_tap = None
            if self.index >= len(TARGETS):
                self.close()
                return
        self.area.queue_draw()


def main():
    output, log = sys.argv[1], sys.argv[2]
    app = Gtk.Application(application_id=f"os.duo2.touchedges.{output.replace('-', '')}")
    app.connect("activate", lambda a: Targets(a, output, log).present())
    app.run([])


if __name__ == "__main__":
    main()
