"""The SIM PIN dialog: over everything on the active panel, a numeric keypad,
the attempts left, and after three wrong PINs the PUK and a new PIN
(mobilemodel.PinEntry decides the steps; mobile.Mobile sends it to
ModemManager). Shown when ModemManager says the modem is locked (sim-pin,
sim-puk), after the session's own lock screen (duo-wc draws nothing of the
shell over a lock), again at every screen-on until the SIM is unlocked;
"Not now" hides it until then, the bar's SIM icon brings it back. The
digits are only in the entry until they are sent: never logged or kept.

"Emergency call" turns the same keypad into an emergency dialler (as on
every phone's SIM PIN screen): only emergency numbers (emergencymodel.py),
one-tap 112 and the country's own, the call through the shell
(Shell.dial_emergency); the dialog then steps aside for Calls' call window.
"""

from gi.repository import Gdk, GLib, Gtk
from gi.repository import Gtk4LayerShell as LayerShell

from . import emergencymodel, mobilemodel
from .i18n import _

KEYS = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "back", "0", "ok"]


class SimPinDialog(Gtk.Window):
    def __init__(self, app, shell, monitor):
        super().__init__(application=app)
        self.shell = shell
        self.monitor = monitor
        self.request = None
        self.entry = None
        self.busy = False
        self.error = None
        self.emergency = None  # an emergencymodel.Entry while it dials
        self.add_css_class("duo-sim-pin")
        self.set_decorated(False)
        LayerShell.init_for_window(self)
        LayerShell.set_namespace(self, "duo-sim-pin")
        LayerShell.set_layer(self, LayerShell.Layer.OVERLAY)
        LayerShell.set_monitor(self, monitor)
        for edge in (
            LayerShell.Edge.TOP,
            LayerShell.Edge.BOTTOM,
            LayerShell.Edge.LEFT,
            LayerShell.Edge.RIGHT,
        ):
            LayerShell.set_anchor(self, edge, True)
        LayerShell.set_exclusive_zone(self, -1)
        LayerShell.set_keyboard_mode(self, LayerShell.KeyboardMode.ON_DEMAND)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.add_css_class("sim-sheet")
        box.set_valign(Gtk.Align.CENTER)
        icon = Gtk.Image.new_from_icon_name("auth-sim-locked-symbolic")
        icon.set_pixel_size(40)
        icon.add_css_class("sim-icon")
        self.title = Gtk.Label()
        self.title.add_css_class("sim-title")
        self.body = Gtk.Label()
        self.body.add_css_class("sim-body")
        self.body.set_wrap(True)
        self.body.set_max_width_chars(20)
        self.body.set_justify(Gtk.Justification.CENTER)
        self.left = Gtk.Label()
        self.left.add_css_class("sim-left")
        self.dots = Gtk.Label()
        self.dots.add_css_class("sim-dots")
        self.message = Gtk.Label()
        self.message.add_css_class("sim-message")
        self.message.set_wrap(True)
        self.message.set_max_width_chars(20)
        self.message.set_justify(Gtk.Justification.CENTER)
        # The emergency dialler's one-tap numbers (shown only then).
        self.suggest = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.suggest.set_halign(Gtk.Align.CENTER)
        self.suggest.set_visible(False)
        self.icon = icon
        for w in (icon, self.title, self.body, self.left, self.dots, self.message, self.suggest):
            box.append(w)

        grid = Gtk.Grid(column_spacing=14, row_spacing=12, column_homogeneous=True)
        grid.set_halign(Gtk.Align.CENTER)
        grid.add_css_class("sim-keypad")
        self.keys = []
        for i, key in enumerate(KEYS):
            if key == "back":
                b = Gtk.Button()
                b.set_child(Gtk.Image.new_from_icon_name("edit-clear-symbolic"))
                b.update_property([Gtk.AccessibleProperty.LABEL], [_("Delete")])
            elif key == "ok":
                b = Gtk.Button()
                b.set_child(Gtk.Image.new_from_icon_name("object-select-symbolic"))
                b.add_css_class("ok")
                b.update_property([Gtk.AccessibleProperty.LABEL], [_("OK")])
                self.ok_key = b
            else:
                b = Gtk.Button(label=key)
            b.add_css_class("key")
            b.set_can_focus(False)
            b.connect("clicked", lambda _b, k=key: self.press(k))
            grid.attach(b, i % 3, i // 3, 1, 1)
            self.keys.append(b)
        box.append(grid)

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        row.set_halign(Gtk.Align.CENTER)
        self.sos = Gtk.Button(label=_("Emergency call"))
        self.sos.add_css_class("sim-later")
        self.sos.add_css_class("sim-emergency")
        self.sos.set_can_focus(False)
        self.sos.connect("clicked", lambda _b: self.open_emergency())
        self.later = Gtk.Button(label=_("Not now"))
        self.later.add_css_class("sim-later")
        self.later.set_can_focus(False)
        self.later.connect("clicked", lambda _b: self._later())
        row.append(self.sos)
        row.append(self.later)
        box.append(row)
        self.set_child(box)

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._key)
        self.add_controller(keys)

    def connector(self):
        return self.monitor.get_connector()

    def show_request(self, request):
        """Ask for `request` (mobilemodel.pin_request); a new kind (the SIM
        now wants its PUK) starts the entry over."""
        if self.entry is None or self.request is None or self.request["kind"] != request["kind"]:
            self.entry = mobilemodel.PinEntry(request["kind"])
            self.error = None
        self.request = request
        self._update()
        if not self.get_visible():
            self.present()

    # -- the emergency dialler -----------------------------------------------------------

    def open_emergency(self):
        offer = emergencymodel.offer(self.shell.emergency_context())
        self.emergency = emergencymodel.Entry(offer["numbers"])
        child = self.suggest.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.suggest.remove(child)
            child = nxt
        for number, what in offer["suggestions"]:
            b = Gtk.Button(label=number)
            b.set_tooltip_text(_(what))
            b.add_css_class("sim-later")
            if number == "112":
                b.add_css_class("sim-emergency")
            b.set_can_focus(False)
            b.connect("clicked", lambda _b, n=number: self._choose(n))
            self.suggest.append(b)
        self.error = None
        self._update()

    def _choose(self, number):
        if self.emergency is not None:
            self.emergency.choose(number)
            self._update()

    def _later(self):
        if self.emergency is not None:
            self.emergency = None  # Back: the PIN again
            self._update()
            return
        self.shell.sim_prompt_later()

    def _emergency_press(self, key):
        result = self.emergency.press("call" if key == "ok" else key)
        if result is not None:
            if self.shell.dial_emergency(result[1], "SIM PIN dialog"):
                # Calls' call window is the call screen: out of its way.
                self.emergency = None
                self.shell.sim_prompt_later()
                return
            self.emergency.message = "failed"
        self._update()

    def hide_dialog(self):
        self.emergency = None
        if self.entry is not None:
            self.entry.reset()
        self.busy = False
        self.error = None
        if self.get_visible():
            self.set_visible(False)
            # The dock it covered comes back.
            GLib.idle_add(lambda: self.shell.sync_docks() and False)

    def _key(self, _ctrl, keyval, _code, _state):
        name = Gdk.keyval_name(keyval) or ""
        if name.startswith("KP_"):
            name = name[3:]
        if len(name) == 1 and name.isdigit():
            self.press(name)
        elif name in ("BackSpace", "Delete"):
            self.press("back")
        elif name in ("Return", "Enter"):
            self.press("ok")
        else:
            return False
        return True

    def press(self, key):
        if self.emergency is not None:
            self._emergency_press(key)
            return
        if self.busy or self.entry is None:
            return
        self.error = None
        request = self.entry.press(key)
        if request is not None:
            self.busy = True
            self._update()
            self.shell.mobile.send(request, self._sent)
            request = None
        self._update()

    def _sent(self, ok, error):
        self.busy = False
        if ok:
            self.hide_dialog()
            return
        self.error = "wrong"
        self.entry.reset()
        self._update()

    def _update_emergency(self):
        e = self.emergency
        self.icon.set_from_icon_name("call-start-symbolic")
        self.title.set_label(_("Emergency call"))
        self.body.set_label(_("Only emergency numbers while the SIM is locked."))
        self.left.set_label("")
        self.left.remove_css_class("warn")
        self.dots.set_label(e.digits or " ")
        self.message.set_label(
            {
                "not-emergency": _("Not an emergency number."),
                "failed": _("The call could not be placed."),
            }.get(e.message, "")
        )
        self.suggest.set_visible(True)
        self.sos.set_visible(False)
        self.later.set_label(_("Back"))
        self.ok_key.set_child(Gtk.Image.new_from_icon_name("call-start-symbolic"))
        self.ok_key.update_property([Gtk.AccessibleProperty.LABEL], [_("Call")])
        for b in self.keys:
            b.set_sensitive(True)

    def _update(self):
        if self.emergency is not None:
            self._update_emergency()
            return
        self.icon.set_from_icon_name("auth-sim-locked-symbolic")
        self.suggest.set_visible(False)
        self.sos.set_visible(True)
        self.later.set_label(_("Not now"))
        self.ok_key.set_child(Gtk.Image.new_from_icon_name("object-select-symbolic"))
        self.ok_key.update_property([Gtk.AccessibleProperty.LABEL], [_("OK")])
        kind = self.request["kind"] if self.request else "pin"
        stage = self.entry.stage if self.entry else "pin"
        left = self.request.get("left") if self.request else None
        if kind == "pin":
            self.title.set_label(_("SIM PIN"))
            self.body.set_label(
                _("The SIM is locked. Enter its PIN to use calls, SMS and mobile data.")
            )
        elif stage == "puk":
            self.title.set_label(_("SIM blocked"))
            self.body.set_label(
                _(
                    "Too many wrong PINs. Enter the PUK (8 digits, from the card the SIM "
                    "came on); then choose a new PIN."
                )
            )
        elif stage == "new":
            self.title.set_label(_("New PIN"))
            self.body.set_label(_("Choose a new PIN for the SIM (4 to 8 digits)."))
        else:
            self.title.set_label(_("New PIN"))
            self.body.set_label(_("The new PIN again."))
        if left is None or stage in ("new", "again"):
            self.left.set_label("")
        elif kind == "pin" and left == 1:
            self.left.set_label(_("Last attempt: after a wrong PIN the SIM needs its PUK"))
        else:
            # Translators: attempts left to enter the SIM's PIN or PUK.
            self.left.set_label(_("%d attempts left") % left if left != 1 else _("1 attempt left"))
        if left is not None and left <= 1:
            self.left.add_css_class("warn")
        else:
            self.left.remove_css_class("warn")
        count = self.entry.count if self.entry else 0
        self.dots.set_label(" ".join("●" for _i in range(count)) if count else " ")
        problem = self.entry.problem if self.entry else None
        if self.busy:
            text = _("Checking…")
        elif self.error == "wrong":
            text = _("Wrong PUK.") if kind == "puk" else _("Wrong PIN.")
        elif problem == "pin-length":
            text = _("A PIN is 4 to 8 digits.")
        elif problem == "puk-length":
            text = _("A PUK is 8 digits.")
        elif problem == "differ":
            text = _("The two PINs differ. Choose the new PIN again.")
        else:
            text = ""
        self.message.set_label(text)
        for b in self.keys:
            b.set_sensitive(not self.busy)
