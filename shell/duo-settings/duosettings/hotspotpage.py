"""The Hotspot page: share the mobile data over WiFi (and a USB cable).

Everything goes through `sudo -n duo2-hotspot` (hotspot/README.md); turning
it on, USB tethering and a new password are asked of the user in the shell
first (the helper asks duo-wc's confirm: this page is an app, it has no
grant). The page reads the hotspot's state file itself (no password in it)
every few seconds while shown; the password only when "Show" or "Copy" is
tapped, and it is forgotten when the page closes. Pure parts: hotspot.py
(tests/test_hotspot.py)."""

import time

from gi.repository import Adw, GLib, Gtk

from . import hotspot as hs
from .main import (
    Page,
    _,
    ask,
    combo_row,
    inform,
    respond,
    run_async,
    run_with_input,
    select_quietly,
    set_quietly,
    switch_row,
)

ERRORS = {
    "airplane": _("Airplane mode is on."),
    "no-hostapd": _("The hotspot's software (hostapd) is not installed."),
    "no-wifi": _("The WiFi chip is not there."),
    "channel-not-shared": _(
        "The hotspot cannot run beside this WiFi connection. Switch WiFi off first, "
        "or allow it under “If WiFi cannot stay on”."
    ),
    "cut-not-allowed": _(
        "Switching WiFi off for the hotspot is not allowed on this phone (development mode)."
    ),
    "concurrent-failed": _("The WiFi chip did not start the hotspot beside the WiFi connection."),
    "no-channel": _("No WiFi channel may be used for a hotspot here."),
    "usb-ssh": _("A terminal session (ssh) runs over the USB cable."),
    "no-gadget": _("The phone's USB connection is not ready."),
    "not-confirmed": _("Not done: it was not confirmed."),
}


def error_text(reply):
    return ERRORS.get(reply.get("code")) or reply.get("error") or _("It did not work.")


