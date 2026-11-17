"""The eSIM page: the built-in eUICC's profiles (duo2-esim over lpac, as
root through sudo). Every change is asked of the user in the shell first
(the helper asks duo-wc's `confirm`): this page only says what to do. The
EID and the ICCIDs are shown by their last digits (the EID in full on a
tap). The pure parts are in esim.py (tests/test_esim.py)."""

from gi.repository import Adw, Gtk

from .esim import looks_like_code, mask, parse
from .main import Page, _, ask, inform, run_async

HELPER = "/usr/local/sbin/duo2-esim"


def profile_title(p):
    return p.get("nickname") or p.get("name") or p.get("provider") or _("Profile")


def profile_subtitle(p):
    state = _("In use") if p.get("state") == "enabled" else _("Off")
    parts = [p.get("provider") or "", state, mask(p.get("iccid"))]
    return " · ".join(x for x in parts if x)


def error_text(err):
    if err == "not confirmed":
        return _("Not done: it was not confirmed.")
    return err or _("It did not work.")


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
        g = self.group()
        add = Adw.ButtonRow(title=_("Add a profile")) if hasattr(Adw, "ButtonRow") else None
        if add is None:
            add = Adw.ActionRow(use_markup=False, title=_("Add a profile"), activatable=True)
            add.connect("activated", lambda _r: self.add_dialog())
        else:
            add.connect("activated", lambda _r: self.add_dialog())
        g.add(add)
        self.shown_eid = False

    def shown(self):
        self.refresh()

    def helper(self, args, done):
        run_async(["sudo", "-n", HELPER] + list(args), lambda ok, out: done(parse(out)))

    def refresh(self):
        self.helper(["info"], self.got_info)
        self.helper(["list"], self.got_list)

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
                    lambda _b, p=p, e=enabled, pop=pop: self.run(
                        ["disable" if e else "enable", p["iccid"]], pop
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

    def run(self, args, popover=None):
        if popover is not None:
            popover.popdown()
        if self.busy:
            return
        self.busy = True
        self.toast(_("Confirm on the screen…"))

        def done(res):
            self.busy = False
            if res.get("ok"):
                self.toast(_("Done"))
            else:
                inform(self.window, _("eSIM"), error_text(res.get("error")))
            self.refresh()

        self.helper(args, done)

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

    def add_dialog(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        code = Gtk.Entry(placeholder_text="LPA:1$…")
        conf = Gtk.Entry(placeholder_text=_("Confirmation code (if the operator gave one)"))
        box.append(code)
        box.append(conf)
        ask(
            self.window,
            _("Add an eSIM profile"),
            _(
                "Paste the activation code from the operator (the text under its QR code). The profile "
                "is downloaded over the internet."
            ),
            _("Add"),
            lambda: self.add(code.get_text().strip(), conf.get_text().strip()),
            extra=box,
        )

    def add(self, code, conf):
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
        self.run(["add", code] + ([conf] if conf else []))
