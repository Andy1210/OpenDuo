"""The notification daemon (org.freedesktop.Notifications on the session
bus) and its banners.

A banner is a wlr-layer-shell surface on the overlay layer, along the top of
ONE panel: the panel of the window with the keyboard focus (duo-wc's
`keyboard_output`), where the user is looking. It shows the newest
notification for a few seconds (notifystore.Notification.banner_ms); swiping
it up or sideways hides it (it stays in the list), tapping it runs the
notification's default action. The list is in the quick menu
(quickmenu.py); the status bar shows the newest apps' icons.

`notify-send` (libnotify-bin) talks to this.
"""

from gi.repository import Gio, GLib, Gtk, Pango
from gi.repository import Gtk4LayerShell as LayerShell

from . import notifystore, timefmt
from .i18n import _
from .widgets import Swipeable, icon_image

BUS_NAME = "org.freedesktop.Notifications"
PATH = "/org/freedesktop/Notifications"
IFACE = "org.freedesktop.Notifications"

INTROSPECTION = """
<node>
  <interface name="org.freedesktop.Notifications">
    <method name="GetCapabilities">
      <arg type="as" name="capabilities" direction="out"/>
    </method>
    <method name="Notify">
      <arg type="s" name="app_name" direction="in"/>
      <arg type="u" name="replaces_id" direction="in"/>
      <arg type="s" name="app_icon" direction="in"/>
      <arg type="s" name="summary" direction="in"/>
      <arg type="s" name="body" direction="in"/>
      <arg type="as" name="actions" direction="in"/>
      <arg type="a{sv}" name="hints" direction="in"/>
      <arg type="i" name="expire_timeout" direction="in"/>
      <arg type="u" name="id" direction="out"/>
    </method>
    <method name="CloseNotification">
      <arg type="u" name="id" direction="in"/>
    </method>
    <method name="GetServerInformation">
      <arg type="s" name="name" direction="out"/>
      <arg type="s" name="vendor" direction="out"/>
      <arg type="s" name="version" direction="out"/>
      <arg type="s" name="spec_version" direction="out"/>
    </method>
    <signal name="NotificationClosed">
      <arg type="u" name="id"/>
      <arg type="u" name="reason"/>
    </signal>
    <signal name="ActionInvoked">
      <arg type="u" name="id"/>
      <arg type="s" name="action_key"/>
    </signal>
    <signal name="ActivationToken">
      <arg type="u" name="id"/>
      <arg type="s" name="activation_token"/>
    </signal>
  </interface>
</node>
"""

CAPABILITIES = ["actions", "body", "icon-static", "persistence"]


def log(*args):
    print("duo-shell:", *args, flush=True)


def card(n, on_dismiss, on_tap, on_action, up=False, css="notification"):
    """A notification as a swipeable card (banner and list alike)."""
    box = Swipeable(on_dismiss, on_tap, up=up, orientation=Gtk.Orientation.VERTICAL, spacing=4)
    box.add_css_class(css)
    if n.urgency == notifystore.URGENCY_CRITICAL:
        box.add_css_class("critical")
    row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
    row.append(icon_image(n.icon(), 32))
    text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
    text.set_hexpand(True)
    head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
    app = Gtk.Label(label=n.app_name or _("Notification"), xalign=0)
    app.add_css_class("app")
    app.set_ellipsize(Pango.EllipsizeMode.END)
    app.set_hexpand(True)
    when = Gtk.Label(label=GLib.DateTime.new_from_unix_local(int(n.at)).format(timefmt.hm()) or "")
    when.add_css_class("when")
    head.append(app)
    head.append(when)
    text.append(head)
    if n.summary:
        summary = Gtk.Label(label=n.summary, xalign=0)
        summary.add_css_class("summary")
        summary.set_wrap(True)
        summary.set_lines(2)
        summary.set_ellipsize(Pango.EllipsizeMode.END)
        text.append(summary)
    if n.body:
        body = Gtk.Label(label=n.body, xalign=0)
        body.add_css_class("body")
        body.set_wrap(True)
        body.set_lines(3)
        body.set_ellipsize(Pango.EllipsizeMode.END)
        text.append(body)
    row.append(text)
    box.append(row)
    buttons = [(k, label) for k, label in n.actions if k != "default"][:3]
    if buttons:
        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6, homogeneous=True)
        actions.add_css_class("actions")
        for key, label in buttons:
            b = Gtk.Button(label=label)
            b.set_can_focus(False)
            b.connect("clicked", lambda _b, k=key: on_action(k))
            actions.append(b)
        box.append(actions)
    return box


_notify_settings = None