class HotspotPage(Page):
    icon = "network-wireless-hotspot-symbolic"
    title = _("Hotspot")

    def build(self):
        self.st = {}
        self.busy = False
        self.password = None
        self.shown_password = False
        self.hide_timer = 0
        self.pw_dialog = None

        g = self.group(
            None,
            _(
                "Share the phone's mobile data with other devices. "
                "Devices on the hotspot use your mobile data."
            ),
        )
        self.switch = switch_row(_("WiFi hotspot"), _("Off"), False, self.toggle)
        g.add(self.switch)
        self.name_row = Adw.ActionRow(use_markup=False, title=_("Network name"), activatable=True)
        self.name_row.add_suffix(Gtk.Image.new_from_icon_name("document-edit-symbolic"))
        self.name_row.connect("activated", lambda _r: self.rename())
        g.add(self.name_row)
        self.pw_row = Adw.ActionRow(use_markup=False, title=_("Password"), subtitle=hs.masked(""))
        for icon, tip, cb in (
            ("view-reveal-symbolic", _("Show"), self.toggle_password),
            ("edit-copy-symbolic", _("Copy"), self.copy_password),
        ):
            b = Gtk.Button(icon_name=icon, valign=Gtk.Align.CENTER, tooltip_text=tip)
            b.add_css_class("flat")
            b.connect("clicked", lambda _b, cb=cb: cb())
            self.pw_row.add_suffix(b)
        menu = Gtk.MenuButton(icon_name="view-more-symbolic", valign=Gtk.Align.CENTER)
        menu.add_css_class("flat")
        menu.set_tooltip_text(_("Change"))
        pop = Gtk.Popover()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        for label, cb in (
            (_("New password"), self.new_password),
            (_("Your own password…"), self.own_password),
        ):
            b = Gtk.Button(label=label)
            b.add_css_class("flat")
            b.connect("clicked", lambda _b, cb=cb, p=pop: (p.popdown(), cb()))
            box.append(b)
        pop.set_child(box)
        menu.set_popover(pop)
        self.pw_row.add_suffix(menu)
        g.add(self.pw_row)
        self.band = combo_row(
            _("Band"),
            [_(label) for _v, label in hs.BANDS],
            0,
            self.set_band,
            _("While the phone itself is on WiFi, the hotspot uses that network's channel."),
        )
        g.add(self.band)
        self.security = combo_row(
            _("Security"), [_(label) for _v, label in hs.SECURITIES], 0, self.set_security
        )
        g.add(self.security)

        self.dev_group = self.group(_("Connected devices"))
        self.dev_rows = []

        g = self.group(_("USB"))
        self.usb = switch_row(
            _("USB tethering"),
            _(
                "Share mobile data with a computer on the USB cable. "
                "The USB connection drops for a moment when it starts "
                "or stops."
            ),
            False,
            self.toggle_usb,
        )
        g.add(self.usb)

        g = self.group(_("More"))
        self.client = combo_row(
            _("If WiFi cannot stay on"),
            [_(label) for _v, label in hs.WIFI_CLIENT],
            0,
            self.set_client,
        )
        g.add(self.client)
        self.idle = combo_row(
            _("Turn off with no device connected"),
            [_("Never") if m == 0 else _("After %d min") % m for m in hs.IDLE_OFF],
            hs.IDLE_OFF.index(10),
            self.set_idle,
        )
        g.add(self.idle)
        self.use5g = switch_row(
            _("Use 5G for the hotspot"),
            _("While devices are connected, the phone may use 5G: faster, more power."),
            True,
            self.set_5g,
        )
        g.add(self.use5g)
        self.data_row = Adw.ActionRow(use_markup=False, title=_("Shared this time"), subtitle="—")
        g.add(self.data_row)

    # -- the state ----------------------------------------------------------------------

    def shown(self):
        st = hs.read_state()
        if not st.get("settings"):
            # first use: the helper makes the name and the password
            self.helper(["status"], lambda r: self.show_state(r))
        self.every(3, self.refresh)
        self.every(10, self.refresh_bytes)

    def hidden(self):
        super().hidden()
        self.forget_password()

    def refresh(self):
        self.show_state(hs.read_state())

    def refresh_bytes(self):
        if hs.wifi_on(self.st) or hs.usb_on(self.st):
            self.helper(["status", "--bytes"], self._got_bytes)
        else:
            self.data_row.set_subtitle("—")

    def _got_bytes(self, reply):
        b = reply.get("shared_bytes")
        if isinstance(b, dict):
            self.data_row.set_subtitle(
                _("%(up)s sent, %(down)s received")
                % {"up": hs.fmt_bytes(b.get("out")), "down": hs.fmt_bytes(b.get("in"))}
            )

    def show_state(self, st):
        if not isinstance(st, dict):
            return
        self.st = st
        s = st.get("settings") or {}
        set_quietly(self.switch, hs.wifi_on(st))
        self.switch.set_subtitle(self.status_text(st))
        self.name_row.set_subtitle(s.get("ssid") or "—")
        select_quietly(self.band, hs.index_of(hs.BANDS, s.get("band")))
        select_quietly(self.security, hs.index_of(hs.SECURITIES, s.get("security")))
        select_quietly(self.client, hs.index_of(hs.WIFI_CLIENT, s.get("wifi_client")))
        never = bool(s.get("never_cut_wifi"))
        self.client.set_sensitive(not never)
        self.client.set_subtitle(
            _("Development mode: WiFi is never switched off.") if never else ""
        )
        select_quietly(self.idle, hs.idle_index(s.get("idle_off_min", 10)))
        set_quietly(self.use5g, bool(s.get("use_5g", True)))
        set_quietly(self.usb, hs.usb_on(st))
        for r in self.dev_rows:
            self.dev_group.remove(r)
        self.dev_rows = []
        rows = hs.device_rows(st, time.time())
        if not rows:
            row = Adw.ActionRow(use_markup=False, title=_("No devices"))
            self.dev_group.add(row)
            self.dev_rows.append(row)
        for name, (ip, via, minutes) in rows:
            bits = [ip] if ip else []
            bits.append(_("USB") if via == "usb" else _("WiFi"))
            if minutes is not None:
                bits.append(_("%d min") % minutes)
            row = Adw.ActionRow(
                use_markup=False, title=name or _("Device"), subtitle=" · ".join(bits)
            )
            row.add_prefix(
                Gtk.Image.new_from_icon_name(
                    "computer-symbolic" if via == "usb" else "phone-symbolic"
                )
            )
            self.dev_group.add(row)
            self.dev_rows.append(row)

    def status_text(self, st):
        texts = []
        for p in hs.status_parts(st):
            if p[0] == "off":
                texts.append(_("Off"))
            elif p[0] == "on":
                texts.append(_("On, %(band)s GHz, channel %(chan)s") % {"band": p[1], "chan": p[2]})
            elif p[0] == "beside":
                texts.append(_("beside your WiFi connection"))
            elif p[0] == "second":
                texts.append(_("beside your WiFi, on a channel of its own"))
            elif p[0] == "held":
                texts.append(_("the phone's own WiFi is off meanwhile"))
            elif p[0] == "no-data":
                texts.append(_("no mobile data"))
        return " · ".join(texts)

    # -- the helper ---------------------------------------------------------------------

    def helper(self, args, done=None):
        run_async(
            ["sudo", "-n", hs.HELPER] + list(args), lambda ok, out: done and done(hs.parse(out))
        )

    def act(self, args, after=None, quiet=False):
        """A change: the reply's error in a dialog, the state again."""
        if self.busy:
            return
        self.busy = True

        def done(reply):
            self.busy = False
            if not reply.get("ok") and not quiet:
                if after is None or not after(reply):
                    inform(self.window, _("Hotspot"), error_text(reply))
            elif after is not None and reply.get("ok"):
                after(reply)
            st = reply.get("state")
            self.show_state(st if isinstance(st, dict) and st.get("settings") else hs.read_state())

        self.helper(args, done)

    def toggle(self, on):
        if not on:
            self.act(["off"])
            return
        self.switch.set_subtitle(_("Confirm on the screen…"))
        self.act(["on"], after=self.maybe_cut)

    def maybe_cut(self, reply):
        """`on` refused because WiFi cannot stay on: ask (True = handled)."""
        if reply.get("ok") or reply.get("code") != "needs-wifi-off":
            return False
        ask(
            self.window,
            _("Switch WiFi off?"),
            _(
                "The hotspot cannot run beside this WiFi connection. While the hotspot is on, the phone "
                "itself uses mobile data, and a terminal session (ssh) over WiFi drops."
            ),
            _("Switch WiFi off"),
            lambda: self.act(["on", "--wifi-off"]),
            destructive=True,
        )
        return True

    def toggle_usb(self, on):
        if not on:
            self.act(["usb", "off"])
            return
        set_quietly(self.usb, False)

        def go(force=False):
            self.act(["usb", "on"] + (["--force"] if force else []), after=lambda r: ssh(r))

        def ssh(reply):
            if reply.get("code") != "usb-ssh":
                return False
            ask(
                self.window,
                _("A terminal session runs over USB"),
                _("It drops for a moment when USB tethering starts."),
                _("Start anyway"),
                lambda: go(True),
                destructive=True,
            )
            return True

        ask(
            self.window,
            _("Start USB tethering?"),
            _(
                "The computer on the USB cable gets internet through the phone's mobile data. The USB "
                "connection drops for a moment (a terminal session over it may break)."
            ),
            _("Start"),
            go,
        )

    def setting(self, name, value):
        self.act(["set", name, value])

    def set_band(self, i):
        self.setting("band", hs.BANDS[i][0])

    def set_security(self, i):
        self.setting("security", hs.SECURITIES[i][0])

    def set_client(self, i):
        self.setting("wifi-client", hs.WIFI_CLIENT[i][0])

    def set_idle(self, i):
        self.setting("idle-off", str(hs.IDLE_OFF[i]))

    def set_5g(self, on):
        self.setting("use-5g", "yes" if on else "no")

    def rename(self):
        entry = Gtk.Entry(text=(self.st.get("settings") or {}).get("ssid") or "", max_length=32)
        entry.set_activates_default(True)

        def ok():
            name = entry.get_text()
            if not hs.valid_ssid(name):
                inform(self.window, _("Network name"), _("A name of 1 to 32 characters."))
                return
            self.setting("ssid", name)

        ask(
            self.window,
            _("Network name"),
            _("Other devices see the hotspot by this name."),
            _("Save"),
            ok,
            extra=entry,
        )

    # -- the password -------------------------------------------------------------------

    def with_password(self, fn):
        if self.password is not None:
            fn(self.password)
            return

        def got(reply):
            if reply.get("ok") and reply.get("password"):
                self.password = reply["password"]
                self.arm_forget()
                fn(self.password)
            else:
                inform(self.window, _("Hotspot"), error_text(reply))

        self.helper(["password"], got)

    def toggle_password(self):
        if self.shown_password:
            self.shown_password = False
            self.pw_row.set_subtitle(hs.masked(self.password))
            return

        def show(pw):
            self.shown_password = True
            self.pw_row.set_subtitle(pw)

        self.with_password(show)

    def copy_password(self):
        def copy(pw):
            self.pw_row.get_clipboard().set(pw)
            self.toast(_("Copied"))

        self.with_password(copy)

    def arm_forget(self):
        if self.hide_timer:
            GLib.source_remove(self.hide_timer)
        self.hide_timer = GLib.timeout_add_seconds(60, self._forget_due)

    def _forget_due(self):
        self.hide_timer = 0
        self.forget_password()
        return False

    def forget_password(self):
        if self.hide_timer:
            GLib.source_remove(self.hide_timer)
            self.hide_timer = 0
        self.password = None
        self.shown_password = False
        self.pw_row.set_subtitle(hs.masked(""))

    def new_password(self):
        def done(reply):
            self.forget_password()
            if reply.get("ok"):
                self.toast(_("New password made"))

        ask(
            self.window,
            _("New password?"),
            _("Devices that know the old one have to join again."),
            _("Make a new one"),
            lambda: self.act(["password", "--new"], after=done),
        )

    def own_password(self):
        """A password of the user's own: Save only for one the helper takes
        (the dialog says what is wrong), then the helper with the password on
        stdin, after an empty line for the grant this app does not have (the
        shell asks the user)."""
        entry = Adw.PasswordEntryRow(title=_("New password"))
        box = Gtk.ListBox()
        box.add_css_class("boxed-list")
        box.append(entry)
        rule = _("8 to 63 characters: letters, digits and the usual signs (no accents).")
        hints = {
            "chars": _("Only letters, digits and the usual signs (no accents)."),
            "short": rule,
            "long": _("At most 63 characters."),
        }
        d = Adw.AlertDialog.new(_("Your own password"), rule)
        d.add_response("cancel", _("Cancel"))
        d.add_response("ok", _("Save"))
        d.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
        d.set_default_response("ok")
        d.set_close_response("cancel")
        d.set_response_enabled("ok", False)
        d.set_extra_child(box)

        def changed(_e):
            problem = hs.password_problem(entry.get_text())
            d.set_response_enabled("ok", problem is None)
            d.set_body(
                _("Devices that know the old one have to join again.")
                if problem is None
                else hints[problem]
            )

        entry.connect("changed", changed)
        entry.connect(
            "entry-activated", lambda _e: d.get_response_enabled("ok") and respond(d, "ok")
        )

        def response(_d, r):
            pw = entry.get_text()
            entry.set_text("")
            if r == "ok" and hs.valid_password(pw):
                self.save_password(pw)

        d.connect("response", response)
        d.present(self.window)
        entry.grab_focus()
        self.pw_dialog = d  # (tests/gtk_hotspot_page.py)
        return d

    def save_password(self, pw):
        def done(_code, out):
            self.forget_password()
            reply = hs.parse(out)
            if reply.get("ok"):
                self.toast(_("Password changed"))
            else:
                inform(self.window, _("Hotspot"), error_text(reply))

        # Line 1: no grant (an app has none: the shell asks); line 2: the password.
        run_with_input(["sudo", "-n", hs.HELPER, "password", "--set"], "\n" + pw + "\n", done)
