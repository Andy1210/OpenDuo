"""The settings pages."""

import os
import time

from gi.repository import Adw, Gio, GLib, Gtk

from . import bluetooth, conf, pen, region, sim, system, touchcal, wallpaper, wifi
from .main import (
    Page,
    SliderRow,
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
from .safetimer import guarded, keep_running_after

SIGNAL_ICONS = [
    "network-wireless-signal-none-symbolic",
    "network-wireless-signal-weak-symbolic",
    "network-wireless-signal-ok-symbolic",
    "network-wireless-signal-good-symbolic",
    "network-wireless-signal-excellent-symbolic",
]


def clear_group(group, rows):
    for r in rows:
        group.remove(r)
    rows.clear()


def rfkill_blocked(kind):
    base = "/sys/class/rfkill"
    try:
        for entry in os.listdir(base):
            with open(os.path.join(base, entry, "type")) as f:
                if f.read().strip() != kind:
                    continue
            with open(os.path.join(base, entry, "soft")) as f:
                return f.read().strip() == "1"
    except OSError:
        pass
    return None


# -- WiFi -------------------------------------------------------------------------


class WifiPage(Page):
    icon = "network-wireless-symbolic"
    title = _("Wi-Fi")

    def build(self):
        self.ctrl = wifi.WpaCtrl()
        self.connecting = None
        scan = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text=_("Search again"))
        scan.connect("clicked", lambda _b: self.scan())
        self.header.pack_end(scan)
        g = self.group()
        self.radio = switch_row(_("Wi-Fi"), on_toggle=self.set_radio)
        g.add(self.radio)
        self.current_group = self.group(_("Connected"))
        self.current_rows = []
        self.nets_group = self.group(_("Networks"))
        self.net_rows = []
        self.hint = Adw.ActionRow(use_markup=False, title=_("Searching..."))
        self.nets_group.add(self.hint)

    def shown(self):
        self.every(4, self.refresh)
        self.scan()
        self.timers.append(GLib.timeout_add_seconds(20, keep_running_after(self.scan)))

    def hidden(self):
        super().hidden()
        self.ctrl.close()

    def set_radio(self, on):
        run_async(
            ["sudo", "-n", system.SHELL_HELPER, "wifi", "on" if on else "off"],
            lambda ok, _o: None if ok else self.toast(_("Could not switch Wi-Fi")),
        )
        GLib.timeout_add(1500, lambda: self.refresh() and False)

    def scan(self):
        try:
            self.ctrl.request("SCAN")
        except OSError:
            return
        GLib.timeout_add(3500, lambda: self.refresh() and False)

    def refresh(self):
        blocked = rfkill_blocked("wlan")
        set_quietly(self.radio, blocked is False)
        if blocked or not self.ctrl.available():
            self.show([], {}, [])
            self.hint.set_title(_("Wi-Fi is off") if blocked else _("No Wi-Fi device"))
            self.hint.set_visible(True)
            return True
        try:
            status = wifi.parse_status(self.ctrl.request("STATUS"))
            saved = wifi.parse_networks(self.ctrl.request("LIST_NETWORKS"))
            scan = wifi.parse_scan(self.ctrl.request("SCAN_RESULTS"))
        except OSError:
            self.ctrl.close()
            return True
        signal = None
        if status.get("wpa_state") == "COMPLETED":
            try:
                for line in self.ctrl.request("SIGNAL_POLL").splitlines():
                    if line.startswith("RSSI="):
                        signal = int(line[5:])
            except (OSError, ValueError):
                pass
        status["_signal"] = signal
        self.show(scan, status, saved)
        self.hint.set_title(_("Searching...") if not scan else "")
        self.hint.set_visible(not scan)
        return True

    def show(self, scan, status, saved):
        clear_group(self.current_group, self.current_rows)
        clear_group(self.nets_group, self.net_rows)
        by_ssid = {n["ssid"]: n for n in saved}
        current = status.get("ssid") if status.get("wpa_state") == "COMPLETED" else None
        self.current_group.set_visible(bool(current))
        if current:
            row = Adw.ActionRow(
                use_markup=False,
                title=current,
                subtitle=_("Connected")
                + (" · %s" % status.get("ip_address") if status.get("ip_address") else ""),
            )
            sig = status.get("_signal")
            row.add_prefix(
                Gtk.Image.new_from_icon_name(
                    SIGNAL_ICONS[wifi.bars(sig)] if sig else SIGNAL_ICONS[4]
                )
            )
            forget = Gtk.Button(label=_("Forget"), valign=Gtk.Align.CENTER)
            forget.add_css_class("flat")
            forget.connect("clicked", lambda _b, s=current: self.forget(by_ssid.get(s)))
            row.add_suffix(forget)
            self.current_group.add(row)
            self.current_rows.append(row)
        for net in scan:
            if net["ssid"] == current:
                continue
            known = net["ssid"] in by_ssid
            row = Adw.ActionRow(use_markup=False, title=net["ssid"])
            row.set_title_lines(1)
            sub = []
            if known:
                sub.append(_("Saved"))
            if net["security"] == "enterprise":
                sub.append(_("Enterprise (not supported here)"))
            if self.connecting == net["ssid"]:
                sub.append(_("Connecting..."))
            if sub:
                row.set_subtitle(" · ".join(sub))
            row.add_prefix(Gtk.Image.new_from_icon_name(SIGNAL_ICONS[wifi.bars(net["signal"])]))
            if net["security"] != "open":
                row.add_suffix(Gtk.Image.new_from_icon_name("system-lock-screen-symbolic"))
            row.set_activatable(net["security"] not in ("enterprise", "wep"))
            row.connect("activated", lambda _r, n=net: self.choose(n, by_ssid.get(n["ssid"])))
            self.nets_group.add(row)
            self.net_rows.append(row)

    def choose(self, net, saved):
        if saved is not None:
            if saved.get("temp_disabled") and net["security"] != "open":
                self.ask_password(net, retry=saved)
            else:
                self.join(net["ssid"], saved["id"], new=False)
        elif net["security"] == "open":
            self.add_and_join(net, "")
        else:
            self.ask_password(net)

    def ask_password(self, net, retry=None):
        entry = Adw.PasswordEntryRow(title=_("Password"))
        box = Gtk.ListBox()
        box.add_css_class("boxed-list")
        box.append(entry)
        d = Adw.AlertDialog.new(
            _("Connect to %s") % net["ssid"], _("Wrong password last time.") if retry else None
        )
        d.add_response("cancel", _("Cancel"))
        d.add_response("ok", _("Connect"))
        d.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
        d.set_default_response("ok")
        d.set_close_response("cancel")
        d.set_response_enabled("ok", False)
        d.set_extra_child(box)

        def changed(_e):
            d.set_response_enabled(
                "ok", wifi.passphrase_problem(entry.get_text(), net["security"]) is None
            )

        entry.connect("changed", changed)
        entry.connect(
            "entry-activated", lambda _e: d.get_response_enabled("ok") and respond(d, "ok")
        )

        def response(_d, r):
            text = entry.get_text()
            entry.set_text("")
            if r == "ok":
                if retry is not None:
                    self.request_ok("REMOVE_NETWORK %d" % retry["id"])
                self.add_and_join(net, text)

        d.connect("response", response)
        d.present(self.window)
        entry.grab_focus()

    def request_ok(self, cmd):
        try:
            return self.ctrl.ok(cmd)
        except OSError:
            return False

    def add_and_join(self, net, passphrase):
        try:
            nid = int(self.ctrl.request("ADD_NETWORK").strip())
            for cmd in wifi.network_commands(nid, net["ssid"], net["security"], passphrase):
                if not self.ctrl.ok(cmd):
                    raise ValueError
        except (OSError, ValueError):
            self.toast(_("Could not add the network"))
            return
        finally:
            passphrase = None
        self.join(net["ssid"], nid, new=True)

    def join(self, ssid, nid, new):
        if not self.request_ok("SELECT_NETWORK %d" % nid):
            self.toast(_("Could not connect"))
            return
        self.connecting = ssid
        started = time.monotonic()

        def check():
            try:
                status = wifi.parse_status(self.ctrl.request("STATUS"))
                nets = {n["id"]: n for n in wifi.parse_networks(self.ctrl.request("LIST_NETWORKS"))}
            except OSError:
                status, nets = {}, {}
            if status.get("wpa_state") == "COMPLETED" and status.get("ssid") == ssid:
                self.connecting = None
                # SELECT_NETWORK disabled the others: enable them again, keep it.
                self.request_ok("ENABLE_NETWORK all")
                self.request_ok("SAVE_CONFIG")
                self.toast(_("Connected to %s") % ssid)
                self.refresh()
                return False
            failed = nets.get(nid, {}).get("temp_disabled") or time.monotonic() - started > 25
            if failed:
                self.connecting = None
                if new:
                    self.request_ok("REMOVE_NETWORK %d" % nid)
                self.request_ok("ENABLE_NETWORK all")
                self.request_ok("REASSOCIATE")
                self.toast(
                    _("Could not connect to %s") % ssid
                    if not new
                    else _("Could not connect: wrong password?")
                )
                self.refresh()
                return False
            return True

        GLib.timeout_add(700, check)
        self.refresh()

    def forget(self, saved):
        if saved is None:
            return

        def go():
            if self.request_ok("REMOVE_NETWORK %d" % saved["id"]):
                self.request_ok("SAVE_CONFIG")
                self.request_ok("REASSOCIATE")
                self.toast(_("Forgot %s") % saved["ssid"])
            self.refresh()

        ask(
            self.window,
            _("Forget %s?") % saved["ssid"],
            _("The phone will no longer join it by itself, and its password is deleted."),
            _("Forget"),
            go,
            destructive=True,
        )