def banners_wanted():
    """GNOME's do-not-disturb switch (org.gnome.desktop.notifications
    show-banners), which Duo Settings sets: off = no banners, except
    critical ones."""
    global _notify_settings
    try:
        if _notify_settings is None:
            _notify_settings = Gio.Settings.new("org.gnome.desktop.notifications")
        return _notify_settings.get_boolean("show-banners")
    except Exception:
        return True


class Banner(Gtk.Window):
    """One panel's banner surface."""

    def __init__(self, app, center, monitor):
        super().__init__(application=app)
        self.center = center
        self.monitor = monitor
        self.nid = None
        self.timer = None
        self.add_css_class("duo-banner")
        self.set_decorated(False)
        LayerShell.init_for_window(self)
        LayerShell.set_namespace(self, "duo-banner")
        LayerShell.set_layer(self, LayerShell.Layer.OVERLAY)
        LayerShell.set_monitor(self, monitor)
        for edge in (LayerShell.Edge.TOP, LayerShell.Edge.LEFT, LayerShell.Edge.RIGHT):
            LayerShell.set_anchor(self, edge, True)
        LayerShell.set_margin(self, LayerShell.Edge.TOP, 52)
        LayerShell.set_margin(self, LayerShell.Edge.LEFT, 8)
        LayerShell.set_margin(self, LayerShell.Edge.RIGHT, 8)
        LayerShell.set_keyboard_mode(self, LayerShell.KeyboardMode.NONE)
        LayerShell.set_exclusive_zone(self, -1)

    def show_notification(self, n):
        self.nid = n.id
        self.set_child(
            card(
                n,
                on_dismiss=self.hide_banner,
                on_tap=lambda: self.center.tapped(n.id),
                on_action=lambda key: self.center.invoke(n.id, key),
                up=True,
                css="banner-card",
            )
        )
        self.present()
        if self.timer is not None:
            GLib.source_remove(self.timer)
            self.timer = None
        ms = n.banner_ms()
        if ms is not None:
            self.timer = GLib.timeout_add(ms, self._expired)

    def _expired(self):
        self.timer = None
        n = self.center.store.get(self.nid) if self.nid is not None else None
        self.hide_banner()
        if n is not None and n.transient:
            self.center.close(n.id, notifystore.EXPIRED)
        return False

    def hide_banner(self):
        if self.timer is not None:
            GLib.source_remove(self.timer)
            self.timer = None
        self.nid = None
        self.set_visible(False)


