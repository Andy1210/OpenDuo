"""The question when a display is plugged in: "Desktop or Mirror?"
(displaymodel.py; duo-wc's `displays`, compositor/duo-wc/src/mirror.rs).

duo-wc mirrors the display while it asks (`external_on_connect = ask`, the
default), so nothing on the phone moves until the answer. The question is
a card at the bottom of the panel in use (the confirm sheet's look):

- **Desktop**: windows on the display, the shell's desk there (a taskbar,
  the app menu), a Bluetooth keyboard and mouse or laptop mode's touchpad
  drive it;
- **Mirror**: the display shows the phone's panel (the one in use);
- **Remember my choice**: the next display is used so without asking
  (Duo Settings > Display changes it back to Ask).

The answer is duo-wc's `set_display` (and `set_external on_connect` when
remembered); the quick menu's Display tile switches later.
"""

from gi.repository import Gtk
from gi.repository import Gtk4LayerShell as LayerShell

from . import displaymodel, wc
from .i18n import _


class DisplayPrompt(Gtk.Window):
    def __init__(self, app, shell, monitor):
        super().__init__(application=app)
        self.shell = shell
        self.monitor = monitor
        self.output = None
        self.add_css_class("duo-confirm")
        self.add_css_class("duo-display-prompt")
        self.set_decorated(False)
        LayerShell.init_for_window(self)
        LayerShell.set_namespace(self, "duo-display-prompt")
        LayerShell.set_layer(self, LayerShell.Layer.OVERLAY)
        LayerShell.set_monitor(self, monitor)
        for edge in (LayerShell.Edge.BOTTOM, LayerShell.Edge.LEFT, LayerShell.Edge.RIGHT):
            LayerShell.set_anchor(self, edge, True)
            LayerShell.set_margin(self, edge, 12)
        LayerShell.set_keyboard_mode(self, LayerShell.KeyboardMode.NONE)

        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        card.add_css_class("confirm-card")
        self.title = Gtk.Label(label=_("A display is connected"), xalign=0)
        self.title.add_css_class("confirm-title")
        self.title.set_wrap(True)
        self.title.set_max_width_chars(20)
        self.title.set_hexpand(True)
        self.body = Gtk.Label(label=_("How should it be used?"), xalign=0)
        self.body.add_css_class("confirm-body")
        self.body.set_wrap(True)
        self.body.set_max_width_chars(20)
        self.body.set_hexpand(True)
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8, homogeneous=True)
        row.add_css_class("confirm-buttons")
        self.desk = self._choice("focus-windows-symbolic", _("Desktop"), "desk")
        self.mirror = self._choice("video-display-symbolic", _("Mirror"), "mirror")
        row.append(self.desk)
        row.append(self.mirror)
        self.remember = Gtk.CheckButton(label=_("Remember my choice"))
        self.remember.add_css_class("remember")
        self.remember.set_can_focus(False)
        card.append(self.title)
        card.append(self.body)
        card.append(row)
        card.append(self.remember)
        self.set_child(card)

    def _choice(self, icon, label, mode):
        button = Gtk.Button()
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        box.set_halign(Gtk.Align.CENTER)
        box.append(Gtk.Image.new_from_icon_name(icon))
        box.append(Gtk.Label(label=label))
        button.set_child(box)
        button.set_can_focus(False)
        button.update_property([Gtk.AccessibleProperty.LABEL], [label])
        button.connect("clicked", lambda _b: self.choose(mode))
        return button

    def connector(self):
        return self.monitor.get_connector()

    def ask(self, display):
        """Show the question about `display` (duo-wc's entry)."""
        self.output = display.get("output")
        name = display.get("name")
        if name:
            # Translators: %s is the display's own name (a TV's model).
            self.body.set_label(_("%s: how should it be used?") % name)
        else:
            self.body.set_label(_("How should it be used?"))
        self.remember.set_active(False)
        self.present()

    def choose(self, mode):
        output, remember = self.output, self.remember.get_active()
        self.close_prompt()
        if output is None:
            return
        for request, args in displaymodel.answer(output, mode, remember):
            try:
                wc.request(request, **args)
            except (OSError, RuntimeError, ValueError) as e:
                print("duo-shell: display: %s: %s" % (request, e), flush=True)
        print(
            "duo-shell: display %s used as %s%s"
            % (output, mode, " (remembered)" if remember else ""),
            flush=True,
        )

    def close_prompt(self):
        self.output = None
        self.set_visible(False)