# -- Bluetooth --------------------------------------------------------------------


class BluetoothPage(Page):
    icon = "bluetooth-active-symbolic"
    title = _("Bluetooth")

    def build(self):
        self.bt = None
        self.dialog = None
        g = self.group()
        self.power = switch_row(_("Bluetooth"), on_toggle=self.set_power)
        g.add(self.power)
        self.spinner = Adw.Spinner()
        self.header.pack_end(self.spinner)
        self.paired_group = self.group(_("My devices"))
        self.paired_rows = []
        self.near_group = self.group(
            _("Nearby devices"), _("Put the device in pairing mode, then tap it.")
        )
        self.near_rows = []
        self.empty = Adw.ActionRow(use_markup=False, title=_("Searching..."))
        self.near_group.add(self.empty)

    def shown(self):
        if self.bt is None:
            self.bt = bluetooth.shared(self.changed_soon, self)
        self.bt.ui = self
        self.bt.reload()
        self.update()
        self.bt.start_discovery()
        # Discovery stops when the page is left; renewed while it is open.
        self.timers.append(
            GLib.timeout_add_seconds(30, keep_running_after(lambda: self.bt.start_discovery()))
        )

    def hidden(self):
        super().hidden()
        if self.bt is not None:
            self.bt.stop_discovery()

    def close(self):
        if self.bt is not None:
            self.bt.close()

    def changed_soon(self):
        if not getattr(self, "_queued", False):
            self._queued = True
            GLib.timeout_add(250, self._changed)

    def _changed(self):
        self._queued = False
        self.update()
        return False

    def set_power(self, on):
        if not (self.bt and self.bt.available()):
            return
        if on and rfkill_blocked("bluetooth"):
            # Blocked (airplane mode): BlueZ cannot power it until the
            # block goes (duo-shell's helper), then it powers it itself.
            def freed(ok, _out):
                if not ok:
                    self.toast(_("Could not switch Bluetooth on"))
                    return
                self.bt.set_powered(True)
                GLib.timeout_add(1500, lambda: self.bt.start_discovery() and False)

            run_async(["sudo", "-n", system.SHELL_HELPER, "bluetooth", "on"], freed)
            return
        self.bt.set_powered(on)
        if on:
            GLib.timeout_add(1500, lambda: self.bt.start_discovery() and False)

    def update(self):
        if self.bt is None:
            return
        avail = self.bt.available()
        powered = avail and self.bt.powered()
        self.power.set_sensitive(avail)
        set_quietly(self.power, powered)
        self.spinner.set_visible(powered and self.bt.discovering())
        clear_group(self.paired_group, self.paired_rows)
        clear_group(self.near_group, self.near_rows)
        devices = self.bt.devices() if powered else []
        paired = [d for d in devices if d["paired"]]
        near = [d for d in devices if not d["paired"] and d["named"]]
        self.paired_group.set_visible(bool(paired))
        for d in paired:
            row = Adw.ActionRow(use_markup=False, title=d["alias"], subtitle=pen.device_subtitle(d))
            row.add_prefix(Gtk.Image.new_from_icon_name(icon_for(d["icon"])))
            row.add_suffix(Gtk.Image.new_from_icon_name("go-next-symbolic"))
            row.set_activatable(True)
            row.connect("activated", lambda _r, dev=d: self.device_menu(dev))
            self.paired_group.add(row)
            self.paired_rows.append(row)
        for d in near:
            row = Adw.ActionRow(use_markup=False, title=d["alias"])
            row.add_prefix(Gtk.Image.new_from_icon_name(icon_for(d["icon"])))
            row.set_activatable(True)
            row.connect("activated", lambda _r, dev=d: self.pair(dev))
            self.near_group.add(row)
            self.near_rows.append(row)
        self.empty.set_visible(not near)
        self.empty.set_title(
            _("Bluetooth is off")
            if avail and not powered
            else _("No Bluetooth adapter")
            if not avail
            else _("Searching...")
        )

    def pair(self, dev):
        self.toast(_("Pairing with %s...") % dev["alias"])
        self.bt.pair(
            dev["path"],
            lambda err: self.toast(
                _("Paired with %s") % dev["alias"] if err is None else _("Pairing failed: %s") % err
            ),
        )

    def device_menu(self, dev):
        d = Adw.AlertDialog.new(dev["alias"], dev["address"])
        d.add_response("cancel", _("Close"))
        d.add_response("forget", _("Forget"))
        d.set_response_appearance("forget", Adw.ResponseAppearance.DESTRUCTIVE)
        if dev["connected"]:
            d.add_response("disconnect", _("Disconnect"))
        else:
            d.add_response("connect", _("Connect"))
            d.set_response_appearance("connect", Adw.ResponseAppearance.SUGGESTED)
        d.set_close_response("cancel")

        def done(what):
            return lambda err: self.toast(err if err else what)

        def response(_d, r):
            if r == "connect":
                self.bt.connect(dev["path"], done(_("Connected")))
            elif r == "disconnect":
                self.bt.disconnect(dev["path"], done(_("Disconnected")))
            elif r == "forget":
                self.bt.forget(dev["path"], done(_("Forgot %s") % dev["alias"]))

        d.connect("response", response)
        d.present(self.window)

    # The pairing agent's questions (bluetooth.Bluez calls these).
    def _code_dialog(self, heading, body, on_answer, with_entry=False, digits=False):
        entry = None
        d = Adw.AlertDialog.new(heading, body)
        d.add_response("cancel", _("Cancel"))
        d.add_response("ok", _("Pair"))
        d.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
        d.set_close_response("cancel")
        if with_entry:
            entry = Gtk.Entry(
                input_purpose=Gtk.InputPurpose.DIGITS if digits else Gtk.InputPurpose.FREE_FORM
            )
            d.set_extra_child(entry)
        d.connect(
            "response", lambda _d, r: on_answer(r == "ok", entry.get_text() if entry else None)
        )
        self.dialog = d
        d.present(self.window)

    def request_pin(self, name, done):
        self._code_dialog(
            _("Pair with %s") % name,
            _("Type the PIN code the device shows or uses (often 0000)."),
            lambda ok, text: done(text if ok and text else None),
            with_entry=True,
        )

    def request_passkey(self, name, done):
        def answer(ok, text):
            done(text if ok and text and text.isdigit() and int(text) < 1000000 else None)

        self._code_dialog(
            _("Pair with %s") % name,
            _("Type the 6-digit code the device shows."),
            answer,
            with_entry=True,
            digits=True,
        )

    def confirm(self, name, passkey, done):
        label = Gtk.Label(label=passkey)
        label.add_css_class("code-label")
        d = Adw.AlertDialog.new(
            _("Pair with %s?") % name, _("Check that the device shows the same code.")
        )
        d.set_extra_child(label)
        d.add_response("cancel", _("Cancel"))
        d.add_response("ok", _("Pair"))
        d.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
        d.set_close_response("cancel")
        d.connect("response", lambda _d, r: done(r == "ok"))
        self.dialog = d
        d.present(self.window)

    def authorize(self, name, done):
        ask(self.window, _("Pair with %s?") % name, None, _("Pair"), lambda: done(True))

    def show_code(self, name, code):
        label = Gtk.Label(label=code)
        label.add_css_class("code-label")
        d = Adw.AlertDialog.new(_("Pairing with %s") % name, _("Type this code on the device."))
        d.set_extra_child(label)
        d.add_response("ok", _("OK"))
        self.dialog = d
        d.present(self.window)

    def cancel(self):
        if self.dialog is not None:
            self.dialog.force_close()
            self.dialog = None


def icon_for(bluez_icon):
    names = {
        "audio-headset": "audio-headset-symbolic",
        "audio-headphones": "audio-headphones-symbolic",
        "audio-card": "audio-speakers-symbolic",
        "input-keyboard": "input-keyboard-symbolic",
        "input-mouse": "input-mouse-symbolic",
        "input-gaming": "input-gaming-symbolic",
        "phone": "phone-symbolic",
        "computer": "computer-symbolic",
        "input-tablet": "input-tablet-symbolic",
    }
    return names.get(bluez_icon, "bluetooth-active-symbolic")


# -- Display ------------------------------------------------------------------------


