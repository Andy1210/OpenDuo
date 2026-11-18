"""The eSIM page: the built-in eUICC's profiles (duo2-esim over lpac, as
root through sudo). Every change is asked of the user in the shell first
(the helper asks duo-wc's `confirm`): this page only says what to do. The
EID and the ICCIDs are shown by their last digits (the EID in full on a
tap). A new profile's activation code is read from its QR code (a camera,
or a picture of it: qrscan.py), or typed; its download shows its steps
(the helper's progress lines). A profile switched on while the phone uses
the SIM card in the tray offers to switch to the eSIM (duo-settings-helper
sim use 2). The pure parts are in esim.py (tests/test_esim.py)."""

import json

from gi.repository import Adw, Gio, GLib, Gtk

from . import qrscan, sim, system
from .esim import (
    failure_text,
    looks_like_code,
    mask,
    needs_confirmation,
    normalize,
    parse,
    progress_stage,
    server,
)
from .main import Page, _, ask, inform, run_async

HELPER = "/usr/local/sbin/duo2-esim"


def run_lines(argv, on_line, done):
    """Run argv without blocking the window: on_line(line) for each line of
    its output as it comes, done(last line) at the end."""
    try:
        proc = Gio.Subprocess.new(
            argv, Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_SILENCE
        )
    except GLib.Error:
        done("")
        return
    stream = Gio.DataInputStream.new(proc.get_stdout_pipe())
    last = [""]

    def finished(p, res):
        try:
            p.wait_finish(res)
        except GLib.Error:
            pass
        done(last[0])

    def got(st, res):
        try:
            line, _length = st.read_line_finish_utf8(res)
        except GLib.Error:
            line = None
        if line is None:
            proc.wait_async(None, finished)
            return
        if line.strip():
            last[0] = line
            on_line(line)
        st.read_line_async(GLib.PRIORITY_DEFAULT, None, got)

    stream.read_line_async(GLib.PRIORITY_DEFAULT, None, got)


class ProgressDialog(Adw.Dialog):
    """A download's progress: what is happening and how far. Closing it
    only hides it: the download goes on, its result is shown anyway."""

    def __init__(self, title):
        super().__init__(title=title)
        self.set_content_width(360)
        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        for side in ("start", "end", "top", "bottom"):
            getattr(box, "set_margin_" + side)(18)
        self.label = Gtk.Label(label=_("Confirm on the screen…"), wrap=True)
        self.bar = Gtk.ProgressBar(fraction=0.03)
        box.append(self.label)
        box.append(self.bar)
        view.set_content(box)
        self.set_child(view)

    def show_line(self, line):
        stage = progress_stage(line)
        if stage is not None:
            self.bar.set_fraction(stage[0])
            self.label.set_label(stage[1])


def profile_title(p):
    return p.get("nickname") or p.get("name") or p.get("provider") or _("Profile")


def profile_subtitle(p):
    state = _("In use") if p.get("state") == "enabled" else _("Off")
    parts = [p.get("provider") or "", state, mask(p.get("iccid"))]
    if p.get("class") == "test":
        # The chip's factory profiles for lab network simulators: they do
        # not work on a real network.
        parts.insert(0, _("Factory test profile"))
    return " · ".join(x for x in parts if x)


