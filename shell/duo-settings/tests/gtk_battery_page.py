"""The Battery page under GTK 4 + libadwaita (tests/run-gtk.sh): built from a
made-up report, laid out at one panel's size (538 x 757), its text checked
(an app's details sheet too) and two pictures saved to $OUT: the page as the
phone shows it and the whole page. Exit code 0 when everything is there.
(Broadway has no icon theme cache here: icons show as missing; the sheet's
slide-in is not drawn into the picture.)"""

import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gsk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Adw, Gdk, Gio, GLib, Graphene, Gsk, Gtk  # noqa: E402

OUT = os.environ.get("OUT", "/tmp")
NOW = time.time()


def fake_report():
    t0 = NOW - 6 * 3600
    pts = []
    for i in range(0, 361):
        t = t0 + i * 60
        lvl = 100 - i * 0.12 if t < NOW - 3600 else 57 + (t - (NOW - 3600)) / 3600 * 20
        pts.append([round(t), round(lvl)])
    battery_list = [
        {
            "key": "comp:screen",
            "mah": 410.0,
            "cpu_mah": 0,
            "gpu_mah": 0,
            "extra": {"panel0_s": 5400, "panel1_s": 3100},
        },
        {
            "key": "app:org.mozilla.firefox",
            "mah": 260.0,
            "cpu_mah": 210.0,
            "gpu_mah": 50.0,
            "extra": {
                "cpu0_s": 1200,
                "cpu1_s": 400,
                "cpu2_s": 90,
                "gpu_s": 300,
                "fg_s": 4100,
                "bg_s": 600,
            },
        },
        {"key": "comp:idle", "mah": 190.0, "cpu_mah": 0, "gpu_mah": 0, "extra": {"time_s": 7000}},
        {
            "key": "exe:gnome-calls",
            "mah": 20.0,
            "cpu_mah": 20.0,
            "gpu_mah": 0,
            "extra": {"cpu0_s": 300, "bg_s": 18000},
        },
        {
            "key": "app:org.gnome.Calls",
            "mah": 15.0,
            "cpu_mah": 12.0,
            "gpu_mah": 3.0,
            "extra": {"cpu0_s": 90, "fg_s": 300},
        },
        {
            "key": "android:com.android.chrome",
            "mah": 80.0,
            "cpu_mah": 70.0,
            "gpu_mah": 10.0,
            "extra": {"cpu0_s": 300, "cpu1_s": 200, "fg_s": 900},
        },
        {
            "key": "system:compositor",
            "mah": 120.0,
            "cpu_mah": 60.0,
            "gpu_mah": 60.0,
            "extra": {"cpu0_s": 600, "gpu_s": 800},
        },
        {
            "key": "comp:modem",
            "mah": 95.0,
            "cpu_mah": 0,
            "gpu_mah": 0,
            "extra": {"up_s": 18000, "awake_s": 15000, "rx_bytes": 12e6, "tx_bytes": 1e6},
        },
        {
            "key": "comp:wifi",
            "mah": 60.0,
            "cpu_mah": 0,
            "gpu_mah": 0,
            "extra": {"up_s": 18000, "rx_bytes": 350e6, "tx_bytes": 20e6},
        },
        {
            "key": "comp:dsp",
            "mah": 30.0,
            "cpu_mah": 0,
            "gpu_mah": 0,
            "extra": {"adsp_s": 1200, "cdsp_s": 60, "slpi_s": 18000},
        },
        {
            "key": "system:kernel",
            "mah": 25.0,
            "cpu_mah": 25.0,
            "gpu_mah": 0,
            "extra": {"cpu0_s": 400, "irq_s": 200},
        },
        {"key": "comp:bluetooth", "mah": 3.0, "cpu_mah": 0, "gpu_mah": 0, "extra": {"on_s": 18000}},
        {
            "key": "system:batterystats",
            "mah": 0.8,
            "cpu_mah": 0.8,
            "gpu_mah": 0,
            "extra": {"cpu0_s": 20},
        },
    ]
    return {
        "version": 1,
        "generated": NOW,
        "window": {"start": t0, "end": NOW, "reason": "full", "start_level": 100},
        "battery": {
            "level": 77,
            "charger": True,
            "status": "Charging",
            "on_battery_s": 5 * 3600,
            "used_pct": 43.0,
            "used_mah": 1308.8,
            "charger_mah": 140.0,
            "capacity_mah": 4449,
            "remaining_h": None,
        },
        "graph": {
            "points": pts,
            "charging": [[round(NOW - 3600), round(NOW)]],
            "screen": [[round(t0 + 600), round(t0 + 3000)], [round(t0 + 9000), round(t0 + 12000)]],
            "suspended": [[round(t0 + 14000), round(t0 + 15500)]],
        },
        "entries": {
            "battery": battery_list,
            "charger": [
                {
                    "key": "comp:screen",
                    "mah": 90.0,
                    "cpu_mah": 0,
                    "gpu_mah": 0,
                    "extra": {"panel0_s": 1800},
                },
                {
                    "key": "comp:idle",
                    "mah": 50.0,
                    "cpu_mah": 0,
                    "gpu_mah": 0,
                    "extra": {"time_s": 1800},
                },
            ],
        },
        "calibration": {
            "battery": {"groups": 131, "ratio": 1.18, "measured_mah": 1308.8, "prior_mah": 1109.0},
            "charger": {"groups": 3, "ratio": 1.05},
            "scales": {},
            "profile": {"file": True, "sources": {}},
        },
        "collector": {"cpu_s": 40.1, "awake_s": 21600, "cpu_pct_of_core": 0.19},
    }