class DisplayPage(Page):
    icon = "video-display-symbolic"
    title = _("Display")

    def build(self):
        g = self.group()
        self.brightness = SliderRow(
            _("Brightness"),
            "display-brightness-symbolic",
            "display-brightness-symbolic",
            self.set_brightness,
            1,
            100,
        )
        g.add(self.brightness)
        # duo-brightness: the slider then sets the user's offset on the light curve.
        self.auto_brightness = switch_row(
            _("Automatic brightness"),
            _("Follows the light around the phone; the slider sets how bright"),
            on_toggle=self.set_auto_brightness,
        )
        g.add(self.auto_brightness)
        self.idle = combo_row(
            _("Screen off after"),
            [system.idle_label(s, _) for s in system.IDLE_CHOICES],
            on_select=self.set_idle,
        )
        g.add(self.idle)

        g = self.group(_("Panels"))
        self.refresh_row = combo_row(
            _("Refresh rate"),
            ["60 Hz", "90 Hz"],
            on_select=self.set_refresh,
            subtitle=_("90 Hz is smoother and uses a little more power"),
        )
        g.add(self.refresh_row)
        self.scale_row = combo_row(
            _("Scale"),
            [system.scale_label(s) for s in system.SCALES],
            on_select=self.set_scale,
            subtitle=_("Larger makes everything bigger"),
        )
        g.add(self.scale_row)
        self.gap = switch_row(
            _("Hinge gap"),
            _("Spanned content continues behind the hinge, as if the screen went on under it"),
            on_toggle=self.set_gap,
        )
        g.add(self.gap)
        self.restart_group = self.group()
        row = Adw.ActionRow(
            use_markup=False,
            title=_("Restart the display to apply"),
            subtitle=_("Open apps will close"),
        )
        btn = Gtk.Button(label=_("Restart"), valign=Gtk.Align.CENTER)
        btn.add_css_class("suggested-action")
        btn.connect("clicked", lambda _b: self.restart_display())
        row.add_suffix(btn)
        self.restart_group.add(row)
        self.restart_group.set_visible(False)
        self.applied = None

    def shown(self):
        b = system.brightness_percent()
        self.brightness.set_visible(b is not None)
        if b is not None:
            self.brightness.set_value(b)
        self.auto_brightness.set_visible(
            b is not None and os.path.exists("/usr/local/bin/duo-brightness")
        )
        set_quietly(
            self.auto_brightness, conf.is_on(conf.read("/etc/duo2/brightness.conf").get("auto"))
        )
        st = system.wc_state()
        cfg = system.wc_conf()
        idle = st.get("idle_timeout", int(cfg.get("idle_timeout", 60) or 60))
        select_quietly(
            self.idle, system.IDLE_CHOICES.index(idle) if idle in system.IDLE_CHOICES else 1
        )
        rate = system.refresh_rate()
        select_quietly(self.refresh_row, 0 if rate == 60 else 1)
        self.refresh_row.set_sensitive(rate is not None)
        scale = system.normalise_scale(st.get("scale", cfg.get("scale", "2.5")))
        select_quietly(self.scale_row, system.SCALES.index(scale))
        set_quietly(self.gap, st.get("hinge_gap", cfg.get("hinge_gap", "on")) == "on")
        if self.applied is None:
            self.applied = (rate, scale)

    def set_brightness(self, v):
        run_async(["sudo", "-n", system.SHELL_HELPER, "brightness", str(v)])

    def set_auto_brightness(self, on):
        run_async(["sudo", "-n", system.SHELL_HELPER, "auto-brightness", "on" if on else "off"])

    def set_idle(self, i):
        secs = system.IDLE_CHOICES[i]
        # The helper saves it and applies it live (duo-wc takes no setting
        # from an app directly: docs/hardware/security.md).
        run_async(
            ["sudo", "-n", system.HELPER, "wc-conf", "idle_timeout", str(secs)],
            lambda ok, _o: None if ok else self.toast(_("Could not save the setting")),
        )

    def set_gap(self, on):
        run_async(["sudo", "-n", system.HELPER, "wc-conf", "hinge_gap", "on" if on else "off"])

    def pending(self):
        rate = 60 if self.refresh_row.get_selected() == 0 else 90
        scale = system.SCALES[self.scale_row.get_selected()]
        live_scale = system.normalise_scale(system.wc_state().get("scale", scale))
        self.restart_group.set_visible(rate != self.applied[0] or scale != live_scale)

    def set_refresh(self, i):
        rate = "60" if i == 0 else "90"
        run_async(
            ["sudo", "-n", system.HELPER, "refresh", rate],
            lambda ok, _o: (
                self.pending() if ok else self.toast(_("Could not change the refresh rate"))
            ),
        )

    def set_scale(self, i):
        run_async(
            ["sudo", "-n", system.HELPER, "wc-conf", "scale", system.SCALES[i]],
            lambda ok, _o: self.pending() if ok else self.toast(_("Could not save the setting")),
        )

    def restart_display(self):
        # The shell asks the user (the question no app can answer for him:
        # duo-wc's confirm), then duo-wc restarts.
        run_async(
            ["sudo", "-n", system.HELPER, "restart-display"],
            lambda ok, _o: None if ok else self.toast(_("Could not restart the display")),
        )


# -- Sound ----------------------------------------------------------------------------


class SoundPage(Page):
    icon = "audio-volume-high-symbolic"
    title = _("Sound")

    def build(self):
        g = self.group()
        self.volume = SliderRow(
            _("Volume"), "audio-volume-low-symbolic", "audio-volume-high-symbolic", self.set_volume
        )
        g.add(self.volume)
        self.mute = switch_row(_("Mute"), on_toggle=lambda on: system.set_mute(on))
        g.add(self.mute)
        g = self.group(_("Output"))
        self.outputs = []
        self.output_row = combo_row(_("Play sound through"), [], on_select=self.set_output)
        g.add(self.output_row)
        from .ringtonegroup import RingtoneGroup

        self.ringtone = RingtoneGroup(self)
        from .vibration import VibrationGroup

        self.vibration = VibrationGroup(self)

    def shown(self):
        self.vibration.refresh()
        self.ringtone.refresh()
        self.every(3, self.refresh)

    def hidden(self):
        self.ringtone.stop()
        super().hidden()

    def refresh(self):
        vol, muted = system.volume()
        if vol is not None and self.volume._pending is None:
            self.volume.set_value(vol)
        set_quietly(self.mute, muted)
        sinks = system.sinks()
        if [s[0] for s in sinks] != [s[0] for s in self.outputs]:
            self.outputs = sinks
            self.output_row._quiet = True
            self.output_row.set_model(Gtk.StringList.new([s[1] for s in sinks]))
            self.output_row._quiet = False
        default = system.default_sink()
        for i, (name, _d) in enumerate(self.outputs):
            if name == default:
                select_quietly(self.output_row, i)
        self.output_row.set_sensitive(len(self.outputs) > 1)
        return True

    def set_volume(self, v):
        system.set_volume(v)

    def set_output(self, i):
        if 0 <= i < len(self.outputs):
            system.set_default_sink(self.outputs[i][0])


# -- Notifications ------------------------------------------------------------------


class NotificationsPage(Page):
    icon = "preferences-system-notifications-symbolic"
    title = _("Notifications")

    def build(self):
        self.settings = Gio.Settings.new("org.gnome.desktop.notifications")
        g = self.group(
            None,
            _(
                "Notifications still arrive and stay in the list in the quick menu. "
                "Alarms always ring."
            ),
        )
        self.dnd = switch_row(
            _("Do not disturb"),
            _("No banners, no sounds or vibration for notifications and calls"),
            on_toggle=lambda on: self.settings.set_boolean("show-banners", not on),
        )
        g.add(self.dnd)
        g = self.group(_("Calls that ring anyway"))
        self.repeat = switch_row(
            _("Repeated calls"),
            _("Someone who called in the last 15 minutes"),
            on_toggle=lambda on: self.set_pref("repeat_callers", on),
        )
        self.contacts = switch_row(
            _("Calls from contacts"),
            _("Callers in your address book"),
            on_toggle=lambda on: self.set_pref("contacts", on),
        )
        g.add(self.repeat)
        g.add(self.contacts)
        self.settings.connect("changed::show-banners", lambda *_a: self.shown())

    def shown(self):
        from . import dndprefs

        set_quietly(self.dnd, not self.settings.get_boolean("show-banners"))
        prefs = dndprefs.load()
        set_quietly(self.repeat, prefs["repeat_callers"])
        set_quietly(self.contacts, prefs["contacts"])

    def set_pref(self, key, on):
        from . import dndprefs

        prefs = dndprefs.load()
        prefs[key] = bool(on)
        try:
            dndprefs.save(prefs)
        except OSError:
            self.toast(_("Could not save the setting"))


# -- Rotation and poses ---------------------------------------------------------------


