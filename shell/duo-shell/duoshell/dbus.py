"""The two D-Bus services the bar talks to.

- BlueZ (system bus): whether a Bluetooth adapter is there and powered; and
  the paired Surface pens with their battery (penmodel.pens: the pen's
  low-battery notice, pen.py).
- the on-screen keyboard (session bus, sm.puri.OSK0: phosh-osk-stub or
  squeekboard, whichever the session started): whether it is shown, and
  showing / hiding it.

Both are asynchronous and never raise into GTK: a missing service is just
"unknown" (None).
"""

from gi.repository import Gio, GLib

BLUEZ = "org.bluez"
ADAPTER = "org.bluez.Adapter1"
BATTERY = "org.bluez.Battery1"
PROPS = "org.freedesktop.DBus.Properties"

OSK_NAME = "sm.puri.OSK0"
OSK_PATH = "/sm/puri/OSK0"
OSK_IFACE = "sm.puri.OSK0"


class Bluetooth:
    """`powered`: True / False, or None while there is no adapter. `on_pens`
    (optional) gets penmodel.pens() whenever BlueZ's objects are read again
    (a device or a battery came, went or changed)."""

    def __init__(self, on_change, on_pens=None):
        self.on_change = on_change
        self.on_pens = on_pens
        self.powered = None
        self.bus = None
        Gio.bus_get(Gio.BusType.SYSTEM, None, self._got_bus)

    def _got_bus(self, _src, result):
        try:
            self.bus = Gio.bus_get_finish(result)
        except GLib.Error as e:
            print("duo-shell: no system bus: %s" % e.message, flush=True)
            return
        self.bus.signal_subscribe(
            BLUEZ,
            PROPS,
            "PropertiesChanged",
            None,
            ADAPTER,
            Gio.DBusSignalFlags.NONE,
            self._changed,
        )
        # A pen's battery level (its Connected comes with InterfacesAdded /
        # Removed of the battery: BlueZ shows Battery1 while it is connected).
        self.bus.signal_subscribe(
            BLUEZ,
            PROPS,
            "PropertiesChanged",
            None,
            BATTERY,
            Gio.DBusSignalFlags.NONE,
            self._changed,
        )
        for member in ("InterfacesAdded", "InterfacesRemoved"):
            self.bus.signal_subscribe(
                BLUEZ,
                "org.freedesktop.DBus.ObjectManager",
                member,
                None,
                None,
                Gio.DBusSignalFlags.NONE,
                self._changed,
            )
        self.bus.signal_subscribe(
            "org.freedesktop.DBus",
            "org.freedesktop.DBus",
            "NameOwnerChanged",
            None,
            BLUEZ,
            Gio.DBusSignalFlags.NONE,
            self._changed,
        )
        self.refresh()

    def _changed(self, *_args):
        self.refresh()

    def refresh(self):
        if self.bus is None:
            return
        self.bus.call(
            BLUEZ,
            "/",
            "org.freedesktop.DBus.ObjectManager",
            "GetManagedObjects",
            None,
            GLib.VariantType("(a{oa{sa{sv}}})"),
            Gio.DBusCallFlags.NONE,
            3000,
            None,
            self._got_objects,
        )

    def _got_objects(self, bus, result):
        powered = None
        self.adapter = None
        objects = {}
        try:
            (objects,) = bus.call_finish(result).unpack()
            for path, ifaces in sorted(objects.items()):
                if ADAPTER in ifaces:
                    powered = bool(ifaces[ADAPTER].get("Powered", False))
                    self.adapter = path
                    break
        except GLib.Error:
            powered = None
        if powered != self.powered:
            self.powered = powered
            self.on_change()
        if self.on_pens is not None:
            from .penmodel import pens

            self.on_pens(pens(objects))

    def set_powered(self, on):
        """Switch the adapter on or off (BlueZ lets the user do it)."""
        path = getattr(self, "adapter", None)
        if self.bus is None or path is None:
            return
        self.bus.call(
            BLUEZ,
            path,
            PROPS,
            "Set",
            GLib.Variant("(ssv)", (ADAPTER, "Powered", GLib.Variant("b", bool(on)))),
            None,
            Gio.DBusCallFlags.NONE,
            5000,
            None,
            self._set_done,
        )

    def _set_done(self, bus, result):
        try:
            bus.call_finish(result)
        except GLib.Error as e:
            print("duo-shell: Bluetooth: %s" % e.message, flush=True)
        self.refresh()


class Keyboard:
    """The on-screen keyboard (sm.puri.OSK0). `visible`: True / False, or None
    while none is running."""

    def __init__(self, on_change):
        self.on_change = on_change
        self.visible = None
        self.proxy = None
        Gio.DBusProxy.new_for_bus(
            Gio.BusType.SESSION,
            Gio.DBusProxyFlags.NONE,
            None,
            OSK_NAME,
            OSK_PATH,
            OSK_IFACE,
            None,
            self._got_proxy,
        )

    def _got_proxy(self, _src, result):
        try:
            self.proxy = Gio.DBusProxy.new_for_bus_finish(result)
        except GLib.Error as e:
            print("duo-shell: no session bus for the keyboard: %s" % e.message, flush=True)
            return
        self.proxy.connect("g-properties-changed", lambda *_a: self._update())
        self.proxy.connect("notify::g-name-owner", lambda *_a: self._update())
        self._update()

    def _update(self):
        visible = None
        if self.proxy is not None and self.proxy.get_name_owner():
            value = self.proxy.get_cached_property("Visible")
            visible = bool(value.unpack()) if value is not None else False
        if visible != self.visible:
            self.visible = visible
            self.on_change()

    def set_visible(self, visible):
        if self.proxy is None:
            return
        self.proxy.call(
            "SetVisible",
            GLib.Variant("(b)", (bool(visible),)),
            Gio.DBusCallFlags.NONE,
            2000,
            None,
            self._done,
        )

    def toggle(self):
        self.set_visible(not self.visible)

    def _done(self, proxy, result):
        try:
            proxy.call_finish(result)
        except GLib.Error as e:
            print("duo-shell: keyboard: %s" % e.message, flush=True)
