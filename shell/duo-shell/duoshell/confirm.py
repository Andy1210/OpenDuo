"""A question at the bottom of a panel, over everything (the overview too):
"Force close <app>?" when a window asked to close does not answer and its
overview card's force-close button was tapped (duo-wc's
`force_close_request`). Cancel, or Force close: duo-wc kills the app
(`force_close_window`: SIGTERM, then SIGKILL).

And duo-wc's `confirm_request`: an action only the session may take, asked
for by root (Duo Settings' helper: "restart the display" for a refresh rate
or a language change). Only this question's yes, sent back on the shell's
trusted connection (`confirm_answer`), lets duo-wc do it: no app can answer
(docs/hardware/security.md)."""

from gi.repository import GLib, Gtk
from gi.repository import Gtk4LayerShell as LayerShell

from . import wc
from .i18n import _


class ConfirmSheet(Gtk.Window):
    def __init__(self, app, monitor):
        super().__init__(application=app)
        self.monitor = monitor
        self.window_id = None
        self.confirm_id = None
        self.callback = None
        self.add_css_class("duo-confirm")
        self.set_decorated(False)
        LayerShell.init_for_window(self)
        LayerShell.set_namespace(self, "duo-confirm")
        LayerShell.set_layer(self, LayerShell.Layer.OVERLAY)
        LayerShell.set_monitor(self, monitor)
        for edge in (LayerShell.Edge.BOTTOM, LayerShell.Edge.LEFT, LayerShell.Edge.RIGHT):
            LayerShell.set_anchor(self, edge, True)
            LayerShell.set_margin(self, edge, 12)
        LayerShell.set_keyboard_mode(self, LayerShell.KeyboardMode.NONE)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.add_css_class("confirm-card")
        self.title = Gtk.Label(xalign=0)
        self.title.add_css_class("confirm-title")
        self.title.set_wrap(True)
        body = Gtk.Label(
            label=_("It does not answer. Anything it has not saved is lost."), xalign=0
        )
        body.add_css_class("confirm-body")
        body.set_wrap(True)
        # A wrapped label's natural width is its whole text on one line:
        # the surface then asked for more than the panel. Small natural
        # widths, and the panel's width (anchored left and right) decides.
        body.set_max_width_chars(20)
        body.set_hexpand(True)
        self.title.set_max_width_chars(20)
        self.title.set_hexpand(True)
        self.body = body
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8, homogeneous=True)
        row.add_css_class("confirm-buttons")
        cancel = Gtk.Button(label=_("Cancel"))
        self.cancel = cancel
        cancel.set_can_focus(False)
        cancel.connect("clicked", lambda _b: self._cancel())
        go = Gtk.Button(label=_("Force close"))
        self.go = go
        go.add_css_class("destructive")
        go.add_css_class("destructive-action")
        go.set_can_focus(False)
        go.connect("clicked", lambda _b: self._go())
        row.append(cancel)
        row.append(go)
        box.append(self.title)
        box.append(body)
        box.append(row)
        self.set_child(box)

    def ask(self, window_id, name):
        self._answer(False)
        self.window_id = window_id
        # Translators: %s is an app's name or a window's title.
        self.title.set_label(_("Force close %s?") % (name or _("this app")))
        self.body.set_label(_("It does not answer. Anything it has not saved is lost."))
        self.go.set_label(_("Force close"))
        self._plain_buttons(destructive=True)
        self.present()

    def _plain_buttons(self, destructive):
        self.cancel.set_label(_("Cancel"))
        for css in ("destructive", "destructive-action"):
            if destructive:
                self.go.add_css_class(css)
            else:
                self.go.remove_css_class(css)

    def ask_action(self, confirm_id, action, detail=None):
        """duo-wc's confirm_request: `restart-display` (Duo Settings) or
        `remove-packages` (polkit, for PackageKit; `detail` names them)."""
        self._answer(False)
        self.window_id = None
        self.confirm_id = confirm_id
        self._plain_buttons(destructive=True)
        if action == "remove-packages":
            self.title.set_label(_("Remove software?"))
            # Translators: %s names the asking program and the packages.
            self.body.set_label(
                (_("An app asks to uninstall: %s") % detail)
                if detail
                else _("An app asks to uninstall software.")
            )
            self.go.set_label(_("Remove"))
        elif action == "esim":
            # duo2-esim (as root) says what will happen; only this yes lets it.
            self.title.set_label(_("Change the eSIM?"))
            self.body.set_label(detail or _("An app asks to change the eSIM."))
            self.go.set_label(_("Delete") if (detail or "").startswith("Delete") else _("Continue"))
            self._plain_buttons(destructive=(detail or "").startswith("Delete"))
        elif action == "helper":
            # duo-shell-helper asked without the shell's grant (another app,
            # e.g. Duo Settings): the user decides.
            self.title.set_label(_("Allow this?"))
            self.body.set_label(detail or _("An app asks for a system action."))
            self.go.set_label(_("Allow"))
            self._plain_buttons(destructive=False)
        elif action == "restart-display":
            self.title.set_label(_("Restart the display?"))
            self.body.set_label(
                _(
                    "Duo Settings asks for it. Every open app closes; the screen "
                    "is dark for a few seconds."
                )
            )
            self.go.set_label(_("Restart"))
        else:
            self.title.set_label(action)
            self.body.set_label("")
            self.go.set_label(_("Yes"))
        self.present()

    def ask_question(self, title, body, yes_label, callback, no_label=None, destructive=False):
        """A question of the shell's own (an app wants the location, an app
        wants to place a call): `callback(yes)` once, with False when it is
        cancelled, replaced or closed."""
        self._answer(False)
        self.window_id = None
        self.callback = callback
        self.title.set_label(title)
        self.body.set_label(body or "")
        self.go.set_label(yes_label)
        self.cancel.set_label(no_label or _("Cancel"))
        for css in ("destructive", "destructive-action"):
            if destructive:
                self.go.add_css_class(css)
            else:
                self.go.remove_css_class(css)
        self.present()

    def _answer(self, yes):
        callback, self.callback = self.callback, None
        if callback is not None:
            try:
                callback(yes)
            except Exception as e:  # an answer handler must not break the sheet
                print("duo-shell: question answer failed: %r" % (e,), flush=True)
        confirm_id, self.confirm_id = self.confirm_id, None
        if confirm_id is None:
            return
        try:
            wc.request("confirm_answer", confirm=confirm_id, yes=yes)
            print("duo-shell: confirm %s: %s" % (confirm_id, "yes" if yes else "no"), flush=True)
        except (OSError, RuntimeError, ValueError) as e:
            print("duo-shell: confirm_answer: %s" % e, flush=True)

    def _cancel(self):
        self._answer(False)
        self.close_sheet()

    def _go(self):
        if self.callback is not None:
            self.close_sheet(keep_confirm=True)
            self._answer(True)
            return
        if self.confirm_id is not None:
            self.close_sheet(keep_confirm=True)
            self._answer(True)
            return
        window_id, self.window_id = self.window_id, None
        self.close_sheet()
        if window_id is None:
            return
        try:
            reply = wc.request("force_close_window", window=window_id)
            print(
                "duo-shell: force closed window %s (pid %s)"
                % (window_id, (reply or {}).get("pid")),
                flush=True,
            )
        except (OSError, RuntimeError, ValueError) as e:
            print("duo-shell: force close: %s" % e, flush=True)

    def closed_elsewhere(self, confirm_id):
        """duo-wc closed the question (timed out, another came)."""
        if self.confirm_id == confirm_id:
            self.confirm_id = None
            self.close_sheet()

    def close_sheet(self, keep_confirm=False):
        if not keep_confirm:
            self._answer(False)
        self.window_id = None
        if self.get_visible():
            GLib.idle_add(lambda: self.set_visible(False) and False)