class RotationPage(Page):
    """The sensors' rules and the panels in use. One canvas: there is no
    dual / span pose to pick any more (a window spans by itself, a double
    tap on its top); what is left is whether the sensors turn the picture
    and pick the panels, the hinge gap, and the user's own choice of panel,
    which wins over the sensors (duo-posed's `panels`)."""

    icon = "object-rotate-right-symbolic"
    title = _("Rotation & poses")

    PANELS = (("both", _("Both")), ("left", _("Left only")), ("right", _("Right only")))

    def build(self):
        g = self.group(
            None,
            _(
                "The sensors (duo-posed) turn the screens and pick the panels "
                "from how the phone is held and folded."
            ),
        )
        self.enabled = switch_row(
            _("Use the sensors"), on_toggle=lambda on: self.posed("enabled", on)
        )
        g.add(self.enabled)
        self.rotate = switch_row(
            _("Auto-rotate"),
            _("The picture turns with the phone"),
            on_toggle=lambda on: self.posed("auto_rotate", on),
        )
        g.add(self.rotate)
        self.pose = switch_row(
            _("Automatic poses"),
            _("One panel when folded back or stood up, both when open"),
            on_toggle=lambda on: self.posed("auto_pose", on),
        )
        g.add(self.pose)
        self.gap = switch_row(
            _("Hinge gap"),
            _("A window across both panels continues behind the hinge"),
            on_toggle=lambda on: run_async(
                ["sudo", "-n", system.HELPER, "wc-conf", "hinge_gap", "on" if on else "off"]
            ),
        )
        g.add(self.gap)
        g = self.group(
            _("Panels"),
            _(
                "Left or right only keeps the other panel dark, whatever "
                "the sensors say; a closed phone still goes dark."
            ),
        )
        row = Adw.PreferencesRow()
        row.set_activatable(False)
        box = Gtk.Box(
            spacing=6, homogeneous=True, margin_top=8, margin_bottom=8, margin_start=8, margin_end=8
        )
        self.panel_buttons = {}
        self._quiet = False
        group = None
        for key, label in self.PANELS:
            b = Gtk.ToggleButton(label=label)
            b.set_size_request(-1, 48)
            if group:
                b.set_group(group)
            group = group or b
            b.connect(
                "toggled",
                lambda btn, k=key: btn.get_active() and not self._quiet and self.set_panels(k),
            )
            box.append(b)
            self.panel_buttons[key] = b
        row.set_child(box)
        g.add(row)

    def shown(self):
        c = system.posed_conf()
        on = conf.is_on(c.get("enabled"))
        set_quietly(self.enabled, on)
        set_quietly(self.rotate, conf.is_on(c.get("auto_rotate")))
        set_quietly(self.pose, conf.is_on(c.get("auto_pose"), True))
        self.rotate.set_sensitive(on)
        self.pose.set_sensitive(on)
        st = system.wc_state()
        set_quietly(self.gap, st.get("hinge_gap", "on") == "on")
        key = c.get("panels", "both")
        self._quiet = True
        self.panel_buttons.get(key, self.panel_buttons["both"]).set_active(True)
        self._quiet = False

    def posed(self, key, on):
        def done(ok, _o):
            if not ok:
                self.toast(_("Could not save the setting"))
            self.shown()

        run_async(["sudo", "-n", system.HELPER, "posed", key, "on" if on else "off"], done)

    def set_panels(self, key):
        def done(ok, _o):
            if not ok:
                self.toast(_("Could not save the setting"))
            self.shown()

        run_async(["sudo", "-n", system.HELPER, "posed", "panels", key], done)


# -- Lock -------------------------------------------------------------------------------


