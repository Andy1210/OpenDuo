"""Settings > Updates: OpenDuo's own packages and Debian's, from their
repositories (docs/UPDATES.md).

Everything goes through `sudo -n duo2-updates` (maint/duo2-updates): the
check and the install run as systemd units there, so closing Settings stops
nothing; Install is asked of the user in the shell first (the helper asks
duo-wc's confirm) and takes a snapshot before any package changes, only on
the charger or at 50 % battery. The page reads the helper's status while it
is shown. Pure parts: updates.py (tests/test_updates.py)."""

from gi.repository import Adw, GLib, Gtk

from . import system
from . import updates as up
from .main import Page, _, ask, inform, run_async, set_quietly, switch_row

ERRORS = {
    "busy": _("An update is already running."),
    "power": _("Connect the charger, or wait until the battery is at 50 %."),
    "kernel": _("The new system kernel is still being checked. Restart once it is confirmed."),
    "refused": _("Not done: it was not confirmed."),
    "value": _("This setting is not available here."),
}


PHASE_TEXT = {
    "starting": _("starting"),
    "plan": _("looking for updates"),
    "refresh": _("reading the package lists"),
    "download": _("downloading"),
    "snapshot": _("taking a snapshot"),
    "install": _("installing the packages"),
    "verify": _("checking the system"),
    "cleanup": _("removing old kernels"),
    "recheck": _("looking for more updates"),
}


def status_text(key):
    k = key[0]
    if k == "checking":
        return _("Looking for updates…")
    if k == "installing":
        return (
            _("Installing: %s") % PHASE_TEXT[key[1]] if key[1] in PHASE_TEXT else _("Installing…")
        )
    if k == "failed":
        return _("The last update did not finish: %s") % key[1]
    if k == "restart":
        return _("Updates installed. Restart the phone to finish.")
    if k == "updates":
        return _("%d updates are ready") % key[1] if key[1] != 1 else _("1 update is ready")
    if k == "up-to-date":
        return _("The phone is up to date")
    return _("Not checked yet")


def when_text(w):
    k, v = w
    if k == "today":
        return _("Checked today at %s") % v
    if k == "yesterday":
        return _("Checked yesterday at %s") % v
    if k == "date":
        return _("Checked on %s") % v
    return _("Never checked")


def why_not(reason):
    k = reason[0]
    if k == "none":
        return _("Nothing to install")
    if k == "busy":
        return _("An update is running")
    if k == "kernel":
        return _("The new system kernel is still being checked")
    if k == "refused":
        return _("An update would change what this phone must keep: %s") % "; ".join(reason[1][:2])
    if k == "power":
        return _("Connect the charger, or wait until the battery is at 50 %")
    return ""