def install_desktop_file():
    # GLib hides a desktop file whose program is not on PATH: two stand-ins
    home = os.environ.get("HOME", "/tmp")
    bindir = os.path.join(home, "bin")
    os.makedirs(bindir, exist_ok=True)
    for prog in ("firefox", "gnome-calls"):
        with open(os.path.join(bindir, prog), "w") as f:
            f.write("#!/bin/sh\n")
        os.chmod(os.path.join(bindir, prog), 0o755)
    os.environ["PATH"] = bindir + ":" + os.environ.get("PATH", "/usr/bin")
    d = os.path.join(home, ".local/share/applications")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "org.gnome.Calls.desktop"), "w") as f:
        f.write(
            "[Desktop Entry]\nType=Application\nName=Calls\nExec=gnome-calls\nIcon=phone-symbolic\n"
        )
    with open(os.path.join(d, "org.mozilla.firefox.desktop"), "w") as f:
        f.write(
            "[Desktop Entry]\nType=Application\nName=Firefox\nExec=firefox %u\nIcon=web-browser-symbolic\n"
        )


def render(widget, path, background=None):
    for _ in range(20):
        w, h = widget.get_width(), widget.get_height()
        paintable = Gtk.WidgetPaintable.new(widget)
        snap = Gtk.Snapshot()
        if background:
            bg = Gdk.RGBA()
            bg.parse(background)
            snap.append_color(bg, Graphene.Rect().init(0, 0, w, h))
        paintable.snapshot(snap, w, h)
        node = snap.to_node()
        if node is not None:
            break
        spin(200)
    r = Gsk.CairoRenderer()
    r.realize_for_display(Gdk.Display.get_default())
    tex = r.render_texture(node, None)
    tex.save_to_png(path)
    r.unrealize()


def texts(widget, out=None):
    out = [] if out is None else out
    if isinstance(widget, Gtk.Label):
        out.append(widget.get_label())
    if isinstance(widget, Adw.PreferencesRow):
        out.append(widget.get_title())
        if isinstance(widget, Adw.ActionRow) and widget.get_subtitle():
            out.append(widget.get_subtitle())
    child = widget.get_first_child()
    while child is not None:
        texts(child, out)
        child = child.get_next_sibling()
    return out


def row_titles(widget, out=None):
    out = [] if out is None else out
    if isinstance(widget, Adw.ActionRow):
        out.append(widget.get_title())
    child = widget.get_first_child()
    while child is not None:
        row_titles(child, out)
        child = child.get_next_sibling()
    return out


def spin(ms):
    end = time.monotonic() + ms / 1000.0
    ctx = GLib.MainContext.default()
    while time.monotonic() < end:
        ctx.iteration(False)
        time.sleep(0.01)