class LockPage(Page):
    """The PIN and when to lock. Root keeps the PIN (duo-lock-helper, the
    one door to /var/lib/duo2-lock): this page reads the policy (no secret)
    and hands every change to the helper, with the current PIN when one is
    set; so no other app can take the PIN away or switch the lock off.

    Encrypt my data (homecrypt/README.md): the home is encrypted with a key
    the PIN opens, at the next restart; the page shows root's public state
    file and asks the helper (`home enable|disable|cancel|recovery-key`)."""

    icon = "system-lock-screen-symbolic"
    title = _("Lock screen")

    def build(self):
        g = self.group(
            _("PIN"),
            _(
                "The lock screen asks for this PIN. Without a PIN the phone "
                "does not lock when the screen goes off."
            ),
        )
        self.pin_row = Adw.ActionRow(use_markup=False, title=_("PIN"))
        self.set_btn = Gtk.Button(valign=Gtk.Align.CENTER)
        self.set_btn.connect("clicked", lambda _b: self.change_pin())
        self.pin_row.add_suffix(self.set_btn)
        self.clear_btn = Gtk.Button(label=_("Remove"), valign=Gtk.Align.CENTER)
        self.clear_btn.add_css_class("destructive-action")
        self.clear_btn.connect("clicked", lambda _b: self.remove_pin())
        self.pin_row.add_suffix(self.clear_btn)
        g.add(self.pin_row)
        g = self.group(_("When to lock"))
        self.on_blank = switch_row(
            _("Lock when the screen turns off"),
            on_toggle=lambda on: self.set_rule("lock_on_blank", on),
        )
        g.add(self.on_blank)
        self.on_close = switch_row(
            _("Lock when folded closed"),
            _("A swipe unlocks it when no PIN is set"),
            on_toggle=lambda on: self.set_rule("lock_on_close", on),
        )
        g.add(self.on_close)

        g = self.group(
            _("Data encryption"),
            _(
                "Your files, settings, messages and Android's data are encrypted with a key "
                "only your PIN opens. After a restart, calls, alarms and the lock screen work "
                "at once; the rest waits until you enter the PIN."
            ),
        )
        self.enc = switch_row(_("Encrypt my data"), on_toggle=self.toggle_encryption)
        g.add(self.enc)
        self.enc_pending = Adw.ActionRow(use_markup=False, title=_("Waiting for a restart"))
        cancel = Gtk.Button(label=_("Cancel"), valign=Gtk.Align.CENTER)
        cancel.connect("clicked", lambda _b: self.cancel_encryption_change())
        restart = Gtk.Button(label=_("Restart now"), valign=Gtk.Align.CENTER)
        restart.add_css_class("suggested-action")
        restart.connect("clicked", lambda _b: self.offer_restart())
        self.enc_pending.add_suffix(cancel)
        self.enc_pending.add_suffix(restart)
        g.add(self.enc_pending)
        self.recovery_row = Adw.ActionRow(use_markup=False, title=_("Recovery key"))
        new_key = Gtk.Button(label=_("New key"), valign=Gtk.Align.CENTER)
        new_key.connect("clicked", lambda _b: self.new_recovery_key())
        self.recovery_row.add_suffix(new_key)
        g.add(self.recovery_row)

    def shown(self):
        p = system.lock_policy()
        has_pin = p["pin"] == "yes"
        self.home = system.home_state()
        v = system.home_view(self.home, has_pin)
        set_quietly(self.enc, v["active"])
        self.enc.set_sensitive(v["sensitive"])
        self.enc.set_subtitle(v["subtitle"])
        self.enc_pending.set_visible(v["restart"])
        self.recovery_row.set_visible(v["recovery_row"])
        self.recovery_row.set_subtitle(
            _("Made: keep it written down, away from the phone")
            if self.home.get("recovery") == "yes"
            else _("None: a forgotten PIN means your data is gone")
        )
        self.pin_row.set_subtitle(_("Set") if has_pin else _("Not set"))
        self.set_btn.set_label(_("Change") if has_pin else _("Set PIN"))
        self.set_btn.remove_css_class("suggested-action")
        if not has_pin:
            self.set_btn.add_css_class("suggested-action")
        self.clear_btn.set_visible(has_pin)
        self.on_blank.set_sensitive(has_pin)
        self.on_blank.set_subtitle("" if has_pin else _("Set a PIN first"))
        set_quietly(self.on_blank, has_pin and p["lock_on_blank"] == "on")
        set_quietly(self.on_close, p["lock_on_close"] == "on")

    def helper(self, args, stdin, saved, after=None):
        """duo-lock-helper ARGS with `stdin`; `saved` is the toast on success."""

        def done(code, out):
            ok, problem = system.lock_answer(code, out)
            if ok:
                if saved:
                    self.toast(saved)
                if after:
                    after()
            elif problem == "wrong":
                self.toast(_("The current PIN is wrong; nothing changed"))
            elif problem and problem.startswith("wait"):
                self.toast(_("Too many wrong PINs: try again in %s s") % problem.split()[1])
            elif problem == "encrypted":
                self.toast(
                    _("Your data is encrypted with this PIN: turn off Encrypt my data first")
                )
            elif problem and problem.startswith("refused "):
                self.toast(problem[len("refused ") :])
            else:
                self.toast(_("Could not save the setting"))
            self.shown()

        run_with_input(["sudo", "-n", system.LOCK_HELPER] + args, stdin, done)

    # -- data encryption ------------------------------------------------------------------

    def toggle_encryption(self, on):
        state = system.home_state()["state"]
        self.shown()  # the switch follows the state, not the tap
        if on and state == "plain":
            self.enable_encryption()
        elif not on and state == "encrypted":
            self.disable_encryption()
        elif state in ("encrypt-pending", "decrypt-pending"):
            self.cancel_encryption_change()

    def enable_encryption(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        recovery = Gtk.CheckButton(label=_("Make a recovery key (recommended)"), active=True)
        box.append(recovery)
        body = "\n\n".join(
            [
                _("Your data is encrypted with your PIN while the phone restarts."),
                _(
                    "If you forget the PIN, your data is gone. Only the recovery key can open "
                    "it then: it is shown once, write it down and keep it away from the phone."
                ),
                _(
                    "The restart copies your data once: it needs as much free space as your "
                    "files take, and a few minutes. Keep the phone charging."
                ),
                _(
                    "Without special hardware the phone cannot slow down guessing: someone "
                    "with your phone and the right tools can try a short PIN. A PIN of 8 "
                    "digits or more protects much better."
                ),
                _(
                    "Files deleted before now may still be readable from the storage chips "
                    "by experts."
                ),
            ]
        )

        def go():
            self._pin_dialog(
                _("Enter the PIN"),
                [_("PIN")],
                lambda texts: self.home_helper(
                    ["enable", "recovery" if recovery.get_active() else "norecovery"],
                    texts[0],
                    self.encryption_prepared,
                ),
            )

        ask(self.window, _("Encrypt your data?"), body, _("Continue"), go, extra=box)

    def home_helper(self, args, text, after):
        """duo-lock-helper home ARGS with `text` on stdin; after(output) on
        success."""

        def done(code, out):
            ok, problem = system.lock_answer(code, out)
            if ok:
                after(out)
            elif problem == "wrong":
                self.toast(_("The PIN is wrong; nothing changed"))
            elif problem and problem.startswith("wait"):
                self.toast(_("Too many wrong PINs: try again in %s s") % problem.split()[1])
            elif problem and problem.startswith("refused "):
                self.toast(problem[len("refused ") :])
            else:
                self.toast(_("It did not work; nothing changed"))
            self.shown()

        run_with_input(["sudo", "-n", system.LOCK_HELPER, "home"] + args, text + "\n", done)

    @staticmethod
    def recovery_in(out):
        for line in (out or "").splitlines():
            if line.startswith("recovery "):
                return line.split(None, 1)[1].strip()
        return None

    def encryption_prepared(self, out):
        # The lock screen asks for the PIN at every start now: duo-wc's lock
        # protocol on (as for "lock when the screen turns off").
        run_async(["sudo", "-n", system.HELPER, "wc-conf", "session_lock", "on"])
        key = self.recovery_in(out)
        if key:
            self.show_recovery_key(key, self.offer_restart)
        else:
            self.offer_restart()

    def show_recovery_key(self, key, then=None):
        groups = key.split("-")
        shown = "\n".join(" ".join(groups[i : i + 3]) for i in range(0, len(groups), 3))
        label = Gtk.Label(label=shown, justify=Gtk.Justification.CENTER)
        label.add_css_class("title-2")
        label.add_css_class("monospace")
        d = Adw.AlertDialog.new(
            _("Your recovery key"),
            _(
                "Write it down now and keep it somewhere safe, away from the phone: it is "
                "shown only this once. If you forget the PIN, type it on the lock screen's "
                "keypad instead of the PIN."
            ),
        )
        d.set_extra_child(label)
        d.add_response("ok", _("I wrote it down"))
        d.set_close_response("ok")
        d.connect("response", lambda _d, _r: then() if then else None)
        d.present(self.window)

    def offer_restart(self):
        state = system.home_state()["state"]
        body = (
            _("Your data is decrypted while the phone starts, which takes a few minutes.")
            if state == "decrypt-pending"
            else _("Your data is encrypted while the phone starts, which takes a few minutes.")
        )
        ask(
            self.window,
            _("Restart now?"),
            body,
            _("Restart"),
            lambda: run_async(["sudo", "-n", system.SHELL_HELPER, "reboot"]),
            cancel_label=_("Later"),
        )

    def disable_encryption(self):
        def go():
            self._pin_dialog(
                _("Enter the PIN"),
                [_("PIN")],
                lambda texts: self.home_helper(
                    ["disable"], texts[0], lambda _out: self.offer_restart()
                ),
            )

        ask(
            self.window,
            _("Turn off encryption?"),
            _(
                "Your data is decrypted at the next restart. It needs as much free space as "
                "your files take. Afterwards anyone who gets at the phone's storage can read "
                "your files."
            ),
            _("Continue"),
            go,
            destructive=True,
        )

    def cancel_encryption_change(self):
        # Dropping a waiting encryption puts the PIN's own check back: the
        # PIN itself is needed then, the recovery key only for the other way.
        decrypting = system.home_state()["state"] == "decrypt-pending"
        self._pin_dialog(
            _("Cancel the change?"),
            [_("PIN or recovery key") if decrypting else _("PIN")],
            lambda texts: self.home_helper(
                ["cancel"], texts[0], lambda _out: self.toast(_("Nothing changes at the restart"))
            ),
            recovery=decrypting,
        )

    def new_recovery_key(self):
        def go():
            self._pin_dialog(
                _("Enter the PIN"),
                [_("PIN or recovery key")],
                lambda texts: self.home_helper(
                    ["recovery-key"],
                    texts[0],
                    lambda out: self.show_recovery_key(self.recovery_in(out) or ""),
                ),
                recovery=True,
            )

        ask(
            self.window,
            _("Make a new recovery key?"),
            _("The old one stops working."),
            _("Continue"),
            go,
        )

    def set_rule(self, key, on):
        p = system.lock_policy()
        value = "on" if on else "off"
        if key == "lock_on_blank" and on and p["pin"] != "yes":
            self.shown()
            return

        def lock_on():
            if key == "lock_on_blank" and on:
                # duo-wc offers the lock protocol only with session_lock on
                # (the helper saves it and switches it on live).
                run_async(["sudo", "-n", system.HELPER, "wc-conf", "session_lock", "on"])

        if not on and p[key] == "on" and p["pin"] == "yes":
            # Less safe: the current PIN first.
            def done(texts):
                self.helper(["policy", key, value], texts[0] + "\n", None, lock_on)

            self._pin_dialog(_("Enter the PIN"), [_("Current PIN")], done, cancelled=self.shown)
            return
        self.helper(["policy", key, value], "", None, lock_on)

    def _pin_dialog(self, heading, fields, on_done, cancelled=None, recovery=False):
        """A dialog with PIN fields; on_done(list of texts). With the home
        encrypted the first field of a change (or `recovery`) takes the
        recovery key too."""
        encrypted = system.home_state()["state"] != "plain"
        box = Gtk.ListBox()
        box.add_css_class("boxed-list")
        entries = []
        for title in fields:
            e = Adw.PasswordEntryRow(title=title)
            e.set_input_purpose(Gtk.InputPurpose.PIN)
            box.append(e)
            entries.append(e)
        body = _("%d to %d digits") % (system.PIN_MIN, system.PIN_MAX)
        if recovery or (encrypted and len(fields) == 3):
            body += _(", or the recovery key's %d digits") % system.RECOVERY_DIGITS
        d = Adw.AlertDialog.new(heading, body)
        d.set_extra_child(box)
        d.add_response("cancel", _("Cancel"))
        d.add_response("ok", _("OK"))
        d.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
        d.set_close_response("cancel")
        d.set_response_enabled("ok", False)

        def recovery_key(text):
            digits = "".join(c for c in text if c not in " -")
            return len(digits) == system.RECOVERY_DIGITS and digits.isascii() and digits.isdigit()

        def changed(_e):
            d.set_response_enabled(
                "ok",
                all(
                    system.valid_pin(x.get_text())
                    or (i == 0 and len(fields) == 3 and x.get_text())
                    or (i == 0 and (recovery or encrypted) and recovery_key(x.get_text()))
                    for i, x in enumerate(entries)
                ),
            )

        for e in entries:
            e.connect("changed", changed)

        def response(_d, r):
            texts = [e.get_text() for e in entries]
            for e in entries:
                e.set_text("")
            if r == "ok":
                on_done(texts)
            elif cancelled:
                cancelled()

        d.connect("response", response)
        d.present(self.window)

    def change_pin(self):
        has_pin = system.lock_policy()["pin"] == "yes"
        current = (
            _("Current PIN or recovery key")
            if system.home_state()["state"] != "plain"
            else _("Current PIN")
        )
        fields = ([current] if has_pin else []) + [_("New PIN"), _("New PIN again")]

        def done(texts):
            new, again = texts[-2], texts[-1]
            if new != again:
                self.toast(_("The two PINs differ; nothing changed"))
                return
            current = texts[0] if has_pin else ""
            self.helper(["set"], "%s\n%s\n" % (current, new), _("PIN saved"))

        self._pin_dialog(_("Change PIN") if has_pin else _("Set a PIN"), fields, done)

    def remove_pin(self):
        if system.home_state()["state"] != "plain":
            inform(
                self.window,
                _("The PIN protects your data"),
                _(
                    "Your data is encrypted with this PIN. To remove the PIN, turn off "
                    "Encrypt my data first: that decrypts your data at the next restart."
                ),
            )
            return

        def done(texts):
            # No lock on blank without a PIN (the helper switches it off).
            self.helper(["clear"], texts[0] + "\n", _("PIN removed"))

        self._pin_dialog(_("Remove the PIN?"), [_("Current PIN")], done)


# -- SIM card ---------------------------------------------------------------------------


class SimPage(Page):
    """The SIM's PIN lock: on / off and the PIN changed, with the attempts
    left (sim.py; the PIN goes to ModemManager only). A SIM waiting for its
    PIN or PUK is unlocked in the shell's PIN dialog, not here."""

    icon = "auth-sim-locked-symbolic"
    title = _("SIM card")

    def build(self):
        self.simlock = None
        g = self.group()
        self.status = Adw.ActionRow(use_markup=False, title=_("SIM card"))
        g.add(self.status)
        g = self.group(
            _("SIM PIN"),
            _(
                "With the lock on, the SIM asks for its PIN every time the phone "
                "starts. Three wrong PINs block it; then only its PUK, from the "
                "operator, unblocks it."
            ),
        )
        self.lock = switch_row(_("Lock the SIM with its PIN"), on_toggle=self.set_lock)
        g.add(self.lock)
        self.change_row = Adw.ActionRow(use_markup=False, title=_("Change the SIM PIN"))
        self.change_btn = Gtk.Button(label=_("Change"), valign=Gtk.Align.CENTER)
        self.change_btn.connect("clicked", lambda _b: self.change_pin())
        self.change_row.add_suffix(self.change_btn)
        g.add(self.change_row)
        self.view = sim.lock_view(None)
        # The mailbox (voicemail.py): ModemManager does not know its number,
        # so it is set here; the app grid's Voicemail calls it.
        from . import voicemail

        g = self.group(
            _("Voicemail"), _("The app grid's Voicemail calls this number through Calls.")
        )
        self.vm_entry = Adw.EntryRow(title=_("Voicemail number"))
        self.vm_entry.set_input_purpose(Gtk.InputPurpose.PHONE)
        self.vm_entry.set_show_apply_button(True)
        self.vm_entry.set_text(voicemail.load())
        self.vm_entry.connect("apply", lambda r: self.save_voicemail(r.get_text()))
        g.add(self.vm_entry)
        self.vm_hint = Adw.ActionRow(use_markup=False)
        self.vm_hint.set_title(_("Your operator's mailbox"))
        self.vm_hint_box = Gtk.Box(spacing=6, valign=Gtk.Align.CENTER)
        self.vm_hint.add_suffix(self.vm_hint_box)
        self.vm_hint.set_visible(False)
        g.add(self.vm_hint)
        self.vm_shown = None

    def shown(self):
        if self.simlock is None:
            try:
                self.simlock = sim.SimLock(self.update)
            except (ImportError, ValueError):
                self.simlock = None
        self.update()

    def close(self):
        if self.simlock is not None:
            self.simlock.close()

    def left_text(self, left):
        if left is None:
            return ""
        if left == 1:
            return _("1 attempt left: a wrong PIN blocks the SIM")
        return _("%d attempts left") % left

    def update(self):
        info = self.simlock.info() if self.simlock is not None else None
        v = self.view = sim.lock_view(info)
        names = {
            "No modem": _("No modem"),
            "No SIM": _("No SIM card"),
            "Ready": _("Ready"),
            "Waiting for its PIN": _("Waiting for its PIN (unlock it on the screen)"),
            "Waiting for its PUK": _("Blocked: it needs its PUK (on the screen)"),
            "Not ready": _("Not ready"),
        }
        self.status.set_subtitle(names.get(v["status"], v["status"]))
        set_quietly(self.lock, v["lock_on"])
        self.lock.set_sensitive(v["can_toggle"])
        self.lock.set_subtitle(self.left_text(v["left"]) if v["status"] == "Ready" else "")
        self.change_row.set_sensitive(v["can_change"])
        self.change_row.set_subtitle(
            "" if v["lock_on"] or not v["can_toggle"] else _("Switch the lock on first")
        )
        self.update_voicemail(info or {})

    def update_voicemail(self, info):
        from . import voicemail

        sugg = voicemail.suggestions(info.get("network_code", ""))
        if sugg == self.vm_shown:
            return
        self.vm_shown = sugg
        child = self.vm_hint_box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.vm_hint_box.remove(child)
            child = nxt
        for number, what in sugg:
            b = Gtk.Button(label=number)
            b.set_tooltip_text(what)
            b.connect("clicked", lambda _b, n=number: self.save_voicemail(n, fill=True))
            self.vm_hint_box.append(b)
        self.vm_hint.set_subtitle("; ".join("%s: %s" % (what, number) for number, what in sugg))
        self.vm_hint.set_visible(bool(sugg))

    def save_voicemail(self, text, fill=False):
        from . import voicemail

        number = voicemail.clean(text)
        if text.strip() and not number:
            self.toast(_("That is not a phone number"))
            return
        try:
            voicemail.save(number)
        except OSError:
            self.toast(_("Could not save the setting"))
            return
        if fill:
            self.vm_entry.set_text(number)
        self.toast(_("Voicemail number saved") if number else _("Voicemail number removed"))

    def _dialog(self, heading, fields, on_done):
        """A dialog with SIM PIN fields (4 to 8 digits); on_done(texts) on
        OK, the switch put back on Cancel. The fields are emptied either way."""
        box = Gtk.ListBox()
        box.add_css_class("boxed-list")
        entries = []
        for title in fields:
            e = Adw.PasswordEntryRow(title=title)
            e.set_input_purpose(Gtk.InputPurpose.PIN)
            box.append(e)
            entries.append(e)
        body = _("%d to %d digits") % (sim.PIN_MIN, sim.PIN_MAX)
        left = self.left_text(self.view.get("left"))
        if left:
            body += "\n" + left
        d = Adw.AlertDialog.new(heading, body)
        d.set_extra_child(box)
        d.add_response("cancel", _("Cancel"))
        d.add_response("ok", _("OK"))
        d.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
        d.set_close_response("cancel")
        d.set_response_enabled("ok", False)

        def changed(_e):
            d.set_response_enabled("ok", all(sim.valid_sim_pin(x.get_text()) for x in entries))

        for e in entries:
            e.connect("changed", changed)

        def response(_d, r):
            texts = [e.get_text() for e in entries]
            for e in entries:
                e.set_text("")
            if r == "ok":
                on_done(texts)
            else:
                self.update()

        d.connect("response", response)
        d.present(self.window)

    def _answer(self, ok, error, saved):
        if ok:
            self.toast(saved)
        elif sim.wrong_pin(error):
            self.toast(_("Wrong PIN; nothing changed"))
        else:
            self.toast(_("The SIM refused it: %s") % (error or "?"))
        self.update()

    def set_lock(self, on):
        if self.simlock is None or not self.view["can_toggle"]:
            self.update()
            return

        def done(texts):
            self.simlock.set_lock(
                texts[0],
                on,
                lambda ok, err: self._answer(
                    ok, err, _("SIM lock on") if on else _("SIM lock off")
                ),
            )

        self._dialog(_("Lock the SIM") if on else _("Unlock the SIM"), [_("Current SIM PIN")], done)

    def change_pin(self):
        if self.simlock is None or not self.view["can_change"]:
            return

        def done(texts):
            old, new, again = texts
            if new != again:
                self.toast(_("The two PINs differ; nothing changed"))
                return
            self.simlock.change_pin(
                old, new, lambda ok, err: self._answer(ok, err, _("SIM PIN changed"))
            )

        self._dialog(
            _("Change the SIM PIN"),
            [_("Current SIM PIN"), _("New SIM PIN"), _("New SIM PIN again")],
            done,
        )


# -- Date and time ----------------------------------------------------------------------


class DateTimePage(Page):
    icon = "preferences-system-time-symbolic"
    title = _("Date & time")

    def build(self):
        g = self.group()
        self.now = Adw.ActionRow(use_markup=False, title="")
        self.now.add_css_class("property")
        g.add(self.now)
        self.ntp = switch_row(
            _("Automatic date & time"), _("From the network"), on_toggle=self.set_ntp
        )
        g.add(self.ntp)
        self.zone = Adw.ActionRow(use_markup=False, title=_("Time zone"))
        self.zone.add_suffix(Gtk.Image.new_from_icon_name("go-next-symbolic"))
        self.zone.set_activatable(True)
        self.zone.connect("activated", lambda _r: self.pick_zone())
        g.add(self.zone)

    def shown(self):
        self.every(15, self.refresh)

    def refresh(self):
        td = system.timedate()
        dt = GLib.DateTime.new_now_local()
        self.now.set_title(dt.format("%A, %d %B %Y"))
        self.now.set_subtitle(dt.format("%H:%M"))
        set_quietly(self.ntp, td.get("NTP") is True)
        self.ntp.set_sensitive(td.get("CanNTP", True) is not False)
        self.ntp.set_subtitle(
            _("Synchronised with the network")
            if td.get("NTPSynchronized")
            else _("From the network")
        )
        self.zone.set_subtitle(td.get("Timezone", ""))
        return True

    def set_ntp(self, on):
        run_async(
            ["sudo", "-n", system.HELPER, "ntp", "on" if on else "off"],
            lambda ok, _o: self.refresh() if ok else self.toast(_("Could not change it")),
        )

    def pick_zone(self):
        zones = system.timezones()
        dialog = Adw.Dialog(title=_("Time zone"))
        dialog.set_content_width(500)
        dialog.set_content_height(640)
        tv = Adw.ToolbarView()
        tv.add_top_bar(Adw.HeaderBar())
        search = Gtk.SearchEntry(placeholder_text=_("Search a city"))
        search.set_margin_start(12)
        search.set_margin_end(12)
        search.set_margin_bottom(6)
        tv.add_top_bar(search)
        lb = Gtk.ListBox()
        lb.add_css_class("navigation-sidebar")
        for z in zones:
            lb.append(
                Gtk.Label(
                    label=z.replace("_", " "),
                    xalign=0,
                    margin_top=12,
                    margin_bottom=12,
                    margin_start=6,
                )
            )
        lb.set_filter_func(
            lambda row: system.zone_matches(zones[row.get_index()], search.get_text())
        )
        search.connect("search-changed", lambda _s: lb.invalidate_filter())

        def chosen(_lb, row):
            zone = zones[row.get_index()]
            dialog.close()
            run_async(
                ["sudo", "-n", system.HELPER, "timezone", zone],
                lambda ok, _o: self.refresh() if ok else self.toast(_("Could not change it")),
            )

        lb.connect("row-activated", chosen)
        sw = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        sw.set_child(lb)
        tv.set_content(sw)
        dialog.set_child(tv)
        dialog.present(self.window)


# -- Language & region -------------------------------------------------------------------


class RegionPage(Page):
    icon = "preferences-desktop-locale-symbolic"
    title = _("Language & region")

    DAYS = ("auto", "mon", "sun", "sat")

    def build(self):
        self.generated = system.generated_locales()
        self.items = region.choices(self.generated)
        labels = [lab for _loc, lab in self.items]
        self.saved = region.state(
            system.read_text(region.LOCALE_CONF),
            system.read_text(region.REGION_CONF),
            self.generated,
        )
        lang, reg, day = self.saved
        self.lang, self.reg, self.day = lang, reg, day
        g = self.group(_("Language"))
        self.lang_row = combo_row(
            _("Interface language"),
            labels,
            self.index(lang),
            self.on_lang,
            subtitle=_("Apps that are translated use it"),
        )
        g.add(self.lang_row)
        g = self.group(_("Formats"))
        self.reg_row = combo_row(
            _("Region"),
            labels,
            self.index(reg),
            self.on_region,
            subtitle=_("Date, time, numbers, units and paper"),
        )
        g.add(self.reg_row)
        self.day_row = combo_row(
            _("First day of the week"), self.day_labels(), self.DAYS.index(day), self.on_day
        )
        g.add(self.day_row)
        self.prev_group = self.group(_("Preview"))
        self.prev_rows = {}
        for key, title in (
            ("date", _("Date")),
            ("time", _("Time")),
            ("number", _("Number")),
            ("weekday", _("Week starts on")),
            ("units", _("Units and paper")),
        ):
            row = Adw.ActionRow(use_markup=False, title=title)
            row.add_css_class("property")
            self.prev_group.add(row)
            self.prev_rows[key] = row
        g = self.group()
        self.apply_btn = Gtk.Button(label=_("Apply"), halign=Gtk.Align.CENTER, margin_top=6)
        self.apply_btn.add_css_class("suggested-action")
        self.apply_btn.add_css_class("pill")
        self.apply_btn.connect("clicked", lambda _b: self.confirm())
        g.add(self.apply_btn)
        self.note = Gtk.Label(wrap=True, xalign=0.5, margin_top=6)
        self.note.add_css_class("dim-label")
        g.add(self.note)
        self.update()

    def index(self, loc):
        for i, (item, _lab) in enumerate(self.items):
            if item == loc:
                return i
        return 0

    def day_labels(self):
        auto = region.weekday_name(self.reg, region.locale_value(self.reg, "first_weekday") or "2")
        return [_("Region default (%s)") % auto, _("Monday"), _("Sunday"), _("Saturday")]

    def on_lang(self, i):
        self.lang = self.items[i][0]
        self.update()

    def on_region(self, i):
        self.reg = self.items[i][0]
        self.day_row._quiet = True
        self.day_row.set_model(Gtk.StringList.new(self.day_labels()))
        self.day_row.set_selected(self.DAYS.index(self.day))
        self.day_row._quiet = False
        self.update()

    def on_day(self, i):
        self.day = self.DAYS[i]
        self.update()

    def update(self):
        for key, text in region.preview(self.reg, self.day):
            if key == "units":
                m, _s, p = text.partition(", ")
                text = (
                    (_("Metric") if m == "metric" else _("Imperial"))
                    + ", "
                    + (_("A4") if p == "A4" else _("Letter"))
                )
            self.prev_rows[key].set_subtitle(text)
        changed = (self.lang, self.reg, self.day) != self.saved
        self.apply_btn.set_sensitive(changed)
        self.note.set_label(_("Applying restarts the session: open apps close.") if changed else "")

    def confirm(self):
        ask(
            self.window,
            _("Restart the session?"),
            _(
                "To apply the language and formats the session restarts. The screen goes dark "
                "for a few seconds and every open app closes, this one too."
            ),
            _("Apply and restart"),
            self.apply,
            destructive=True,
        )

    def apply(self):
        def done(ok, _o):
            if ok:
                run_async(["sudo", "-n", system.HELPER, "restart-display"])
            else:
                self.toast(_("Could not save it"))

        run_async(["sudo", "-n", system.HELPER, "locale-set", self.lang, self.reg, self.day], done)


# -- Touch calibration --------------------------------------------------------------------

CALIB_TOOL = "/usr/local/lib/duo-settings/touch-calib.py"
PANELS = (("left", "DSI-1", _("Left panel")), ("right", "DSI-2", _("Right panel")))


def current_matrix(panel):
    """The libinput matrix in force for a panel's touchscreen (udev)."""
    name = {"left": "Surface Duo 2 Touchscreen Left", "right": "Surface Duo 2 Touchscreen Right"}[
        panel
    ]
    import glob

    for dev in glob.glob("/sys/class/input/event*"):
        try:
            with open(os.path.join(dev, "device", "name")) as f:
                if f.read().strip() != name:
                    continue
        except OSError:
            continue
        ok, out = system.run(
            ["udevadm", "info", "--query=property", "/dev/input/" + os.path.basename(dev)]
        )
        for line in out.splitlines():
            if line.startswith("LIBINPUT_CALIBRATION_MATRIX="):
                return touchcal.parse_matrix(line.split("=", 1)[1])
        return touchcal.IDENTITY
    return None


class TouchPage(Page):
    icon = "input-touchpad-symbolic"
    title = _("Touch calibration")

    def build(self):
        g = self.group(
            None,
            _(
                "If taps land beside what you touch, calibrate that panel: tap the "
                "centre of five crosshairs, twice each."
            ),
        )
        for panel, output, label in PANELS:
            row = Adw.ActionRow(use_markup=False, title=label)
            b = Gtk.Button(label=_("Calibrate"), valign=Gtk.Align.CENTER)
            b.connect("clicked", lambda _b, p=panel, o=output: self.calibrate(p, o))
            row.add_suffix(b)
            g.add(row)
        g = self.group()
        row = Adw.ActionRow(use_markup=False, title=_("Back to the shipped calibration"))
        b = Gtk.Button(label=_("Reset"), valign=Gtk.Align.CENTER)
        b.connect("clicked", lambda _b: self.reset())
        row.add_suffix(b)
        g.add(row)

    def calibrate(self, panel, output):
        if not os.path.exists(CALIB_TOOL):
            self.toast(_("The calibration tool is missing"))
            return
        log = os.path.join(GLib.get_user_runtime_dir() or "/tmp", "duo-touch-calib-%s.log" % panel)
        try:
            os.unlink(log)
        except OSError:
            pass
        before = current_matrix(panel)
        system.wc_request("expect_window", output=output)
        run_async(
            ["python3", CALIB_TOOL, output, log], lambda ok, _o: self.measured(panel, log, before)
        )

    def measured(self, panel, log, before):
        try:
            with open(log) as f:
                rows = touchcal.parse_log(f.read())
        except OSError:
            rows = []
        if len(rows) < 10 or before is None:
            self.toast(_("Calibration cancelled"))
            return
        try:
            new, report = touchcal.fit(rows, before)
        except ValueError:
            self.toast(_("The taps were too scattered; try again"))
            return
        lines = []
        for axis in ("x", "y"):
            b, a, changed = report[axis]
            lines.append(
                (
                    _("Across: %.1f px off, %.1f after")
                    if axis == "x"
                    else _("Up and down: %.1f px off, %.1f after")
                )
                % (b, a)
                if changed
                else (
                    _("Across: %.1f px off, kept")
                    if axis == "x"
                    else _("Up and down: %.1f px off, kept")
                )
                % b
            )
        if not (report["x"][2] or report["y"][2]):
            inform(self.window, _("Touch is accurate"), "\n".join(lines))
            return
        ask(
            self.window,
            _("Apply the calibration?"),
            "\n".join(lines) + "\n\n" + _("The display restarts to apply it (open apps close)."),
            _("Apply"),
            lambda: self.apply(panel, new),
        )

    def apply(self, panel, matrix):
        args = touchcal.format_matrix(matrix).split()

        def done(ok, _o):
            if ok:
                run_async(["sudo", "-n", system.HELPER, "restart-display"])
            else:
                self.toast(_("Could not apply it"))

        run_async(["sudo", "-n", system.HELPER, "touch-matrix", panel] + args, done)

    def reset(self):
        def go():
            for panel, _o, _l in PANELS:
                system.helper("touch-matrix", panel, "reset")
            run_async(["sudo", "-n", system.HELPER, "restart-display"])

        ask(
            self.window,
            _("Reset the touch calibration?"),
            _(
                "Both panels go back to the calibration the system ships. "
                "The display restarts (open apps close)."
            ),
            _("Reset"),
            go,
            destructive=True,
        )


# -- Wallpaper ------------------------------------------------------------------------------

TILE_W, TILE_H = 110, 150


class WallpaperPage(Page):
    icon = "preferences-desktop-wallpaper-symbolic"
    title = _("Wallpaper")

    def build(self):
        import threading

        self._threading = threading
        self.tiles = {}  # key -> button ("default", a path, "file")
        self.items = [p for p, _t in wallpaper.builtin()]
        g = self.group(_("Picture"), _("Shown on the home screen and the lock screen."))
        self.flow = Gtk.FlowBox(
            selection_mode=Gtk.SelectionMode.NONE,
            homogeneous=True,
            min_children_per_line=2,
            max_children_per_line=4,
            row_spacing=10,
            column_spacing=10,
            margin_top=6,
            margin_bottom=6,
        )
        g.add(self.flow)
        self.add_tile("default", _("Default"), None)
        self.own_tile = None
        for path, title in wallpaper.builtin():
            self.add_tile(path, title, path)
        g = self.group()
        row = Adw.ActionRow(
            use_markup=False,
            title=_("Choose image\u2026"),
            subtitle=_("A picture from the phone"),
            activatable=True,
        )
        row.add_suffix(Gtk.Image.new_from_icon_name("document-open-symbolic"))
        row.connect("activated", lambda _r: self.choose())
        g.add(row)
        self.mode = combo_row(
            _("Two panels"),
            [_("Same on each panel"), _("Across both panels")],
            on_select=self.set_mode,
            subtitle=_("Across both continues over the hinge gap"),
        )
        g.add(self.mode)

        g = self.group(
            _("Bing image of the day"),
            _(
                "When this is on, the phone contacts bing.com once a day to download the "
                "day\u2019s picture (nothing about you is sent). The picture replaces the "
                "choice above; the last one stays when there is no network."
            ),
        )
        self.bing = switch_row(_("Use the Bing image of the day"), on_toggle=self.set_bing)
        g.add(self.bing)
        self.market = combo_row(
            _("Region"), [n for _c, n in wallpaper.MARKETS], on_select=self.set_market
        )
        g.add(self.market)
        self.caption = Adw.ActionRow(use_markup=False, title="")
        self.caption.add_css_class("property")
        g.add(self.caption)

    # -- tiles -------------------------------------------------------------------------

    def add_tile(self, key, title, path):
        pic = Gtk.Picture(content_fit=Gtk.ContentFit.COVER, can_shrink=True)
        pic.set_size_request(TILE_W, TILE_H)
        if path is None:
            pic.add_css_class("wp-default")
        else:
            self.load_thumb(pic, path)
        label = Gtk.Label(label=title, ellipsize=3)
        label.add_css_class("caption")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        frame = Gtk.Frame(child=pic)
        frame.add_css_class("wp-frame")
        frame.set_overflow(Gtk.Overflow.HIDDEN)
        box.append(frame)
        box.append(label)
        button = Gtk.Button(child=box)
        button.add_css_class("flat")
        button.add_css_class("wp-tile")
        button.set_halign(Gtk.Align.CENTER)
        button.connect("clicked", lambda _b: self.pick(key))
        self.flow.append(button)
        self.tiles[key] = button
        return button

    def load_thumb(self, pic, path):
        @guarded
        def work():
            pb = wallpaper.thumbnail(path, TILE_W * 2, TILE_H * 2)
            if pb is not None:
                from gi.repository import Gdk

                GLib.idle_add(lambda: pic.set_paintable(Gdk.Texture.new_for_pixbuf(pb)) or False)

        self._threading.Thread(target=work, daemon=True).start()

    def pick(self, key):
        if key == "default":
            self.write({"source": "default"})
        elif key == "file":
            return
        else:
            self.write({"source": "file", "file": key})

    def choose(self):
        from gi.repository import Gtk as _Gtk

        flt = _Gtk.FileFilter()
        flt.set_name(_("Pictures"))
        flt.add_mime_type("image/jpeg")
        flt.add_mime_type("image/png")
        flt.add_mime_type("image/webp")
        flt.add_mime_type("image/jxl")
        flt.add_mime_type("image/svg+xml")
        filters = Gio.ListStore.new(_Gtk.FileFilter)
        filters.append(flt)
        dialog = _Gtk.FileDialog(title=_("Choose a picture"), filters=filters, default_filter=flt)

        def done(d, res):
            try:
                f = d.open_finish(res)
            except GLib.Error:
                return
            path = f.get_path()
            if path:
                self.write({"source": "file", "file": path})

        dialog.open(self.window, None, done)

    # -- the file ----------------------------------------------------------------------

    def write(self, changes):
        try:
            wallpaper.write(changes)
        except OSError:
            self.toast(_("Could not save the setting"))
        self.refresh()

    def set_mode(self, index):
        self.write({"mode": wallpaper.MODES[index]})

    def set_bing(self, on):
        c = wallpaper.read()
        changes = {"bing": "on" if on else "off"}
        if on:
            changes["bing_market"] = wallpaper.current_market(c, os.environ.get("LANG", ""))
        self.write(changes)
        if on:
            self.fetch_now()

    def set_market(self, index):
        self.write({"bing_market": wallpaper.MARKETS[index][0]})
        if conf.is_on(wallpaper.read().get("bing")):
            self.fetch_now(force=True)

    def fetch_now(self, force=False):
        # The service itself checks the switch; --force skips its freshness check.
        # (The session's XDG_RUNTIME_DIR is duo-wc's, not the user manager's.)
        run_async(
            [
                "env",
                "XDG_RUNTIME_DIR=/run/user/%d" % os.getuid(),
                "systemctl",
                "--user",
                "start",
                "--no-block",
                "duo-wallpaper-bing.service",
            ]
            if not force
            else ["/usr/local/lib/duo-shell/duo-wallpaper-bing", "--force"]
        )

    def shown(self):
        self.every(5, self.refresh)

    def refresh(self):
        c = wallpaper.read()
        sel = wallpaper.selection(c, self.items)
        bing_on = conf.is_on(c.get("bing"))
        own = c.get("file") if sel == "file" else None
        if own and self.own_tile is None:
            self.own_tile = self.add_tile("file", _("Your picture"), own)
        for key, button in self.tiles.items():
            if key == sel and not bing_on:
                button.add_css_class("selected")
            else:
                button.remove_css_class("selected")
        self.flow.set_sensitive(not bing_on)
        set_quietly(self.bing, bing_on)
        select_quietly(self.mode, wallpaper.mode_index(c))
        codes = [m for m, _n in wallpaper.MARKETS]
        select_quietly(
            self.market, codes.index(wallpaper.current_market(c, os.environ.get("LANG", "")))
        )
        self.market.set_visible(bing_on)
        title, copyright_ = wallpaper.bing_caption()
        self.caption.set_visible(bing_on)
        if bing_on:
            self.caption.set_title(title or _("Waiting for today\u2019s picture\u2026"))
            self.caption.set_subtitle(copyright_)


class HomePage(Page):
    """The home screen and the app drawer (duo-shell's launcher.conf)."""

    icon = "view-app-grid-symbolic"
    title = _("Home & apps")

    def build(self):
        g = self.group(
            _("App drawer"), _("Swipe up twice from the bottom edge, or the apps button.")
        )
        self.view = combo_row(
            _("Show apps as"),
            [_("Grid"), _("List (A to Z)")],
            on_select=self.set_view,
            subtitle=_("The list has a letter index at its right edge"),
        )
        g.add(self.view)

    def shown(self):
        select_quietly(self.view, system.DRAWER_VIEWS.index(system.drawer_view()))

    def set_view(self, i):
        try:
            system.set_drawer_view(system.DRAWER_VIEWS[i])
        except OSError:
            self.toast(_("Could not save the setting"))


from .aboutpage import AboutPage  # noqa: E402
from .androidpage import AndroidPage  # noqa: E402
from .appearancepage import AppearancePage  # noqa: E402
from .batterypage import BatteryPage  # noqa: E402
from .developerpage import DeveloperPage  # noqa: E402
from .displayspage import ExternalDisplayPage  # noqa: E402
from .esimpage import EsimPage  # noqa: E402
from .hotspotpage import HotspotPage  # noqa: E402
from .keyboardpage import KeyboardPage  # noqa: E402
from .laptoppage import LaptopPage  # noqa: E402
from .penpage import PenPage  # noqa: E402
from .privacypage import PrivacyPage  # noqa: E402
from .updatespage import UpdatesPage  # noqa: E402

PAGES = [
    WifiPage,
    BluetoothPage,
    SimPage,
    EsimPage,
    HotspotPage,
    DisplayPage,
    AppearancePage,
    BatteryPage,
    SoundPage,
    NotificationsPage,
    RotationPage,
    LaptopPage,
    ExternalDisplayPage,
    LockPage,
    PrivacyPage,
    AndroidPage,
    WallpaperPage,
    HomePage,
    DateTimePage,
    RegionPage,
    KeyboardPage,
    PenPage,
    TouchPage,
    UpdatesPage,
    DeveloperPage,
    AboutPage,
]
