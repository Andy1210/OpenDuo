"""Settings > Pen: the Surface Pen (Slim Pen 2 and the other MPP pens). The tip,
the eraser end and the side button work with no pairing; the top button
comes over Bluetooth. This page pairs the pen, sets what the top button, the
hand and the side button do, and opens a test canvas.

- Palm rejection is duo2-touchd's (touch/README.md): `palm_rejection` in
  /etc/duo2/pen.conf, through `duo-settings-helper pen palm both|panel|off`;
  the daemon reads the file again by itself.
- The side button, the hover dot and which apps get the pen as a pen are
  duo-wc's (compositor/duo-wc README "The pen"): `pen_button`, `pen_dot`,
  `pen_input` (drawing apps only / all apps), `pen_apps` (the drawing apps) in
  duo-wc.conf, through `duo-settings-helper wc-conf`, which also applies them
  live (`set_pen`); read back from duo-wc's state (`pen`), with what the pen is
  doing now.
- Charging the pen from the phone (the Surface Duo 2 Pen Cover) is
  power/duo2-pen-charger's: `duo-settings-helper pen charge on|off`; off by
  default, the row only where the kernel has the pen charger.
- The pen over Bluetooth: BlueZ, through the window's one bluetooth.Bluez (the
  Bluetooth page's too, and its agent's dialogs). "Pair" walks the owner
  through it (hold the top button until the light flashes) and pairs the
  first Surface pen heard advertising (pen.PairFlow), LE only while it looks.
- The pen's haptics are duo2-pend's (rootfs/overlay/usr/local/sbin/duo2-pend):
  `writing_feel`, `feel_strength`, `button_click` in /etc/duo2/pen.conf,
  through `duo-settings-helper pen feel|strength|click`; the daemon reads the
  file again by itself and sets the pen at every connection.
- The pen's firmware is touch/tools/pen-fw.py's: the versions duo2-pend read at
  the last connection (/var/lib/duo2-pen/firmware.json), the files on the
  phone (`duo-settings-helper pen fw-available`), the update as a system unit
  (`pen fw-update`, `pen fw-stop`) with its progress in
  /run/duo2-pen-fw/status.json: the page may be closed meanwhile.
- The top button is duo-shell's (duo-wc gives it the presses, never an app):
  `click`, `double_click`, `hold` in ~/.config/duo-shell/pen.conf, written
  here as the user, read by the shell at every press (duoshell/penmodel.py).
"""

import os
import time

from gi.repository import Adw, GLib, Gtk

from . import bluetooth, system
from .main import (
    Page,
    SliderRow,
    _,
    ask,
    combo_row,
    run_async,
    select_quietly,
    set_quietly,
    switch_row,
)
from .pen import (
    ACTION_TITLES,
    ACTIONS,
    BUTTON,
    DEFAULT_PEN_APPS,
    FEEL_TITLES,
    FEELS,
    FW_CACHE,
    FW_STATUS,
    PALM,
    PEN_INPUT,
    PEN_TEST,
    PRESS_TITLES,
    PRESSES,
    PairFlow,
    charger,
    charger_subtitle,
    fw_fraction,
    fw_running,
    fw_view,
    now_text,
    paired_pens,
    pen_apps_value,
    pen_status,
    read_buttons,
    read_haptics,
    read_json,
    read_palm,
    set_button,
)


