#!/usr/bin/env python3
"""Measure where real finger taps land: a full-screen GTK4 window shows one
crosshair at a time at known positions; each tap is logged against its target.

    touch-calib.py OUTPUT-NAME LOGFILE

Run inside the duo-wc session (`. /run/duo-wc/session.env`), after
`duoctl expect_window OUTPUT`. Five targets (corners at 10 % and the centre),
two taps each; the window closes itself after the last one. Each log line:
`target_x target_y tap_x tap_y width height` in the window's logical pixels.
"""

import sys

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

TARGETS = [(0.1, 0.1), (0.9, 0.1), (0.5, 0.5), (0.1, 0.9), (0.9, 0.9)]
TAPS_PER_TARGET = 2


class Calib(Gtk.ApplicationWindow):
    def __init__(self, app, output, log):
        super().__init__(application=app, title=f"touch calibration {output}")
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
        cr.arc(tx, ty, 12, 0, 6.2832)
        cr.stroke()
        if self.last_tap:
            cr.set_source_rgb(1, 0.2, 0.2)
            cr.arc(*self.last_tap, 4, 0, 6.2832)
            cr.fill()
        cr.set_source_rgb(0.7, 0.7, 0.7)
        cr.move_to(20, h / 2 + 60 if ty < h / 2 else h / 2 - 60)
        cr.set_font_size(18)
        cr.show_text(
            f"tap the cross centre ({self.index + 1}/{len(TARGETS)}, "
            f"tap {self.taps + 1}/{TAPS_PER_TARGET})"
        )

    def pressed(self, _gesture, _n, x, y):
        tx, ty, w, h = self.target()
        with open(self.log, "a") as f:
            f.write(f"{tx:.1f} {ty:.1f} {x:.1f} {y:.1f} {w} {h}\n")
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
    app = Gtk.Application(application_id=f"os.duo2.touchcalib.{output.replace('-', '')}")
    app.connect("activate", lambda a: Calib(a, output, log).present())
    app.run([])


if __name__ == "__main__":
    main()
