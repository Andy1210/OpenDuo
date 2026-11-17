"""Settings > Developer: what someone who helps develop OpenDuo needs.

Developer mode (off by default) lets a computer log in over SSH (key login
only, as the administrator, over WiFi or a USB cable), manages the keys
that may, switches the USB network on, installs the developer tools and
lets the touch daemon log touch positions (its own switch, off by default,
off again with developer mode and after a restart); a bug report (logs with
the identifiers masked) can be collected with it on or off. Everything runs
through root's `duo-devmode` (devmode/ in the repository) with sudo; what
opens the phone (SSH on, a key added), the touch log and the bug report are
asked of the user in the shell first, so no other app can do them."""

import json

from gi.repository import Adw, Gio, GLib, Gtk

from .main import Page, _, ask, run_async, run_with_input, set_quietly, switch_row

DEVMODE = "/usr/local/sbin/duo-devmode"
KEY_FILE_MAX = 16 * 1024
IFACE_NAMES = {"wlan0": _("WiFi"), "usb0": _("USB cable")}


def parse(out):
    try:
        reply = json.loads((out or "").strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {}
    return reply if isinstance(reply, dict) else {}


class DeveloperPage(Page):
    icon = "utilities-terminal-symbolic"
    title = _("Developer")

    def build(self):
        self.status = {}
        self.busy = False
        g = self.group(
            None,
            _(
                "For people who help develop OpenDuo: log in to the phone from a computer over "
                "SSH, and collect a bug report to send."
            ),
        )
        self.mode = switch_row(
            _("Developer mode"),
            on_toggle=lambda on: self.devmode(["mode", "on" if on else "off"]),
        )
        g.add(self.mode)

        self.ssh_group = self.group(
            _("SSH"),
            _(
                "Key login only, no passwords, on WiFi and on a USB cable (never over mobile "
                "data). Whoever has one of the keys below can run anything on the phone."
            ),
        )
        self.ssh = switch_row(
            _("SSH"),
            on_toggle=lambda on: self.devmode(["ssh", "on" if on else "off"], _("SSH is on")),
        )
        self.ssh_group.add(self.ssh)
        self.account = Adw.ActionRow(use_markup=False, title=_("Account"))
        self.account.add_css_class("property")
        self.ssh_group.add(self.account)
        self.address_rows = []

        self.keys_group = self.group(
            _("Authorized keys"),
            _("The public keys of the computers that may log in (such as ~/.ssh/id_ed25519.pub)."),
        )
        add = Gtk.Button(icon_name="list-add-symbolic", valign=Gtk.Align.CENTER)
        add.add_css_class("flat")
        add.set_tooltip_text(_("Add a key"))
        add.connect("clicked", lambda _b: self.add_key())
        self.keys_group.set_header_suffix(add)
        self.key_rows = []

        self.usb_group = self.group(_("USB"))
        self.usb = switch_row(
            _("USB networking"),
            _("On a USB cable the phone is 10.15.19.1 and gives the computer an address"),
            on_toggle=lambda on: self.devmode(["usb", "on" if on else "off"]),
        )
        self.usb_group.add(self.usb)

        self.debug_group = self.group(_("Debugging"))
        self.touchlog = switch_row(
            _("Log touch positions"),
            _("For debugging, can reveal what you type"),
            on_toggle=lambda on: self.devmode(
                ["touchlog", "on" if on else "off"], _("Touch positions are logged")
            ),
        )
        self.debug_group.add(self.touchlog)
        self.tools = Adw.ActionRow(use_markup=False, title=_("Developer tools"))
        self.tools.add_css_class("property")
        self.debug_group.add(self.tools)

        g = self.group(_("Bug report"))
        self.report = Adw.ActionRow(
            use_markup=False,
            title=_("Collect a bug report"),
            subtitle=_(
                "The system's logs of this and the last start, with phone numbers, addresses, "
                "network names and other identifiers masked, into Downloads"
            ),
        )
        self.report_button = Gtk.Button(label=_("Collect"), valign=Gtk.Align.CENTER)
        self.report_button.connect("clicked", lambda _b: self.bugreport())
        self.report_spinner = Adw.Spinner(visible=False, valign=Gtk.Align.CENTER)
        self.report.add_suffix(self.report_spinner)
        self.report.add_suffix(self.report_button)
        g.add(self.report)
        self.apply({})

    def shown(self):
        self.every(5, self.refresh)

    # --- state ------------------------------------------------------------

    def refresh(self):
        if self.busy:
            return
        run_async(["sudo", "-n", DEVMODE, "status"], lambda ok, out: self.apply(parse(out)))

    def apply(self, st):
        self.status = st
        on = bool(st.get("developer_mode"))
        set_quietly(self.mode, on)
        self.mode.set_subtitle("" if on else _("Off: no SSH and no USB network"))
        for grp in (self.ssh_group, self.keys_group, self.usb_group, self.debug_group):
            grp.set_sensitive(on and bool(st))
        set_quietly(self.ssh, bool(st.get("ssh")))
        set_quietly(self.usb, bool(st.get("usb_network")))
        set_quietly(self.touchlog, on and bool(st.get("touch_log")))
        self.touchlog.set_subtitle(
            _("On until developer mode goes off or the phone restarts")
            if on and st.get("touch_log")
            else _("For debugging, can reveal what you type")
        )
        tools = st.get("devtools") or {}
        if not on:
            self.tools.set_subtitle(_("Installed with developer mode"))
        else:
            self.tools.set_subtitle(
                _("%(installed)d of %(listed)d installed (sleep, audio, WiFi, touch tests)")
                % {"installed": tools.get("installed", 0), "listed": tools.get("listed", 0)}
            )
        if st.get("ssh") and not st.get("ssh_active"):
            self.ssh.set_subtitle(_("On, but sshd does not run"))
        elif st.get("ssh") and not st.get("keys"):
            self.ssh.set_subtitle(_("On; add a key below to log in"))
        else:
            self.ssh.set_subtitle("")
        account = st.get("account") or "?"
        self.account.set_subtitle(_("%s (the administrator, sudo without a password)") % account)
        self.set_addresses(account, (st.get("addresses") or []) if st.get("ssh") else [])
        self.set_keys(st.get("keys") or [])
        return False

    def set_addresses(self, account, addresses):
        for row in self.address_rows:
            self.ssh_group.remove(row)
        self.address_rows = []
        for a in addresses:
            cmd = "ssh %s@%s" % (account, a.get("address", "?"))
            row = Adw.ActionRow(
                use_markup=False,
                title=cmd,
                subtitle=IFACE_NAMES.get(a.get("iface"), a.get("iface") or ""),
            )
            copy = Gtk.Button(icon_name="edit-copy-symbolic", valign=Gtk.Align.CENTER)
            copy.add_css_class("flat")
            copy.set_tooltip_text(_("Copy"))
            copy.connect("clicked", lambda _b, c=cmd: self.copy(c))
            row.add_suffix(copy)
            self.ssh_group.add(row)
            self.address_rows.append(row)

    def set_keys(self, keys):
        for row in self.key_rows:
            self.keys_group.remove(row)
        self.key_rows = []
        if not keys:
            row = Adw.ActionRow(use_markup=False, title=_("No keys yet"))
            self.keys_group.add(row)
            self.key_rows.append(row)
            return
        for k in keys:
            row = Adw.ActionRow(
                use_markup=False,
                title=k.get("comment") or k.get("type", "?"),
                subtitle=k.get("fingerprint", ""),
            )
            row.set_subtitle_lines(1)
            remove = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER)
            remove.add_css_class("flat")
            remove.set_tooltip_text(_("Remove"))
            remove.connect("clicked", lambda _b, key=k: self.remove_key(key))
            row.add_suffix(remove)
            self.keys_group.add(row)
            self.key_rows.append(row)

    def copy(self, text):
        self.get_clipboard().set(text)
        self.toast(_("Copied"))

    # --- actions ------------------------------------------------------------

    def devmode(self, args, done_text=None):
        def done(_ok, out):
            reply = parse(out)
            if reply.get("ok"):
                if done_text:
                    self.toast(done_text)
            else:
                self.failed(reply)
            self.busy = False
            self.refresh()

        self.busy = True
        run_async(["sudo", "-n", DEVMODE] + args, done)

    def failed(self, reply):
        err = reply.get("error") or _("It did not work")
        if err == "not confirmed":
            self.toast(_("Not allowed"))
        else:
            self.toast(err)

    def remove_key(self, key):
        fp = key.get("fingerprint", "")
        ask(
            self.window,
            _("Remove this key?"),
            _("%s can no longer log in.") % (key.get("comment") or fp),
            _("Remove"),
            lambda: self.devmode(["key", "remove", fp], _("Key removed")),
            destructive=True,
        )

    def add_key(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        view = Gtk.TextView(wrap_mode=Gtk.WrapMode.CHAR, monospace=True)
        view.set_size_request(-1, 120)
        frame = Gtk.ScrolledWindow(child=view, min_content_height=120)
        frame.add_css_class("card")
        box.append(frame)
        pick = Gtk.Button(label=_("From a file…"))
        pick.connect("clicked", lambda _b: self.pick_key_file(view))
        box.append(pick)
        d = ask(
            self.window,
            _("Add a key"),
            _("Paste a public key (one line: ssh-ed25519 AAAA… name), or pick its .pub file."),
            _("Add"),
            lambda: self.send_key(view.get_buffer().get_property("text")),
            extra=box,
        )
        d.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
        return d

    def pick_key_file(self, view):
        dialog = Gtk.FileDialog(title=_("A public key file"))
        f = Gtk.FileFilter()
        f.set_name(_("Public keys (.pub)"))
        f.add_pattern("*.pub")
        f.add_pattern("authorized_keys")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(f)
        dialog.set_filters(filters)

        def opened(dlg, res):
            try:
                file = dlg.open_finish(res)
            except GLib.Error:
                return
            file.load_contents_async(None, lambda fl, r: loaded(fl, r))

        def loaded(fl, res):
            try:
                _ok, data, _etag = fl.load_contents_finish(res)
            except GLib.Error as e:
                self.toast(e.message)
                return
            if len(data) > KEY_FILE_MAX:
                self.toast(_("That file is too big for a public key"))
                return
            view.get_buffer().set_text(data.decode("utf-8", "replace"))

        dialog.open(self.window, None, opened)

    def send_key(self, text):
        if not text.strip():
            return

        def done(code, out):
            reply = parse(out)
            if code == 0 and reply.get("ok"):
                self.toast(_("Key added") if reply.get("added") else _("That key is there already"))
            else:
                self.failed(reply)
            self.busy = False
            self.refresh()

        self.busy = True
        run_with_input(["sudo", "-n", DEVMODE, "key", "add"], text, done)

    def bugreport(self):
        self.report_button.set_sensitive(False)
        self.report_spinner.set_visible(True)

        def done(_ok, out):
            reply = parse(out)
            self.report_button.set_sensitive(True)
            self.report_spinner.set_visible(False)
            if reply.get("ok"):
                name = (reply.get("path") or "").rsplit("/", 1)[-1]
                self.toast(_("Saved in Downloads: %s") % name)
            else:
                self.failed(reply)

        run_async(["sudo", "-n", DEVMODE, "bugreport"], done)