class PenPage(Page):
    icon = "document-edit-symbolic"
    title = _("Pen")

    def build(self):
        self.bt = None
        self.flow = None
        self.flow_dialog = None
        self.flow_timer = None
        self.discovering = False
        g = self.group(
            None,
            _(
                "A Surface Pen works on both screens with no pairing: write with the tip, rub with the "
                "eraser end, and keep the side button for menus. Drawing apps get pressure and tilt; "
                "everywhere else the pen works like a finger: it scrolls, taps and long-presses. The top "
                "button needs the pen paired over Bluetooth."
            ),
        )
        self.now = Adw.ActionRow(use_markup=False, title=_("Now"))
        g.add(self.now)
        test = Adw.ActionRow(
            use_markup=False,
            title=_("Pen test"),
            subtitle=_("A canvas that shows what the screen gets from the pen"),
        )
        b = Gtk.Button(label=_("Open"), valign=Gtk.Align.CENTER)
        b.connect("clicked", lambda _b: self.open_test())
        test.add_suffix(b)
        g.add(test)

        g = self.group(_("Bluetooth"))
        self.paired_row = Adw.ActionRow(
            use_markup=False,
            title=_("No pen paired"),
            subtitle=_("Pair the pen to use its top button"),
        )
        self.paired_row.add_prefix(Gtk.Image.new_from_icon_name("input-tablet-symbolic"))
        g.add(self.paired_row)
        self.pair_row = Adw.ActionRow(
            use_markup=False,
            title=_("Pair your Surface Pen"),
            subtitle=_("Hold the top button until the light flashes"),
        )
        self.pair_button = Gtk.Button(label=_("Pair"), valign=Gtk.Align.CENTER)
        self.pair_button.add_css_class("suggested-action")
        self.pair_button.connect("clicked", lambda _b: self.pair_pen())
        self.pair_row.add_suffix(self.pair_button)
        g.add(self.pair_row)

        g = self.group(
            _("Top button"),
            _(
                "Nothing happens while the screen is off or locked. A screenshot is of the screen the "
                "pen was last over, in Pictures/Screenshots."
            ),
        )
        self.presses = {}
        for press in PRESSES:
            row = combo_row(
                PRESS_TITLES[press],
                [ACTION_TITLES[a] for a in ACTIONS],
                on_select=lambda i, p=press: self.set_press(p, ACTIONS[i]),
            )
            g.add(row)
            self.presses[press] = row

        g = self.group(
            _("Feel"),
            _(
                "The pen ticks softly as its tip moves across the screen, like the tool on paper. The "
                "phone sends the ticks over Bluetooth, so the first second after the pen comes to the "
                "screen has none. It uses some of the pen's battery. Needs the pen paired."
            ),
        )
        self.feel = combo_row(
            _("Writing feel"),
            [FEEL_TITLES[f] for f in FEELS],
            on_select=lambda i: self.set_haptic("feel", FEELS[i]),
        )
        g.add(self.feel)
        self.strength = SliderRow(
            _("Strength"),
            "network-wireless-signal-weak-symbolic",
            "network-wireless-signal-excellent-symbolic",
            lambda v: self.set_haptic("strength", str(max(1, v))),
            lower=1,
            upper=100,
        )
        g.add(self.strength)
        self.click = switch_row(
            _("Click when the top button is pressed"),
            _("A short tap in the pen, so you feel the press"),
            on_toggle=lambda on: self.set_haptic("click", "on" if on else "off"),
        )
        g.add(self.click)

        g = self.group(
            _("Firmware"),
            _(
                "Microsoft's updates for the Surface Slim Pen 2, sent to the pen over Bluetooth. Each "
                "part takes a few minutes and the pen restarts in between; if anything goes wrong it "
                "keeps working with its previous firmware."
            ),
        )
        self.fw_current = Adw.ActionRow(use_markup=False, title=_("In the pen"))
        g.add(self.fw_current)
        self.fw_available = Adw.ActionRow(use_markup=False, title=_("On this phone"))
        self.fw_button = Gtk.Button(label=_("Update"), valign=Gtk.Align.CENTER)
        self.fw_button.add_css_class("suggested-action")
        self.fw_button.connect("clicked", lambda _b: self.fw_clicked())
        self.fw_available.add_suffix(self.fw_button)
        g.add(self.fw_available)
        self.fw_status = Adw.ActionRow(use_markup=False, title=_("Update"))
        self.fw_bar = Gtk.ProgressBar(valign=Gtk.Align.CENTER, width_request=110)
        self.fw_status.add_suffix(self.fw_bar)
        self.fw_status.set_visible(False)
        g.add(self.fw_status)
        self.fw_files = None  # the helper's fw-available answer, once per showing

        g = self.group(
            _("Your hand"),
            _("While the pen is near the screen, touches of the hand that holds it are ignored."),
        )
        self.palm = combo_row(
            _("Palm rejection"),
            [_("Both screens"), _("The pen's screen"), _("Off")],
            on_select=lambda i: self.set_palm(PALM[i]),
        )
        g.add(self.palm)

        g = self.group(
            _("Apps"),
            _(
                "Drawing apps get the pen as a pen: pressure, tilt, the eraser and hovering. Every other "
                "app gets it as a finger, so lists and pages scroll with it. The window overview, "
                "carrying windows and the screen edges take it as a finger everywhere."
            ),
        )
        self.input = combo_row(
            _("Pen input"),
            [_("Drawing apps only"), _("All apps that take a pen")],
            on_select=lambda i: self.wc_conf("pen_input", PEN_INPUT[i]),
        )
        g.add(self.input)
        self.apps = Adw.EntryRow(title=_("Drawing apps"), show_apply_button=True)
        self.apps.set_tooltip_text(_("App ids or their last part, separated by commas"))
        self.apps.connect("apply", lambda _r: self.set_apps(self.apps.get_text()))
        self.apps.connect("changed", lambda _r: setattr(self, "apps_edited", not self.apps_quiet))
        self.apps_quiet = False
        self.apps_edited = False
        reset = Gtk.Button(
            icon_name="edit-undo-symbolic",
            valign=Gtk.Align.CENTER,
            tooltip_text=_("The default list"),
        )
        reset.add_css_class("flat")
        reset.connect("clicked", lambda _b: self.set_apps(", ".join(DEFAULT_PEN_APPS)))
        self.apps.add_suffix(reset)
        g.add(self.apps)

        g = self.group()
        self.button = combo_row(
            _("Side button"),
            [_("Right click"), _("Nothing")],
            subtitle=_(
                "In laptop mode, with a mouse or a display's touchpad. Drawing apps use "
                "the button themselves"
            ),
            on_select=lambda i: self.wc_conf("pen_button", BUTTON[i]),
        )
        g.add(self.button)
        self.dot = switch_row(
            _("Show where the pen points"),
            _("A small dot under the pen while it hovers"),
            on_toggle=lambda on: self.wc_conf("pen_dot", "on" if on else "off"),
        )
        g.add(self.dot)

        self.charge_group = self.group()
        self.charge = switch_row(
            _("Charge pen from phone"), charger_subtitle(False, None), on_toggle=self.set_charge
        )
        self.charge_group.add(self.charge)
        self.charge_group.set_visible(False)

    # -- the window's Bluez, the Bluetooth page's dialogs --------------------------
    def bluez(self):
        if self.bt is None:
            self.bt = bluetooth.shared(self.bt_changed, self.agent_ui())
            if self.bt.bus is not None:
                self.bt.reload()
        return self.bt

    def agent_ui(self):
        """The Bluetooth page answers the pairing agent's questions (a code,
        a confirmation): the same dialogs, wherever the pairing started."""
        for page in getattr(self.window, "pages", []):
            if page.__class__.__name__ == "BluetoothPage":
                return page
        return None

    def bt_changed(self):
        if self.get_mapped() or self.flow is not None:
            self.update_bt()
            if self.flow is not None:
                self.flow_step()

    def shown(self):
        select_quietly(self.palm, PALM.index(read_palm()))
        self.show_haptics()
        self.fw_files = None
        self.load_fw_files()
        config = read_buttons()
        for press, row in self.presses.items():
            select_quietly(row, ACTIONS.index(config[press]))
        self.every(1, self.refresh)

    def refresh(self):
        pen = system.wc_state().get("pen")
        self.now.set_subtitle(now_text(pen))
        if pen:
            select_quietly(
                self.button, BUTTON.index(pen.get("button")) if pen.get("button") in BUTTON else 0
            )
            set_quietly(self.dot, bool(pen.get("dot", True)))
            select_quietly(
                self.input,
                PEN_INPUT.index(pen.get("input")) if pen.get("input") in PEN_INPUT else 0,
            )
            self.apps.set_sensitive(pen.get("input", "apps") == "apps")
            apps = pen.get("apps")
            if isinstance(apps, list) and not self.apps_edited:
                text = ", ".join(str(a) for a in apps)
                if self.apps.get_text() != text:
                    self.apps_quiet = True
                    self.apps.set_text(text)
                    self.apps_quiet = False
        self.refresh_charge()
        self.update_bt()
        self.refresh_fw()

    def refresh_charge(self):
        c = charger()
        self.charge_group.set_visible(c is not None)
        if c is not None:
            set_quietly(self.charge, c[0])
            self.charge.set_subtitle(charger_subtitle(*c))

    def set_charge(self, on):
        def done(ok, _o):
            if not ok:
                self.toast(_("Could not save the setting"))
            self.refresh_charge()

        run_async(["sudo", "-n", system.HELPER, "pen", "charge", "on" if on else "off"], done)

    def update_bt(self):
        bt = self.bluez()
        if not bt.available():
            self.paired_row.set_title(_("No Bluetooth adapter"))
            self.paired_row.set_subtitle("")
            self.pair_button.set_sensitive(False)
            return
        self.pair_button.set_sensitive(self.flow is None)
        pens = paired_pens(bt.devices())
        if pens:
            self.paired_row.set_title(pens[0]["alias"])
            self.paired_row.set_subtitle(pen_status(pens[0]))
            self.pair_button.set_label(_("Pair again"))
            self.pair_button.remove_css_class("suggested-action")
        else:
            self.paired_row.set_title(_("No pen paired"))
            self.paired_row.set_subtitle(_("Pair the pen to use its top button"))
            self.pair_button.set_label(_("Pair"))
            self.pair_button.add_css_class("suggested-action")

    # -- the settings ----------------------------------------------------------------
    def set_press(self, press, action):
        try:
            set_button(press, action)
        except (OSError, ValueError):
            self.toast(_("Could not save the setting"))
        select_quietly(self.presses[press], ACTIONS.index(read_buttons()[press]))

    # -- the firmware ------------------------------------------------------------------
    def load_fw_files(self):
        def done(ok, out):
            import json

            try:
                self.fw_files = json.loads(out) if ok and out.strip() else {}
            except ValueError:
                self.fw_files = {}
            self.refresh_fw()

        run_async(["sudo", "-n", system.HELPER, "pen", "fw-available"], done)

    def refresh_fw(self):
        status = read_json(FW_STATUS)
        running = fw_running(status)
        v = fw_view(read_json(FW_CACHE), self.fw_files, status, running)
        self.fw_current.set_subtitle(v["current"])
        self.fw_available.set_subtitle(v["available"])
        self.fw_button.set_label(_("Stop") if running else _("Update"))
        self.fw_button.set_visible(running or v["can_update"])
        if running:
            self.fw_button.remove_css_class("suggested-action")
        else:
            self.fw_button.add_css_class("suggested-action")
        self.fw_status.set_visible(bool(v["status"]))
        self.fw_status.set_subtitle(v["status"])
        frac = fw_fraction(status)
        self.fw_bar.set_visible(frac is not None)
        if frac is not None:
            self.fw_bar.set_fraction(frac)
        was = getattr(self, "_fw_was_running", False)
        self._fw_was_running = running
        if was and not running:
            self.load_fw_files()  # finished: what is newer now

    def fw_clicked(self):
        if fw_running(read_json(FW_STATUS)):
            run_async(
                ["sudo", "-n", system.HELPER, "pen", "fw-stop"], lambda ok, _o: self.refresh_fw()
            )
            return

        def go():
            def done(ok, _o):
                if not ok:
                    self.toast(_("Could not start the update"))
                self.refresh_fw()

            run_async(["sudo", "-n", system.HELPER, "pen", "fw-update"], done)

        ask(
            self.window,
            _("Update the pen?"),
            _(
                "Put the phone on its charger, keep the pen near it, and disconnect other Bluetooth "
                "devices; the pen needs at least 50 % battery. Press the pen's top button now so that it "
                "connects. Each part takes a few minutes and the pen restarts in between: if it "
                "disconnects, press its top button again and the update goes on. You can leave this page."
            ),
            _("Update"),
            go,
        )

    def show_haptics(self):
        feel, strength, click = read_haptics()
        select_quietly(self.feel, FEELS.index(feel))
        self.strength.set_value(strength)
        set_quietly(self.click, click)

    def set_haptic(self, what, value):
        def done(ok, _o):
            if not ok:
                self.toast(_("Could not save the setting"))
                self.show_haptics()

        run_async(["sudo", "-n", system.HELPER, "pen", what, value], done)

    def set_palm(self, mode):
        def done(ok, _o):
            if not ok:
                self.toast(_("Could not save the setting"))
            select_quietly(self.palm, PALM.index(read_palm()))

        run_async(["sudo", "-n", system.HELPER, "pen", "palm", mode], done)

    def wc_conf(self, key, value):
        def done(ok, _o):
            if not ok:
                self.toast(_("Could not save the setting"))
            self.refresh()

        run_async(["sudo", "-n", system.HELPER, "wc-conf", key, value], done)

    def set_apps(self, text):
        value = pen_apps_value(text)
        if value is None:
            self.toast(_("Only app ids: letters, digits, dots, dashes, separated by commas"))
            return
        self.apps_edited = False
        self.wc_conf("pen_apps", value)

    def open_test(self):
        if not os.path.exists(PEN_TEST):
            self.toast(_("The pen test is not installed"))
            return
        output = system.wc_state().get("active_output")
        if output:
            system.wc_request("expect_window", output=output)
        run_async(["python3", PEN_TEST])

    # -- pairing: hold the top button, the first Surface pen heard is paired -------
    def pair_pen(self):
        bt = self.bluez()
        if not bt.available() or self.flow is not None:
            return
        ui = self.agent_ui()
        if ui is not None:
            if ui.bt is None:  # its page not opened yet: it follows the window's Bluez too
                ui.bt = bluetooth.shared(ui.changed_soon, ui)
            bt.ui = ui
        if not bt.powered() and ui is not None:
            ui.set_power(True)  # airplane mode's block freed first, as on its page
        self.flow = PairFlow(time.monotonic())
        self.discovering = False
        self.status = Gtk.Label(label=_("Looking for the pen..."), wrap=True)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.spinner = Adw.Spinner()
        self.spinner.set_size_request(32, 32)
        box.append(self.spinner)
        box.append(self.status)
        d = Adw.AlertDialog.new(
            _("Pair your Surface Pen"),
            _(
                "Hold the pen's top button for 5 to 7 seconds, until its light flashes white. "
                "Keep the pen near the phone."
            ),
        )
        d.set_extra_child(box)
        d.add_response("cancel", _("Cancel"))
        d.set_close_response("cancel")
        d.connect("response", lambda _d, _r: self.flow_end())
        self.flow_dialog = d
        d.present(self.window)
        self.flow_timer = GLib.timeout_add_seconds(1, self.flow_tick)
        self.update_bt()
        self.flow_step()

    def flow_tick(self):
        if self.flow is None:
            self.flow_timer = None
            return False
        self.flow_step()
        return True

    def flow_step(self):
        bt, flow = self.bt, self.flow
        if bt is None or flow is None or flow.state != "looking":
            return
        if not self.discovering:
            if not bt.powered():
                return
            # LE only while it looks: the pen is LE, and a dual scan halves its chances.
            self.discovering = True
            bt.set_discovery_filter("le", done=lambda _e: bt.start_discovery())
            return
        if not bt.discovering():
            bt.start_discovery()  # BlueZ stops it while it pairs, or after a while
        action = flow.step(bt.devices(), time.monotonic())
        if action is None:
            return
        kind, path = action
        name = bt.name_of(path) if path else ""
        if kind == "pair":
            self.status.set_label(_("Pairing with %s...") % name)
            bt.pair(path, self.flow_paired)
        elif kind == "forget":
            self.status.set_label(_("Pairing %s again...") % name)

            def forgot(err):
                flow.forgot(err)
                if err is not None:
                    self.flow_failed(err)

            bt.forget(path, forgot)
        elif kind == "connected":
            self.flow_done(_("%s is paired and connected.") % name)
        elif kind == "timeout":
            self.flow_failed(None)

    def flow_paired(self, err):
        if self.flow is None:
            return
        self.flow.paired(err)
        if err is None:
            self.flow_done(_("Paired. Press the top button to try it."))
        else:
            self.flow_failed(err)

    def flow_done(self, text):
        self.flow_stop_looking()
        self.spinner.set_visible(False)
        self.status.set_label(text)
        self.flow_dialog.set_response_label("cancel", _("Done"))
        self.update_bt()

    def flow_failed(self, err):
        self.flow_stop_looking()
        self.spinner.set_visible(False)
        self.status.set_label(
            _(
                "No pen was found. Hold the top button until the light flashes white, then try again."
            )
            if err is None
            else _("Pairing failed: %s") % err
        )
        self.flow_dialog.set_response_label("cancel", _("Close"))

    def flow_stop_looking(self):
        if self.bt is not None and self.discovering:
            self.bt.stop_discovery()
            self.bt.set_discovery_filter(None)
        self.discovering = False

    def flow_end(self):
        """The dialog closed (Cancel, Done): the walk ends; a pairing already
        under way finishes by itself."""
        self.flow_stop_looking()
        if self.flow_timer is not None:
            GLib.source_remove(self.flow_timer)
            self.flow_timer = None
        self.flow = None
        self.flow_dialog = None
        self.update_bt()

    def close(self):
        self.flow_stop_looking()
        if self.bt is not None:
            self.bt.close()
