"""The Pen page under GTK 4 + libadwaita (tests/run-gtk.sh tests/gtk_pen_page.py):
laid out at one panel's size (538 x 757), its text checked; the palm rejection
row follows a made-up pen.conf, the side button and the dot follow a made-up
duo-wc state, the pen charger's row follows a made-up connector and setting
(hidden without the connector), and every change asks the helper for exactly
its command (a stand-in: nothing runs). The Bluetooth part runs on a stand-in
BlueZ: a paired pen with its battery, the top button's rows writing
duo-shell's pen.conf, and the pairing walk (LE filter, discovery, the new pen
heard and paired, the filter cleared). Pictures to $OUT."""

import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)  # gtk_battery_page's helpers (python3 -I adds no script directory)

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gsk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Adw, Gdk, Gio, GLib, Graphene, Gtk  # noqa: E402
from gtk_battery_page import render, spin, texts  # noqa: E402

OUT = os.environ.get("OUT", "/tmp")


def main():
    home = tempfile.mkdtemp()
    os.environ["HOME"] = home
    conf = os.path.join(home, "pen.conf")
    with open(conf, "w") as f:
        f.write("palm_rejection = panel\nwriting_feel = pencil\nfeel_strength = 30\n")
    from duosettings import main as dmain
    from duosettings import pen, penpage, system

    real_read = pen.read_palm
    penpage.read_palm = lambda path=None: real_read(conf)
    real_haptics = pen.read_haptics
    penpage.read_haptics = lambda path=None: real_haptics(conf)
    import json

    fw_cache = os.path.join(home, "firmware.json")
    fw_status = os.path.join(home, "fw-status.json")
    with open(fw_cache, "w") as f:
        json.dump(
            {
                "read_at": 1,
                "components": [
                    {
                        "component": 17,
                        "name": "BLE",
                        "version": "5.148.139",
                        "raw": 0x0500948B,
                        "bank": 1,
                    },
                    {
                        "component": 16,
                        "name": "MCU",
                        "version": "5.133.139",
                        "raw": 0x0500858B,
                        "bank": 0,
                    },
                    {
                        "component": 19,
                        "name": "HAPTIC",
                        "version": "5.117.139",
                        "raw": 0x0500758B,
                        "bank": 0,
                    },
                ],
            },
            f,
        )
    penpage.FW_CACHE, penpage.FW_STATUS = fw_cache, fw_status
    fw_files = {
        "set": "Microsoft 1.22.139.0",
        "known": True,
        "parts": [
            {
                "label": "BLE-LO",
                "component": 17,
                "name": "BLE",
                "version": "5.150.139",
                "raw": 0x0500968B,
            },
            {
                "label": "MCU-B0",
                "component": 16,
                "name": "MCU",
                "version": "5.142.139",
                "raw": 0x05008E8B,
            },
            {
                "label": "HAPTIC",
                "component": 19,
                "name": "HAPTIC",
                "version": "5.117.139",
                "raw": 0x0500758B,
            },
        ],
    }
    charge_setting = os.path.join(home, "pen-charger")
    charge_out = os.path.join(home, "pen-charger-output")
    os.makedirs(charge_out)
    with open(os.path.join(charge_out, "state"), "w") as f:
        f.write("disabled\n")
    penpage.charger = lambda: pen.charger(charge_setting, charge_out)
    state = {
        "pen": {
            "in_range": True,
            "output": "DSI-2",
            "tool": "pen",
            "tip": False,
            "button": "off",
            "dot": False,
            "input": "all",
            "apps": ["rnote", "krita"],
        }
    }
    system.wc_state = lambda: state
    system.wc_request = lambda request, **args: {}
    ran = []

    def run_async(argv, done=None):
        # As the helper: the setting lands, then (later, as Gio's) done.
        if argv[3:5] == ["pen", "fw-available"]:  # a read: answered, not counted
            if done:
                GLib.idle_add(lambda: done(True, json.dumps(fw_files)) and False)
            return
        ran.append(argv)
        if argv[3:5] == ["pen", "palm"]:
            with open(conf, "a") as f:
                f.write("palm_rejection = %s\n" % argv[5])
        elif argv[3:5] in (["pen", "feel"], ["pen", "strength"], ["pen", "click"]):
            key = {"feel": "writing_feel", "strength": "feel_strength", "click": "button_click"}[
                argv[4]
            ]
            with open(conf, "a") as f:
                f.write("%s = %s\n" % (key, argv[5]))
        elif argv[3:4] == ["wc-conf"]:
            key = {
                "pen_button": "button",
                "pen_dot": "dot",
                "pen_input": "input",
                "pen_apps": "apps",
            }[argv[4]]
            state["pen"][key] = {
                "button": argv[5],
                "dot": argv[5] == "on",
                "input": argv[5],
                "apps": [a for a in argv[5].replace(",", " ").split()],
            }[key]
        elif argv[3:5] == ["pen", "charge"]:
            with open(charge_setting, "w") as f:
                f.write(argv[5] + "\n")
        if done:
            GLib.idle_add(lambda: done(True, "") and False)

    penpage.run_async = run_async

    from duosettings import bluetooth

    class FakeBluez:
        """BlueZ as the page sees it: a paired, connected pen at 64 %."""

        bus = None

        def __init__(self):
            self.ui = None
            self.listeners = []
            self.calls = []
            self.scanning = False
            self.pair_done = None
            self.devs = [
                {
                    "path": "/org/bluez/hci0/dev_P1",
                    "alias": "Surface Slim Pen 2",
                    "pen": True,
                    "paired": True,
                    "connected": True,
                    "battery": 64,
                    "rssi": None,
                    "seen": None,
                    "named": True,
                    "icon": "input-tablet",
                    "address": "",
                }
            ]

        def available(self):
            return True

        def powered(self):
            return True

        def discovering(self):
            return self.scanning

        def devices(self):
            return list(self.devs)

        def name_of(self, path):
            return "Surface Slim Pen 2"

        def reload(self):
            pass

        def set_discovery_filter(self, transport=None, done=None):
            self.calls.append(("filter", transport))
            if done:
                done(None)

        def start_discovery(self):
            self.calls.append("start")
            self.scanning = True

        def stop_discovery(self):
            self.calls.append("stop")
            self.scanning = False

        def pair(self, path, done):
            self.calls.append(("pair", path))
            self.pair_done = done

        def forget(self, path, done):
            self.calls.append(("forget", path))
            done(None)

        def close(self):
            pass

    fake = FakeBluez()

    def shared(on_change, ui):
        fake.listeners.append(on_change)
        return fake

    bluetooth.shared = shared

    app = Adw.Application(
        application_id="org.duo2os.SettingsTest", flags=Gio.ApplicationFlags.NON_UNIQUE
    )
    failures = []
    st = {}

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
        st["win"] = win

    app.connect("activate", activate)
    app.register(None)
    app.activate()
    spin(800)
    win = st["win"]
    if not win.open_tag("pen"):
        failures.append("duo-settings pen does not open the page")
    page = win.current
    spin(800)
    render(win, os.path.join(OUT, "pen-page.png"))
    t = texts(win)
    for want in (
        "Pen",
        "no pairing",
        "Now",
        "Hovering over the right screen",
        "Pen test",
        "Open",
        "Bluetooth",
        "Surface Slim Pen 2",
        "Connected · Battery 64 %",
        "Pair your Surface Pen",
        "Pair again",
        "Top button",
        "Click",
        "Open Rnote",
        "Double click",
        "Screenshot",
        "Press and hold",
        "Quick menu",
        "Feel",
        "Writing feel",
        "Pencil",
        "Strength",
        "Click when the top button is pressed",
        "Firmware",
        "In the pen",
        "Bluetooth 5.148 · Pen 5.133 · Haptics 5.117",
        "On this phone",
        "Newer: Microsoft 1.22.139.0",
        "Update",
        "Your hand",
        "Palm rejection",
        "Side button",
        "Show where the pen points",
        "Charge pen from phone",
        "Needs the Surface Duo 2 Pen Cover",
    ):
        if not any(want in (s or "") for s in t):
            failures.append("missing: " + want)
    # Read back: the file's palm mode, duo-wc's button and dot; no helper run for it.
    if page.palm.get_selected() != 1:
        failures.append("palm row: %d, the file says panel" % page.palm.get_selected())
    if page.button.get_selected() != 1:
        failures.append("button row: %d, duo-wc says off" % page.button.get_selected())
    if page.dot.get_active():
        failures.append("the dot switch is on, duo-wc says off")
    if (
        page.input.get_selected() != 1
        or page.apps.get_text() != "rnote, krita"
        or page.apps.get_sensitive()
    ):
        failures.append(
            "pen input rows: %d %r %s, duo-wc says all, rnote + krita (the list unused)"
            % (page.input.get_selected(), page.apps.get_text(), page.apps.get_sensitive())
        )
    if (
        page.feel.get_selected() != 2
        or int(page.strength.scale.get_value()) != 30
        or page.click.get_active()
    ):
        failures.append(
            "feel rows: %d %s %s, the file says pencil, 30, no click"
            % (page.feel.get_selected(), page.strength.scale.get_value(), page.click.get_active())
        )
    if ran:
        failures.append("showing the page ran the helper: %r" % ran)
    if not page.charge_group.get_visible() or page.charge.get_active():
        failures.append(
            "the charger row: visible %s, on %s (want visible, off by default)"
            % (page.charge_group.get_visible(), page.charge.get_active())
        )
    # Changes: exactly the helper's commands.
    page.palm.set_selected(2)
    spin(100)
    page.button.set_selected(0)
    spin(100)
    page.dot.set_active(True)
    spin(200)
    page.charge.set_active(True)
    spin(200)
    page.feel.set_selected(1)
    spin(100)
    page.strength.scale.set_value(80)
    spin(300)
    page.click.set_active(True)
    spin(200)
    page.input.set_selected(0)
    spin(200)
    page.apps.set_text("rnote krita,, inkscape")
    spin(50)
    page.refresh()  # a refresh while typing keeps the text
    if page.apps.get_text() != "rnote krita,, inkscape":
        failures.append(
            "a refresh replaced the drawing apps being typed: %r" % page.apps.get_text()
        )
    page.apps.emit("apply")
    spin(200)
    page.set_apps("rnote; rm")  # not app ids: nothing runs
    spin(100)
    want = [
        ["sudo", "-n", system.HELPER, "pen", "palm", "off"],
        ["sudo", "-n", system.HELPER, "wc-conf", "pen_button", "right_click"],
        ["sudo", "-n", system.HELPER, "wc-conf", "pen_dot", "on"],
        ["sudo", "-n", system.HELPER, "pen", "charge", "on"],
        ["sudo", "-n", system.HELPER, "pen", "feel", "ink"],
        ["sudo", "-n", system.HELPER, "pen", "strength", "80"],
        ["sudo", "-n", system.HELPER, "pen", "click", "on"],
        ["sudo", "-n", system.HELPER, "wc-conf", "pen_input", "apps"],
        ["sudo", "-n", system.HELPER, "wc-conf", "pen_apps", "rnote, krita, inkscape"],
    ]
    if ran != want:
        failures.append("helper commands: %r" % ran)
    if (
        page.palm.get_selected() != 2
        or page.button.get_selected() != 0
        or not page.dot.get_active()
    ):
        failures.append("the rows did not keep the new settings")
    if (
        page.input.get_selected() != 0
        or page.apps.get_text() != "rnote, krita, inkscape"
        or not page.apps.get_sensitive()
    ):
        failures.append(
            "pen input after the changes: %d %r %s"
            % (page.input.get_selected(), page.apps.get_text(), page.apps.get_sensitive())
        )
    # switched on, the transmitter off (the rule): the row says it is paused
    page.refresh_charge()
    if not page.charge.get_active() or not page.charge.get_subtitle().startswith("Paused"):
        failures.append(
            "charger on, transmitter off: %s %r"
            % (page.charge.get_active(), page.charge.get_subtitle())
        )
    state["pen"] = {
        "in_range": True,
        "output": "DSI-1",
        "tool": "eraser",
        "tip": True,
        "button": "right_click",
        "dot": True,
    }
    page.refresh()
    if page.now.get_subtitle() != "Erasing on the left screen":
        failures.append("now: %r" % page.now.get_subtitle())
    render(win, os.path.join(OUT, "pen-page-eraser.png"))
    # a kernel without the pen charger: no row
    os.remove(os.path.join(charge_out, "state"))
    page.refresh_charge()
    if page.charge_group.get_visible():
        failures.append("the charger row is shown without the kernel's connector")

    # The top button: duo-shell's pen.conf, as the user (no helper).
    ran.clear()
    page.presses["click"].set_selected(1)  # screenshot
    page.presses["hold"].set_selected(3)  # nothing
    spin(100)
    button_conf = os.path.join(home, ".config", "duo-shell", "pen.conf")
    try:
        with open(button_conf) as f:
            written = f.read()
    except OSError:
        written = ""
    for line in ("click = screenshot", "double_click = screenshot", "hold = off"):
        if line not in written.splitlines():
            failures.append("pen.conf has no %r: %r" % (line, written))
    if ran:
        failures.append("the top button ran the helper: %r" % ran)

    # The firmware: Update asks, then starts the helper's unit; the status file drives the rows.
    ran.clear()
    if not page.fw_button.get_visible() or page.fw_button.get_label() != "Update":
        failures.append("firmware: no Update button with newer files")
    page.fw_clicked()
    spin(200)
    dialog = (
        page.window.get_visible_dialog() if hasattr(page.window, "get_visible_dialog") else None
    )
    if dialog is not None:
        dialog.emit("response", "ok")
        dialog.force_close()
        spin(200)
    else:
        failures.append("firmware: Update asked nothing")
    if ran != [["sudo", "-n", system.HELPER, "pen", "fw-update"]]:
        failures.append("firmware: helper commands %r" % ran)
    with open(fw_status, "w") as f:
        json.dump(
            {
                "state": "sending",
                "label": "BLE-LO",
                "version": "5.150.139",
                "sent": 1101,
                "total": 2203,
                "pid": os.getpid(),
            },
            f,
        )
    page.refresh_fw()
    if (
        page.fw_status.get_subtitle()
        != "Sending Bluetooth 5.150: 49 %. Keep the pen near the phone"
    ):
        failures.append("firmware status: %r" % page.fw_status.get_subtitle())
    if page.fw_button.get_label() != "Stop" or not page.fw_bar.get_visible():
        failures.append("firmware: running, but no Stop / progress bar")
    render(win, os.path.join(OUT, "pen-firmware.png"))
    with open(fw_status, "w") as f:
        json.dump({"state": "waiting-link", "pid": os.getpid()}, f)
    page.refresh_fw()
    if page.fw_status.get_subtitle() != "Press the pen's top button to connect it":
        failures.append("firmware waiting: %r" % page.fw_status.get_subtitle())
    ran.clear()
    page.fw_clicked()  # Stop
    spin(200)
    if ran != [["sudo", "-n", system.HELPER, "pen", "fw-stop"]]:
        failures.append("firmware stop: %r" % ran)
    with open(fw_status, "w") as f:
        json.dump(
            {
                "state": "done",
                "pid": os.getpid(),
                "delivered": [{"label": "BLE-LO", "name": "BLE", "version": "5.150.139"}],
            },
            f,
        )
    page.refresh_fw()
    if (
        page.fw_status.get_subtitle() != "Updated to Bluetooth 5.150"
        or page.fw_button.get_label() != "Update"
    ):
        failures.append(
            "firmware done: %r %r" % (page.fw_status.get_subtitle(), page.fw_button.get_label())
        )
    # done with the pen's versions read at the end (newer than the cache): shown at once,
    # nothing newer is offered, without a reopen
    with open(fw_status, "w") as f:
        json.dump(
            {
                "state": "done",
                "pid": os.getpid(),
                "time": int(time.time()) + 5,
                "delivered": [
                    {"label": "BLE-LO", "name": "BLE", "version": "5.150.139"},
                    {"label": "MCU-B0", "name": "MCU", "version": "5.142.139"},
                ],
                "versions": [
                    {
                        "component": 17,
                        "name": "BLE",
                        "version": "5.150.139",
                        "raw": 0x0500968B,
                        "bank": 0,
                    },
                    {
                        "component": 16,
                        "name": "MCU",
                        "version": "5.142.139",
                        "raw": 0x05008E8B,
                        "bank": 0,
                    },
                    {
                        "component": 19,
                        "name": "HAPTIC",
                        "version": "5.117.139",
                        "raw": 0x0500758B,
                        "bank": 0,
                    },
                ],
            },
            f,
        )
    page.refresh_fw()
    if (
        page.fw_current.get_subtitle() != "Bluetooth 5.150 · Pen 5.142 · Haptics 5.117"
        or not page.fw_available.get_subtitle().startswith("Up to date")
        or page.fw_button.get_visible()
    ):
        failures.append(
            "firmware done, new versions: %r %r %s"
            % (
                page.fw_current.get_subtitle(),
                page.fw_available.get_subtitle(),
                page.fw_button.get_visible(),
            )
        )
    render(win, os.path.join(OUT, "pen-firmware-done.png"))

    # The pen scrolls the page like a finger (duosettings/penscroll.py): the window has the
    # capture-phase gesture; a drag that starts on a row of this page finds its scrolled view
    # and moves it (the gesture's signals driven by hand: broadway has no pen).
    from duosettings import penscroll

    ps = getattr(win, "_pen_scroll", None)
    if ps is None or ps.gesture.get_propagation_phase() != Gtk.PropagationPhase.CAPTURE:
        failures.append("pen scroll: no capture-phase gesture on the window")
    else:
        page.prefs.scroll_to(page.now) if hasattr(page.prefs, "scroll_to") else None
        spin(100)
        ok, pt = page.now.compute_point(win, Graphene.Point())
        sw = penscroll._scroller_at(win, pt.x + 20, pt.y + 10) if ok else None
        if sw is None:
            failures.append("pen scroll: no scrolled view under a row of the Pen page")
        else:

            class FakeGesture:
                def __init__(self):
                    self.states = []

                def set_state(self, st):
                    self.states.append(st)

            adj = sw.get_vadjustment()
            adj.set_value(0)
            ps.sw, ps.start, ps.samples, ps.claimed = sw, (0.0, 0.0), [(0.0, 0, 0)], False
            g = FakeGesture()
            ps.update(g, 0, -4)  # under the threshold: nothing yet
            if g.states or adj.get_value() != 0:
                failures.append("pen scroll: moved under the drag threshold")
            ps.update(g, 0, -150)
            if g.states != [Gtk.EventSequenceState.CLAIMED] or abs(adj.get_value() - 150) > 0.5:
                failures.append("pen scroll: %r, value %s" % (g.states, adj.get_value()))
            # a flick at the lift goes on and slows down (a tick callback on the view)
            now = GLib.get_monotonic_time() / 1e6
            ps.sw, ps.claimed = sw, True
            ps.samples = [(now - 0.05, 0, 0), (now, 0, -60)]  # 1200 px/s upwards
            before = adj.get_value()
            # (broadway's frame clock does not tick without a browser: the view's tick
            # callback is taken and driven by hand with frame times)
            ticks = []
            sw.add_tick_callback = lambda cb: ticks.append(cb) or 1
            sw.remove_tick_callback = lambda _tid: None
            ps.end(g, 0, -60)

            class Clock:
                def __init__(self, t):
                    self.t = t

                def get_frame_time(self):
                    return int(self.t * 1e6)

            going, t = bool(ticks), 100.0
            values = []
            while going and t < 110.0:
                going = ticks[0](sw, Clock(t))
                values.append(adj.get_value())
                t += 1 / 60
            if (
                not ticks
                or not values
                or not values[-1] > before + 100
                or going
                or any(b < a for a, b in zip(values, values[1:]))
            ):
                failures.append(
                    "pen scroll: the flick did not glide and stop (%s -> %s, %d frames)"
                    % (before, values[-1:] or None, len(values))
                )
            del sw.add_tick_callback, sw.remove_tick_callback
            ps.reset()
            adj.set_value(0)

    # Pairing: the walk with the stand-in BlueZ.
    page.pair_pen()
    spin(200)
    if fake.calls[:2] != [("filter", "le"), "start"]:
        failures.append("pairing did not start an LE discovery: %r" % fake.calls)
    render(win, os.path.join(OUT, "pen-pairing.png"))
    if "Looking for the pen" not in page.status.get_label():
        failures.append("pairing status: %r" % page.status.get_label())
    fake.devs.append(
        {
            "path": "/org/bluez/hci0/dev_P2",
            "alias": "Surface Slim Pen 2",
            "pen": True,
            "paired": False,
            "connected": False,
            "battery": None,
            "rssi": -50,
            "seen": time.monotonic(),
            "named": True,
            "icon": "input-tablet",
            "address": "",
        }
    )
    for listener in fake.listeners:
        listener()
    spin(1200)
    if ("pair", "/org/bluez/hci0/dev_P2") not in fake.calls:
        failures.append("the pen heard was not paired: %r" % fake.calls)
    elif fake.pair_done is not None:
        fake.pair_done(None)
    spin(200)
    if not page.status.get_label().startswith("Paired"):
        failures.append("after pairing: %r" % page.status.get_label())
    if fake.calls[-2:] != ["stop", ("filter", None)]:
        failures.append("discovery and its filter not ended: %r" % fake.calls)
    render(win, os.path.join(OUT, "pen-paired.png"))
    page.flow_dialog.close()
    spin(300)
    if page.flow is not None:
        failures.append("the walk did not end with its dialog")
    page.hidden()
    for f in failures:
        print("FAIL", f)
    print("pen page: %s" % ("ok" if not failures else "%d failures" % len(failures)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