class UpdatesPage(Page):
    icon = "software-update-available-symbolic"
    title = _("Updates")

    def build(self):
        self.st = {}
        self.poll = 0
        self.dynamic = []

        g = self.group(
            None,
            _(
                "OpenDuo's own parts and Debian's packages. A snapshot of the system is taken "
                "before anything is installed, so an update can be undone."
            ),
        )
        self.state_row = Adw.ActionRow(use_markup=False, title=_("Not checked yet"))
        self.spinner = Gtk.Spinner(valign=Gtk.Align.CENTER)
        self.state_row.add_suffix(self.spinner)
        self.check_button = Gtk.Button(label=_("Check now"), valign=Gtk.Align.CENTER)
        self.check_button.connect("clicked", lambda _b: self.check())
        self.state_row.add_suffix(self.check_button)
        g.add(self.state_row)
        self.install_row = Adw.ActionRow(use_markup=False, title=_("Install the updates"))
        self.install_button = Gtk.Button(label=_("Install"), valign=Gtk.Align.CENTER)
        self.install_button.add_css_class("suggested-action")
        self.install_button.connect("clicked", lambda _b: self.install())
        self.install_row.add_suffix(self.install_button)
        g.add(self.install_row)
        self.restart_row = Adw.ActionRow(use_markup=False, title=_("Restart to finish the update"))
        b = Gtk.Button(label=_("Restart"), valign=Gtk.Align.CENTER)
        b.connect("clicked", lambda _b: self.restart())
        self.restart_row.add_suffix(b)
        g.add(self.restart_row)
        self.kernel_row = Adw.ActionRow(use_markup=False, title=_("System kernel"))
        g.add(self.kernel_row)

        self.changes_group = self.group(_("What's new"))
        self.list_group = self.group(_("To install"))

        g = self.group(_("Automatic"))
        self.notify = switch_row(
            _("Tell me when updates are ready"),
            _("Looks once a day, on WiFi"),
            True,
            lambda on: self.set("notify", on),
        )
        g.add(self.notify)
        self.mobile = switch_row(
            _("Look on mobile data too"),
            _("The daily look downloads the package lists"),
            False,
            lambda on: self.set("mobile-data", on),
        )
        g.add(self.mobile)
        self.auto = switch_row(
            _("Install at night"),
            _(
                "Between 1 and 5 o'clock, on WiFi and the charger, with a snapshot first. "
                "The phone restarts only when you say so."
            ),
            False,
            lambda on: self.set("auto", on),
        )
        g.add(self.auto)

        self.dev_group = self.group(_("Developer"))
        self.testing = switch_row(
            _("Testing channel"),
            _(
                "Newer builds before they reach everyone. Back on stable, what testing "
                "installed stays until stable has a newer version."
            ),
            False,
            lambda on: self.set("channel", "testing" if on else "stable"),
        )
        self.dev_group.add(self.testing)

    # -- the helper -------------------------------------------------------------------------

    def helper(self, args, done=None):
        run_async(
            ["sudo", "-n", up.HELPER] + list(args), lambda ok, out: done and done(up.parse(out))
        )

    def shown(self):
        self.refresh()

    def hidden(self):
        super().hidden()
        if self.poll:
            GLib.source_remove(self.poll)
            self.poll = 0

    def refresh(self):
        def done(st):
            if st.get("ok"):
                self.show(st)
            else:
                self.state_row.set_title(_("Updates are not available on this phone"))
                self.state_row.set_subtitle(st.get("error") or "")
            self.schedule(up.poll_seconds(st) if st.get("ok") else 30)

        self.helper(["status"], done)

    def schedule(self, seconds):
        if self.poll:
            GLib.source_remove(self.poll)

        def tick():
            self.poll = 0
            self.refresh()
            return False

        self.poll = GLib.timeout_add_seconds(seconds, tick)

    def check(self):
        self.check_button.set_sensitive(False)
        self.helper(["check"], lambda r: self.after(r))

    def install(self):
        self.install_button.set_sensitive(False)
        self.helper(["install"], lambda r: self.after(r))

    def after(self, reply):
        if not reply.get("ok"):
            inform(
                self.window, _("Updates"), ERRORS.get(reply.get("code")) or reply.get("error") or ""
            )
        self.refresh()

    def set(self, key, on):
        value = on if isinstance(on, str) else ("on" if on else "off")

        def done(reply):
            if reply.get("ok"):
                self.refresh()
            else:
                self.after(reply)

        self.helper(["set", key, value], done)

    def restart(self):
        ask(
            self.window,
            _("Restart now?"),
            _("The updates take effect when the phone starts again."),
            _("Restart"),
            lambda: run_async(["sudo", "-n", system.SHELL_HELPER, "reboot"]),
            cancel_label=_("Later"),
        )

    # -- the rows ---------------------------------------------------------------------------

    def show(self, st):
        self.st = st
        key = up.status(st)
        running = up.job_running(st)
        self.state_row.set_title(status_text(key))
        self.state_row.set_subtitle(when_text(up.when((st.get("last") or {}).get("time"))))
        self.spinner.set_spinning(running)
        self.spinner.set_visible(running)
        self.check_button.set_sensitive(not running)
        ok, why = up.can_install(st)
        n_ours, n_deb, n_sec = up.counts(st)
        self.install_row.set_visible(bool(n_ours + n_deb) and key[0] != "restart")
        self.install_button.set_sensitive(ok)
        self.install_row.set_subtitle(
            (_("%d from OpenDuo, %d from Debian (%d security)") % (n_ours, n_deb, n_sec))
            if ok
            else why_not(why)
        )
        self.restart_row.set_visible(bool(st.get("reboot_required")))
        note = up.kernel_note(st)
        self.kernel_row.set_visible(note is not None)
        if note and note[0] == "trying":
            self.kernel_row.set_subtitle(
                _(
                    "The new kernel %s is being checked. If it does not pass, the phone goes back to the previous one by itself."
                )
                % note[1]
            )
        elif note:
            self.kernel_row.set_subtitle(
                _("The kernel %s did not pass its checks: the phone uses the previous one.")
                % note[1]
            )
        conf = st.get("conf") or {}
        set_quietly(self.notify, conf.get("notify") == "on")
        set_quietly(self.mobile, conf.get("check_on_mobile_data") == "on")
        set_quietly(self.auto, conf.get("auto_install") == "on")
        set_quietly(self.testing, conf.get("channel") == "testing")
        self.dev_group.set_visible(
            bool(st.get("developer_mode")) or conf.get("channel") == "testing"
        )
        self.fill_lists(st)

    def fill_lists(self, st):
        for group, row in self.dynamic:
            group.remove(row)
        self.dynamic = []

        def add(group, row):
            group.add(row)
            self.dynamic.append((group, row))

        secs = up.sections(st)
        self.changes_group.set_visible(bool(secs))
        for s in secs:
            exp = Adw.ExpanderRow(title=s["title"], use_markup=False)
            for head, points in s["items"]:
                text = "\n".join("• " + p for p in points)
                row = Adw.ActionRow(use_markup=False, title=head or _("Changes"), subtitle=text)
                row.set_subtitle_lines(0)
                exp.add_row(row)
            add(self.changes_group, exp)
        rows = up.package_rows(st)
        names = up.debian_names(st)
        self.list_group.set_visible(bool(rows or names))
        for name, versions in rows:
            add(self.list_group, Adw.ActionRow(use_markup=False, title=name, subtitle=versions))
        if names:
            exp = Adw.ExpanderRow(
                title=_("Debian packages"), subtitle=str(len(names)), use_markup=False
            )
            row = Adw.ActionRow(use_markup=False, title=", ".join(names))
            row.set_title_lines(0)
            exp.add_row(row)
            add(self.list_group, exp)