def main():
    install_desktop_file()
    from duosettings import battery
    from duosettings import main as dmain

    rep = fake_report()
    battery.load_report = lambda path=battery.REPORT, text=None, _orig=battery.load_report: _orig(
        text=text if text else json.dumps(rep)
    )
    ran = []
    dmain.run_async = lambda argv, done=None: (
        ran.append(argv) or GLib.idle_add(lambda: done(True, json.dumps(rep)) and False)
    )

    app = Adw.Application(
        application_id="org.duo2os.SettingsTest", flags=Gio.ApplicationFlags.NON_UNIQUE
    )
    failures = []
    state = {}

    def activate(a):
        css = Gtk.CssProvider()
        css.load_from_string(dmain.CSS)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_DARK)
        win = dmain.Window(a)
        win.set_default_size(538, 757)
        win.present()
        state["win"] = win

    app.connect("activate", activate)
    app.register(None)
    app.activate()
    spin(800)
    win = state["win"]
    page = next(p for p in win.pages if p.get_tag() == "BatteryPage")
    win.open(page)
    spin(1500)
    render(win, os.path.join(OUT, "battery-page.png"))
    t = texts(win)
    for want in (
        "Battery usage",
        "Apps",
        "System",
        "Firefox",
        "Calls",
        "Screen",
        "Mobile network",
        "Display compositor",
        "Signal processors (DSPs)",
        "Clear usage history",
        "Include time on the charger",
        "77 %",
        "Charging",
        "Sleep",
        "Stay awake while charging",
    ):
        if not any(want in (s or "") for s in t):
            failures.append("missing: " + want)
    # Calls in the background (exe:gnome-calls) and in front (app:org.gnome.Calls): one row
    rt = row_titles(win)
    if rt.count("Calls") != 1:
        failures.append("Calls not merged into one row: %r" % rt)
    # shares: the screen 410 of 1308.8 mAh
    if not any(s == "31 %" for s in t):
        failures.append("the screen's share (31 %) is not shown")
    # the whole page: its scrolled content at full height
    content = page.prefs
    while content is not None and not isinstance(content, Gtk.ScrolledWindow):
        content = content.get_first_child()
    if content is not None:
        inner = content.get_child()
        if isinstance(inner, Gtk.Viewport):
            inner = inner.get_child()
        render(inner, os.path.join(OUT, "battery-page-full.png"), "#222226")
    # an app's details
    rows, _total = battery.ranked(
        battery.resolve(battery.merge_entries(rep["entries"]), page.lookup)
    )
    firefox = next(r for r in rows if r["name"] == "Firefox")
    win.set_default_size(538, 757)
    spin(500)
    dialog = page.open_details(firefox)
    spin(1500)
    if dialog.get_root() is not win:
        failures.append("the details sheet is not in the window")
    dt = texts(win)
    for want in ("on the big cores", "GPU", "On screen", "Active in the background"):
        if not any(want in (s or "") for s in dt):
            failures.append("details missing: " + want)
    # the charger switch adds the charger's 140 mAh
    page.charger_switch.set_active(True)
    spin(300)
    if "140 mAh" not in page.summary_row.get_subtitle():
        failures.append("charger time not in the summary: %r" % page.summary_row.get_subtitle())
    dialog.close()
    spin(800)
    # Sleep: the switch (no autosleep.conf here: the daemon's default, on),
    # its command, and the note for a timed hold
    if not page.awake_switch.get_active() or page.sleep_note.get_visible():
        failures.append(
            "sleep: switch %r, note shown %r"
            % (page.awake_switch.get_active(), page.sleep_note.get_visible())
        )
    del ran[:]
    page.awake_switch.set_active(False)
    spin(300)
    if not any(a[-2:] == ["charging", "off"] and "duo2-autosleep-switch" in a[2] for a in ran):
        failures.append("the switch ran %r" % ran)
    import tempfile

    from duosettings import sleep

    with tempfile.NamedTemporaryFile("w", delete=False) as f:
        f.write("%d\n" % (time.time() + 7200))
    sleep.HOLD_UNTIL = f.name
    page.show_sleep()
    spin(300)
    os.unlink(f.name)
    if not page.sleep_note.get_visible() or "remote work until" not in (
        page.sleep_note.get_subtitle() or ""
    ):
        failures.append("timed hold note: %r" % page.sleep_note.get_subtitle())
    render(win, os.path.join(OUT, "battery-page-sleep.png"))
    for f in failures:
        print("FAIL", f)
    print("ok" if not failures else "%d failures" % len(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