class NotificationCenter:
    """The daemon: owns the bus name, keeps the store, drives the banners.
    `on_change()` is called whenever the list changes (bars, quick menus)."""

    def __init__(self, app, shell, on_change):
        self.app = app
        self.shell = shell
        self.on_change = on_change
        self.store = notifystore.Store()
        self.banners = {}
        self.local_actions = {}  # nid -> callback(key): the shell's own notifications
        self.conn = None
        self.node = Gio.DBusNodeInfo.new_for_xml(INTROSPECTION)
        self.owner = Gio.bus_own_name(
            Gio.BusType.SESSION,
            BUS_NAME,
            Gio.BusNameOwnerFlags.ALLOW_REPLACEMENT | Gio.BusNameOwnerFlags.REPLACE,
            self._bus_acquired,
            lambda *_a: log("notifications: name acquired"),
            lambda *_a: log("notifications: name lost (another daemon?)"),
        )

    # -- D-Bus ------------------------------------------------------------------

    def _bus_acquired(self, conn, _name):
        self.conn = conn
        try:
            conn.register_object(PATH, self.node.interfaces[0], self._call, None, None)
        except GLib.Error as e:
            log("notifications: cannot register: %s" % e.message)

    def _call(self, _conn, _sender, _path, _iface, method, params, invocation):
        try:
            if method == "GetCapabilities":
                invocation.return_value(GLib.Variant("(as)", (CAPABILITIES,)))
            elif method == "GetServerInformation":
                invocation.return_value(
                    GLib.Variant("(ssss)", ("duo-shell", "duo2-os", "0.1", "1.2"))
                )
            elif method == "Notify":
                nid = self.notify(*params.unpack())
                invocation.return_value(GLib.Variant("(u)", (nid,)))
            elif method == "CloseNotification":
                (nid,) = params.unpack()
                self.close(nid, notifystore.CLOSED_BY_CALL)
                invocation.return_value(None)
            else:
                invocation.return_dbus_error("org.freedesktop.DBus.Error.UnknownMethod", method)
        except Exception as e:  # never leave a caller hanging
            log("notifications: %s failed: %r" % (method, e))
            invocation.return_dbus_error("org.freedesktop.DBus.Error.Failed", str(e))

    def _signal(self, name, variant):
        if self.conn is None:
            return
        try:
            self.conn.emit_signal(None, PATH, IFACE, name, variant)
        except GLib.Error as e:
            log("notifications: signal %s: %s" % (name, e.message))

    # -- the list -----------------------------------------------------------------

    def notify(
        self, app_name, replaces_id, app_icon, summary, body, actions, hints, expire_timeout
    ):
        n, replaced = self.store.notify(
            app_name,
            replaces_id,
            app_icon,
            summary,
            body,
            actions,
            hints,
            expire_timeout,
            GLib.get_real_time() / 1e6,
        )
        log(
            "notification %d from %r: %r%s"
            % (n.id, n.app_name, n.summary, " (replaces)" if replaced else "")
        )
        haptics = getattr(self.shell, "haptics", None)
        if haptics is not None:
            from . import hapticsmodel

            haptics.trigger(
                hapticsmodel.notification_event(
                    n.hints.get("category"), n.urgency, n.hints, replaced, banners_wanted()
                )
            )
        if notifystore.wakes_screen(n) and not self.shell.lit:
            # An alarm lights the screen; its banner shows once it is lit.
            self.pending = (n.id, GLib.get_monotonic_time())
            self.shell.wake_for_alarm("notification %d" % n.id)
        self.show_banner(n)
        self.on_change()
        return n.id

    def post(self, summary, body, actions=(), on_action=None, icon="", hints=None, replaces_id=0):
        """A notification of the shell itself (no D-Bus round trip); its
        actions call `on_action(key)` instead of signalling a client."""
        nid = self.notify(
            "Duo", replaces_id, icon, summary, body, list(actions), dict(hints or {}), -1
        )
        if on_action is not None:
            self.local_actions[nid] = on_action
        return nid

    def close(self, nid, reason):
        self.local_actions.pop(nid, None)
        n = self.store.remove(nid)
        if n is None:
            return
        for banner in self.banners.values():
            if banner.nid == nid:
                banner.hide_banner()
        self._signal("NotificationClosed", GLib.Variant("(uu)", (nid, reason)))
        self.on_change()

    def dismiss(self, nid):
        self.close(nid, notifystore.DISMISSED)

    def clear(self):
        for n in list(self.store.items):
            self.close(n.id, notifystore.DISMISSED)

    def invoke(self, nid, key):
        n = self.store.get(nid)
        if n is None:
            return
        log("notification %d: action %s" % (nid, key))
        callback = self.local_actions.get(nid)
        if callback is not None:
            try:
                callback(key)
            except Exception as e:  # a broken handler must not break the list
                log("notification %d: action %s failed: %r" % (nid, key, e))
            if not n.hints.get("resident", False):
                self.close(nid, notifystore.DISMISSED)
            return
        token = self._activation_token()
        if token:
            self._signal("ActivationToken", GLib.Variant("(us)", (nid, token)))
        self._signal("ActionInvoked", GLib.Variant("(us)", (nid, key)))
        if not n.hints.get("resident", False):
            self.close(nid, notifystore.DISMISSED)

    def tapped(self, nid):
        n = self.store.get(nid)
        if n is None:
            return
        if n.has_default_action():
            self.invoke(nid, "default")
        else:
            for banner in self.banners.values():
                if banner.nid == nid:
                    banner.hide_banner()

    def _activation_token(self):
        try:
            from gi.repository import Gdk

            context = Gdk.Display.get_default().get_app_launch_context()
            return context.get_startup_notify_id(None, []) or ""
        except Exception:
            return ""

    # -- banners -------------------------------------------------------------------

    def show_banner(self, n):
        name = self.shell.focus_output()
        monitor = self.shell.monitor_named(name)
        if monitor is None:
            return
        if not self.shell.lit:
            return  # dark screen: the list keeps it, no banner
        if not banners_wanted() and n.urgency != notifystore.URGENCY_CRITICAL:
            return  # do not disturb (Duo Settings): the list keeps it
        for other, banner in self.banners.items():
            if other != name:
                banner.hide_banner()
        banner = self.banners.get(name)
        if banner is None or banner.monitor is not monitor:
            banner = Banner(self.app, self, monitor)
            self.banners[name] = banner
        banner.show_notification(n)

    def screen_lit(self):
        """The screen came on: the banner of an alarm that woke it."""
        pending, self.pending = getattr(self, "pending", None), None
        if pending is None:
            return
        nid, at = pending
        n = self.store.get(nid)
        if n is not None and (GLib.get_monotonic_time() - at) < 15e6:
            self.show_banner(n)

    def drop_monitor(self, name):
        banner = self.banners.pop(name, None)
        if banner is not None:
            banner.destroy()
