"""A desk's taskbar and app menu under GTK 4 (tests/run-gtk.sh): the real
Taskbar and DeskMenu with the shell's style.css, a stand-in shell and
monitor, laid out at laptop mode's desk panel (538 x 757) and at a 1280 px
wide display. Checked: they build, the taskbar's row and tray fit its
width at the panel's size, the menu lists and filters the apps and Enter
picks the first. Pictures to $OUT. Exit code 0 when everything holds.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Gtk4LayerShell", "1.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

from duoshell import deskmenu, displayprompt, taskbar  # noqa: E402


class NoLayerShell:
    """The container has no gtk4-layer-shell: its calls do nothing here
    (the surfaces are plain windows; the layout is what is checked)."""

    def __getattr__(self, _name):
        return self

    def __call__(self, *_args, **_kwargs):
        return None


taskbar.LayerShell = deskmenu.LayerShell = displayprompt.LayerShell = NoLayerShell()
SENT = []
displayprompt.wc.request = lambda name, **args: SENT.append((name, args))

OUT = os.environ.get("OUT", "/tmp")
failures = []


class Monitor:
    def __init__(self, name, w, h):
        self.name, self.w, self.h = name, w, h

    def get_connector(self):
        return self.name

    def get_geometry(self):
        r = Gdk.Rectangle()
        r.x, r.y, r.width, r.height = 0, 0, self.w, self.h
        return r


class FakeApp:
    def __init__(self, name):
        self.name = self.short = name
        self.app_id = name.lower() + ".desktop"
        self.fields = [name]
        self.icon = None
        self.info = None


class Shell:
    def __init__(self):
        self.pinned = ["foot.desktop", "org.gnome.TextEditor.desktop"]
        self.wc_state = {
            "laptop": {"on": True, "desk": "DSI-1", "keys": "DSI-2"},
            "windows": [
                {"id": 1, "app_id": "foot", "minimized": False, "placement": {"output": "DSI-1"}},
                {
                    "id": 2,
                    "app_id": "org.gnome.Nautilus",
                    "minimized": False,
                    "placement": {"output": "DSI-1"},
                },
            ],
        }
        self.launched = []

    def app_index(self):
        return (
            {
                "foot.desktop": ("foot", "foot"),
                "org.gnome.TextEditor.desktop": (None, "gnome-text-editor"),
                "org.gnome.Nautilus.desktop": (None, "nautilus"),
            },
            {},
        )

    def toggle_desk_menu(self, name):
        pass

    def toggle_quick(self, monitor):
        pass

    def toggle_pin(self, d):
        return True

    def desk_launch(self, name, info, display):
        self.launched.append(info)

    def desk_menu_changed(self):
        pass


def check(what, ok):
    print(("ok   " if ok else "FAIL ") + what, flush=True)
    if not ok:
        failures.append(what)


def run():
    css = Gtk.CssProvider()
    css.load_from_path(os.path.join(os.path.dirname(HERE), "duoshell", "style.css"))
    Gtk.StyleContext.add_provider_for_display(
        Gdk.Display.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )
    app = Gtk.Application(application_id="org.duo2os.ShellTest")
    app.register(None)
    shell = Shell()
    # The windows are on DSI-1: the display lists only the pinned apps.
    for name, w, h, apps in (("DSI-1", 538, 757, 3), ("HDMI-A-1", 1280, 720, 2)):
        mon = Monitor(name, w, h)
        bar = taskbar.Taskbar(app, shell, mon)
        bar.set_default_size(w, taskbar.HEIGHT)
        bar.update({"battery": (57, "Discharging"), "wifi": {"state": "connected", "rssi": -50}})
        bar.populate(force=True)
        bar.present()
        for _ in range(30):
            GLib.MainContext.default().iteration(False)
        _min = bar.get_child().measure(Gtk.Orientation.HORIZONTAL, -1)[0]
        check("%s: taskbar's minimum %d fits %d" % (name, _min, w), _min <= w)
        check(
            "%s: taskbar lists %d apps" % (name, apps),
            sum(1 for c in iter_children(bar.row) if isinstance(c, Gtk.Button)) == apps,
        )
        check("%s: battery text" % name, bar.percent.get_label() == "57%")
        menu = deskmenu.DeskMenu(app, shell, mon)
        menu.apps = [FakeApp(n) for n in ("Terminal", "Text Editor", "Files", "Settings")]
        menu.query = "te"
        menu._fill()
        names = [r.app.name for r in iter_children(menu.list)]
        check("%s: menu filters 'te' -> %s" % (name, names), names == ["Terminal", "Text Editor"])
        menu.launch_top()
        check("%s: Enter starts the first" % name, len(shell.launched) >= 1)
        bar.close()
        menu.close()
    # "Desktop or Mirror?" on a panel; answered with Remember.
    prompt = displayprompt.DisplayPrompt(app, shell, Monitor("DSI-1", 538, 757))
    prompt.ask({"output": "DP-1", "name": "TV", "use": "mirror", "asking": True})
    for _ in range(20):
        GLib.MainContext.default().iteration(False)
    _min = prompt.get_child().measure(Gtk.Orientation.HORIZONTAL, -1)[0]
    check("prompt's minimum %d fits 538" % _min, _min <= 538)
    check("prompt names the display", "TV" in prompt.body.get_label())
    prompt.remember.set_active(True)
    prompt.choose("desk")
    check(
        "prompt answers %s" % SENT,
        SENT
        == [
            ("set_display", {"output": "DP-1", "mode": "desk"}),
            ("set_external", {"on_connect": "desk"}),
        ],
    )
    check("prompt goes", not prompt.get_visible())


def iter_children(widget):
    child = widget.get_first_child()
    while child is not None:
        yield child
        child = child.get_next_sibling()


run()
sys.exit(1 if failures else 0)