class EsimPage(Page):
    icon = "auth-sim-symbolic"
    title = _("eSIM")

    def build(self):
        self.busy = False
        self.eid = ""
        g = self.group(
            None,
            _(
                "The phone's built-in SIM. Every change is confirmed on the screen first. "
                "An operator gives you a QR code or an activation code for a new profile."
            ),
        )
        self.eid_row = Adw.ActionRow(use_markup=False, title=_("EID"), subtitle=_("Reading…"))
        self.eid_row.set_activatable(True)
        self.eid_row.connect("activated", lambda _r: self.toggle_eid())
        g.add(self.eid_row)
        self.list_group = self.group(_("Profiles"))
        self.rows = []
        g = self.group(
            _("Add a profile"),
            _(
                "The profile is downloaded from the operator over the internet (WiFi or mobile data)."
            ),
        )
        if qrscan.can_scan():
            g.add(
                self.action_row(
                    "camera-photo-symbolic",
                    _("Scan the QR code"),
                    _("With the camera"),
                    self.scan,
                )
            )
        if qrscan.can_read_pictures():
            g.add(
                self.action_row(
                    "image-x-generic-symbolic",
                    _("QR code from a picture"),
                    _("A screenshot or a photo of it"),
                    self.pick_picture,
                )
            )
        g.add(
            self.action_row(
                "input-keyboard-symbolic",
                _("Type the code"),
                _("Or paste it: the text under the QR code"),
                lambda: self.add_dialog(""),
            )
        )
        self.shown_eid = False

    def action_row(self, icon, title, subtitle, fn):
        row = Adw.ActionRow(use_markup=False, title=title, subtitle=subtitle, activatable=True)
        row.add_prefix(Gtk.Image.new_from_icon_name(icon))
        row.add_suffix(Gtk.Image.new_from_icon_name("go-next-symbolic"))
        row.connect("activated", lambda _r: fn())
        return row

    def shown(self):
        self.refresh()

    def helper(self, args, done):
        run_async(["sudo", "-n", HELPER] + list(args), lambda ok, out: done(parse(out)))

    def refresh(self):
        # One after the other: the chip answers one lpac at a time (the
        # helper waits for its turn too).
        self.helper(
            ["info"], lambda res: (self.got_info(res), self.helper(["list"], self.got_list))
        )

    def got_info(self, res):
        if res.get("ok"):
            self.eid = res.get("eid") or ""
            self.show_eid()
        else:
            self.eid_row.set_subtitle(_("The eSIM does not answer: %s") % (res.get("error") or "?"))

    def show_eid(self):
        self.eid_row.set_subtitle(
            (self.eid if self.shown_eid else mask(self.eid))
            + ("" if self.shown_eid else "   " + _("(tap to show)"))
        )

    def toggle_eid(self):
        self.shown_eid = not self.shown_eid
        self.show_eid()

    def got_list(self, res):
        for row in self.rows:
            self.list_group.remove(row)
        self.rows = []
        profiles = res.get("profiles") or []
        if not res.get("ok"):
            row = Adw.ActionRow(
                use_markup=False,
                title=_("Could not read the profiles"),
                subtitle=res.get("error") or "",
            )
            self.list_group.add(row)
            self.rows.append(row)
            return
        if not profiles:
            row = Adw.ActionRow(use_markup=False, title=_("No profiles yet"))
            self.list_group.add(row)
            self.rows.append(row)
            return
        for p in profiles:
            row = Adw.ActionRow(
                use_markup=False, title=profile_title(p), subtitle=profile_subtitle(p)
            )
            menu = Gtk.MenuButton(icon_name="view-more-symbolic", valign=Gtk.Align.CENTER)
            menu.add_css_class("flat")
            menu.set_tooltip_text(_("Change"))
            pop = Gtk.Popover()
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            enabled = p.get("state") == "enabled"
            items = [
                (
                    _("Switch off") if enabled else _("Use this profile"),
                    lambda _b, p=p, e=enabled, pop=pop: (
                        self.run(["disable", p["iccid"]], pop)
                        if e
                        else self.enable(p["iccid"], pop)
                    ),
                ),
                (_("Rename"), lambda _b, p=p, pop=pop: self.rename_dialog(p, pop)),
                (_("Delete"), lambda _b, p=p, pop=pop: self.delete(p, pop)),
            ]
            for label, cb in items:
                b = Gtk.Button(label=label)
                b.add_css_class("flat")
                if label == _("Delete"):
                    b.add_css_class("destructive-action")
                    b.set_sensitive(not enabled)
                b.connect("clicked", cb)
                box.append(b)
            pop.set_child(box)
            menu.set_popover(pop)
            row.add_suffix(menu)
            if enabled:
                row.add_prefix(Gtk.Image.new_from_icon_name("object-select-symbolic"))
            self.list_group.add(row)
            self.rows.append(row)

    def run(self, args, popover=None, on_ok=None, steps=None):
        """A change through the helper; `steps`: a title for a progress
        dialog that follows the helper's progress lines (a download)."""
        if popover is not None:
            popover.popdown()
        if self.busy:
            return
        self.busy = True
        dialog = None
        if steps:
            dialog = ProgressDialog(steps)
            dialog.present(self.window)
        else:
            self.toast(_("Confirm on the screen…"))

        def done(res):
            self.busy = False
            if dialog is not None:
                dialog.force_close()
            if res.get("ok"):
                if on_ok is not None:
                    on_ok(res)
                else:
                    self.toast(_("Done"))
            else:
                inform(self.window, _("eSIM"), failure_text(res))
            self.refresh()

        argv = ["sudo", "-n", HELPER] + list(args)
        if dialog is not None:
            run_lines(argv, dialog.show_line, lambda last: done(parse(last)))
        else:
            run_async(argv, lambda _ok, out: done(parse(out)))

    def enable(self, iccid, popover=None):
        self.run(["enable", iccid], popover, on_ok=lambda _res: self.after_enable())

    def after_enable(self):
        """A profile is on: if the phone uses the SIM card in the tray,
        offer the eSIM (the modem takes one SIM at a time)."""
        self.toast(_("Done"))

        def got(_ok, out):
            try:
                view = sim.slot_view(json.loads((out or "").strip().splitlines()[-1]))
            except (ValueError, IndexError):
                view = None
            esim_slot = (view or {}).get("2") or {}
            if not esim_slot.get("card") or esim_slot.get("in_use"):
                return
            ask(
                self.window,
                _("Use the eSIM now?"),
                _(
                    "The phone uses the SIM card in the tray for calls, messages and data. "
                    "Switch to the eSIM? There is no signal for about a minute while it "
                    "switches; Settings > SIM card switches back."
                ),
                _("Switch"),
                self.use_esim,
                cancel_label=_("Not now"),
            )

        run_async(["sudo", "-n", system.HELPER, "sim", "slots"], got)

    def use_esim(self):
        self.toast(_("Confirm on the screen, then about a minute…"))

        def done(_ok, out):
            try:
                res = json.loads((out or "").strip().splitlines()[-1])
            except (ValueError, IndexError):
                res = {"ok": False, "error": _("no answer")}
            if res.get("ok"):
                self.toast(_("The phone uses the eSIM now"))
            elif res.get("error") != "not confirmed":
                inform(self.window, _("eSIM"), _("It did not switch: %s") % res.get("error"))

        run_async(["sudo", "-n", system.HELPER, "sim", "use", "2"], done)

    def delete(self, p, popover):
        popover.popdown()
        # The shell asks too (the helper's question): this one says why.
        ask(
            self.window,
            _("Delete “%s”?") % profile_title(p),
            _(
                "The profile is removed from the phone for good. To use it again you need a new code "
                "from the operator."
            ),
            _("Delete"),
            lambda: self.run(["delete", p["iccid"]]),
            destructive=True,
        )

    def rename_dialog(self, p, popover):
        popover.popdown()
        entry = Gtk.Entry(text=p.get("nickname") or "", max_length=64)
        ask(
            self.window,
            _("Rename the profile"),
            None,
            _("Rename"),
            lambda: self.run(["rename", p["iccid"], entry.get_text().strip()]),
            extra=entry,
        )

    # -- a new profile ------------------------------------------------------------------

    def scan(self):
        dialog = qrscan.ScanDialog(
            self.got_code,
            accept=looks_like_code,
            wrong=_(
                "That QR code is not an eSIM code. The operator's code starts with LPA:1$ "
                "(it is often on the activation letter or in the operator's e-mail)."
            ),
        )
        dialog.present(self.window)

    def pick_picture(self):
        chooser = Gtk.FileDialog(title=_("A picture of the QR code"))
        images = Gtk.FileFilter(name=_("Pictures"))
        images.add_pixbuf_formats()
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(images)
        chooser.set_filters(filters)
        chooser.set_default_filter(images)

        def picked(d, res):
            try:
                f = d.open_finish(res)
            except Exception:  # noqa: BLE001  (dismissed: GLib.Error)
                return
            path = f.get_path() if f is not None else None
            if not path:
                return
            texts = qrscan.read_picture(path)
            code = next((t for t in texts if looks_like_code(t)), None)
            if code is not None:
                self.got_code(code)
            elif texts:
                inform(
                    self.window,
                    _("eSIM"),
                    _(
                        "The QR code in that picture is not an eSIM code (it does not start with LPA:1$)."
                    ),
                )
            else:
                inform(
                    self.window,
                    _("eSIM"),
                    _(
                        "No QR code was found in that picture. Try one where the code is bigger "
                        "and sharp, or scan it with the camera."
                    ),
                )

        chooser.open(self.window, None, picked)

    def got_code(self, code):
        self.add_dialog(normalize(code), read=True)

    def add_dialog(self, code, read=False):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        entry = Gtk.Entry(placeholder_text="LPA:1$…", text=code)
        conf = Gtk.Entry(placeholder_text=_("Confirmation code (if the operator gave one)"))
        box.append(entry)
        box.append(conf)
        if read:
            body = _("The QR code is read: a profile from %s. Download it now?") % (
                server(code) or "?"
            )
            if needs_confirmation(code):
                body += " " + _("This profile also needs the confirmation code the operator gave.")
        else:
            body = _(
                "Paste the activation code from the operator (the text under its QR code). The profile "
                "is downloaded over the internet."
            )
        ask(
            self.window,
            _("Add an eSIM profile"),
            body,
            _("Download"),
            lambda: self.add(entry.get_text(), conf.get_text().strip()),
            extra=box,
        )
        if read and needs_confirmation(code):
            conf.grab_focus()

    def add(self, code, conf):
        code = normalize(code)
        if not looks_like_code(code):
            inform(
                self.window,
                _("eSIM"),
                _(
                    "That is not an activation code. It starts with LPA:1$ "
                    "and has the operator's server and a code after it."
                ),
            )
            return
        if needs_confirmation(code) and not conf:
            inform(
                self.window,
                _("eSIM"),
                _("This profile needs the confirmation code the operator gave with it."),
            )
            return
        self.run(
            ["add", code] + ([conf] if conf else []),
            on_ok=self.added,
            steps=_("Adding the eSIM profile"),
        )

    def added(self, res):
        p = res.get("profile") or {}
        name = profile_title(p)
        ask(
            self.window,
            _("“%s” is on the phone") % name,
            _(
                "The new profile is off for now. Switch to it now? The SIM in use is then "
                "switched off; you can switch back here at any time."
            ),
            _("Use it now"),
            lambda: self.enable(p["iccid"]) if p.get("iccid") else None,
            cancel_label=_("Later"),
        )
