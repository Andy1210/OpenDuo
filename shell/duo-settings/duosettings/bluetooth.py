"""Bluetooth through BlueZ on the system bus (org.bluez), as the user: the
adapter's power and discovery, the devices (with their battery, Battery1,
and whether one is a Surface pen), pairing with an agent (org.bluez.Agent1)
whose questions the settings window answers.

One instance for the whole window (`shared`): the Bluetooth and the Pen
pages both use it (one agent object on the bus); `ui` is the page shown.
BlueZ makes the adapter bondable only while a default agent is registered
(AlwaysPairable is off): an LE device paired then keeps its keys, and is
reconnected by the kernel's background scan when it advertises (a trusted
HID device: its auto-connect)."""

import time

from gi.repository import Gio, GLib

from .pen import is_surface_pen

BUS = "org.bluez"
AGENT_PATH = "/org/duo2/settings/agent"
AGENT_XML = """
<node>
  <interface name="org.bluez.Agent1">
    <method name="Release"/>
    <method name="RequestPinCode"><arg type="o" direction="in"/><arg type="s" direction="out"/></method>
    <method name="DisplayPinCode"><arg type="o" direction="in"/><arg type="s" direction="in"/></method>
    <method name="RequestPasskey"><arg type="o" direction="in"/><arg type="u" direction="out"/></method>
    <method name="DisplayPasskey"><arg type="o" direction="in"/><arg type="u" direction="in"/><arg type="q" direction="in"/></method>
    <method name="RequestConfirmation"><arg type="o" direction="in"/><arg type="u" direction="in"/></method>
    <method name="RequestAuthorization"><arg type="o" direction="in"/></method>
    <method name="AuthorizeService"><arg type="o" direction="in"/><arg type="s" direction="in"/></method>
    <method name="Cancel"/>
  </interface>
</node>
"""


def device_sort_key(dev):
    return (
        not dev.get("connected"),
        not dev.get("paired"),
        -(dev.get("rssi") or -200),
        (dev.get("alias") or "").lower(),
    )


class Bluez:
    """`on_change()` is called whenever an adapter or a device changes.
    `ui` answers the agent: an object with request_pin(name, done),
    request_passkey(name, done), confirm(name, passkey, done),
    authorize(name, done), show_code(name, code), cancel()."""

    def __init__(self, on_change, ui):
        self.listeners = [on_change]
        self.ui = ui
        self.bus = None
        self.objects = {}
        # When each device was last heard advertising (monotonic seconds):
        # its RSSI or advertising data changed (only during a discovery).
        self.seen = {}
        self.subs = []
        self.agent_id = None
        self.agent_registered = False
        try:
            self.bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        except GLib.Error:
            return
        for iface, member in (
            ("org.freedesktop.DBus.ObjectManager", "InterfacesAdded"),
            ("org.freedesktop.DBus.ObjectManager", "InterfacesRemoved"),
            ("org.freedesktop.DBus.Properties", "PropertiesChanged"),
        ):
            self.subs.append(
                self.bus.signal_subscribe(
                    BUS, iface, member, None, None, Gio.DBusSignalFlags.NONE, self._signal
                )
            )
        self.reload()

    # -- state --------------------------------------------------------------------
    def reload(self):
        try:
            res = self.bus.call_sync(
                BUS,
                "/",
                "org.freedesktop.DBus.ObjectManager",
                "GetManagedObjects",
                None,
                None,
                Gio.DBusCallFlags.NONE,
                3000,
                None,
            )
            self.objects = res.unpack()[0]
        except GLib.Error:
            self.objects = {}

    def on_change(self):
        for listener in list(self.listeners):
            listener()

    def _signal(self, _conn, _sender, path, iface, member, params):
        args = params.unpack()
        if member == "InterfacesAdded":
            self.objects.setdefault(args[0], {}).update(args[1])
            if "RSSI" in args[1].get("org.bluez.Device1", {}):
                self.seen[args[0]] = time.monotonic()
        elif member == "InterfacesRemoved":
            obj = self.objects.get(args[0], {})
            for i in args[1]:
                obj.pop(i, None)
            if not obj:
                self.objects.pop(args[0], None)
                self.seen.pop(args[0], None)
        elif member == "PropertiesChanged":
            obj = self.objects.get(path)
            if obj is None or args[0] not in obj:
                return
            obj[args[0]].update(args[1])
            for name in args[2]:
                obj[args[0]].pop(name, None)
            if args[0] == "org.bluez.Device1" and {"RSSI", "ManufacturerData", "ServiceData"} & set(
                args[1]
            ):
                self.seen[path] = time.monotonic()
        self.on_change()

    def adapter(self):
        for path, ifaces in sorted(self.objects.items()):
            if "org.bluez.Adapter1" in ifaces:
                return path, ifaces["org.bluez.Adapter1"]
        return None, {}

    def available(self):
        return self.adapter()[0] is not None

    def powered(self):
        return bool(self.adapter()[1].get("Powered"))

    def discovering(self):
        return bool(self.adapter()[1].get("Discovering"))

    def devices(self):
        adapter, _ = self.adapter()
        out = []
        for path, ifaces in self.objects.items():
            d = ifaces.get("org.bluez.Device1")
            if not d or (adapter and d.get("Adapter") != adapter):
                continue
            battery = ifaces.get("org.bluez.Battery1", {}).get("Percentage")
            pen = is_surface_pen(d)
            out.append(
                {
                    "path": path,
                    "alias": d.get("Alias") or d.get("Name") or d.get("Address"),
                    "named": "Name" in d or pen,
                    "paired": bool(d.get("Paired")),
                    "connected": bool(d.get("Connected")),
                    "trusted": bool(d.get("Trusted")),
                    "icon": "input-tablet" if pen else d.get("Icon") or "bluetooth",
                    "rssi": d.get("RSSI"),
                    "address": d.get("Address", ""),
                    "battery": int(battery) if battery is not None else None,
                    "pen": pen,
                    "seen": self.seen.get(path),
                }
            )
        return sorted(out, key=device_sort_key)

    def name_of(self, path):
        d = self.objects.get(path, {}).get("org.bluez.Device1", {})
        return d.get("Alias") or d.get("Name") or d.get("Address") or path

    # -- actions ------------------------------------------------------------------
    def _set(self, path, iface, prop, value):
        self.bus.call(
            BUS,
            path,
            "org.freedesktop.DBus.Properties",
            "Set",
            GLib.Variant("(ssv)", (iface, prop, value)),
            None,
            Gio.DBusCallFlags.NONE,
            5000,
            None,
            None,
            None,
        )

    def set_powered(self, on):
        path, _ = self.adapter()
        if path:
            self._set(path, "org.bluez.Adapter1", "Powered", GLib.Variant("b", on))

    def _call(self, path, iface, method, args=None, done=None, timeout=30000):
        def finish(bus, res):
            try:
                bus.call_finish(res)
                err = None
            except GLib.Error as e:
                err = e.message
            if done:
                done(err)

        self.bus.call(
            BUS, path, iface, method, args, None, Gio.DBusCallFlags.NONE, timeout, None, finish
        )

    def start_discovery(self):
        path, props = self.adapter()
        if path and props.get("Powered") and not props.get("Discovering"):
            self._call(path, "org.bluez.Adapter1", "StartDiscovery")

    def stop_discovery(self):
        path, props = self.adapter()
        if path and props.get("Discovering"):
            self._call(path, "org.bluez.Adapter1", "StopDiscovery")

    def set_discovery_filter(self, transport=None, done=None):
        """LE only (`le`) for the pen's pairing walk, or no filter (None): the
        filter is this client's, and goes with the next discovery."""
        path, _ = self.adapter()
        if not path:
            return
        flt = {"Transport": GLib.Variant("s", transport)} if transport else {}
        self._call(
            path,
            "org.bluez.Adapter1",
            "SetDiscoveryFilter",
            GLib.Variant("(a{sv})", (flt,)),
            done=done,
        )

    def pair(self, dev, done):
        self.register_agent()

        def paired(err):
            if err is None:
                self._set(dev, "org.bluez.Device1", "Trusted", GLib.Variant("b", True))
                self.connect(dev, lambda _e: None)
            done(err)

        self._call(dev, "org.bluez.Device1", "Pair", done=paired, timeout=60000)

    def connect(self, dev, done):
        self._call(dev, "org.bluez.Device1", "Connect", done=done)

    def disconnect(self, dev, done):
        self._call(dev, "org.bluez.Device1", "Disconnect", done=done)

    def forget(self, dev, done):
        path, _ = self.adapter()
        if path:
            self._call(
                path, "org.bluez.Adapter1", "RemoveDevice", GLib.Variant("(o)", (dev,)), done=done
            )

    # -- the agent ----------------------------------------------------------------
    def register_agent(self):
        if self.agent_registered or self.bus is None:
            return
        if self.agent_id is None:
            node = Gio.DBusNodeInfo.new_for_xml(AGENT_XML)
            self.agent_id = self.bus.register_object(
                AGENT_PATH, node.interfaces[0], self._agent_call, None, None
            )
        try:
            self.bus.call_sync(
                BUS,
                "/org/bluez",
                "org.bluez.AgentManager1",
                "RegisterAgent",
                GLib.Variant("(os)", (AGENT_PATH, "KeyboardDisplay")),
                None,
                Gio.DBusCallFlags.NONE,
                3000,
                None,
            )
            self.bus.call_sync(
                BUS,
                "/org/bluez",
                "org.bluez.AgentManager1",
                "RequestDefaultAgent",
                GLib.Variant("(o)", (AGENT_PATH,)),
                None,
                Gio.DBusCallFlags.NONE,
                3000,
                None,
            )
            self.agent_registered = True
        except GLib.Error:
            pass

    def _agent_call(self, _conn, _sender, _path, _iface, method, params, inv):
        args = params.unpack()
        rejected = "org.bluez.Error.Rejected"

        def answer(value=None, fmt=None):
            if value is None and fmt is not None:
                inv.return_dbus_error(rejected, "Rejected")
            elif fmt is None:
                inv.return_value(None)
            else:
                inv.return_value(GLib.Variant(fmt, (value,)))

        if method == "RequestPinCode":
            self.ui.request_pin(self.name_of(args[0]), lambda pin: answer(pin, "(s)"))
        elif method == "RequestPasskey":
            self.ui.request_passkey(
                self.name_of(args[0]), lambda key: answer(None if key is None else int(key), "(u)")
            )
        elif method == "DisplayPinCode":
            self.ui.show_code(self.name_of(args[0]), args[1])
            answer()
        elif method == "DisplayPasskey":
            self.ui.show_code(self.name_of(args[0]), "%06d" % args[1])
            answer()
        elif method == "RequestConfirmation":
            self.ui.confirm(
                self.name_of(args[0]),
                "%06d" % args[1],
                lambda ok: answer() if ok else inv.return_dbus_error(rejected, "Rejected"),
            )
        elif method == "RequestAuthorization":
            self.ui.authorize(
                self.name_of(args[0]),
                lambda ok: answer() if ok else inv.return_dbus_error(rejected, "Rejected"),
            )
        elif method == "AuthorizeService":
            # A paired, trusted device using one of its services: yes.
            answer()
        elif method == "Cancel":
            self.ui.cancel()
            answer()
        else:  # Release
            self.agent_registered = False
            answer()

    def close(self):
        global _shared
        if _shared is self:
            _shared = None
        if self.bus is None:
            return
        for s in self.subs:
            self.bus.signal_unsubscribe(s)
        self.subs = []
        if self.agent_registered:
            try:
                self.bus.call_sync(
                    BUS,
                    "/org/bluez",
                    "org.bluez.AgentManager1",
                    "UnregisterAgent",
                    GLib.Variant("(o)", (AGENT_PATH,)),
                    None,
                    Gio.DBusCallFlags.NONE,
                    2000,
                    None,
                )
            except GLib.Error:
                pass
            self.agent_registered = False


_shared = None


def shared(on_change, ui):
    """The window's one Bluez: made by the first page that asks, and every
    page's `on_change` called on a change."""
    global _shared
    if _shared is None:
        _shared = Bluez(on_change, ui)
    elif on_change not in _shared.listeners:
        _shared.listeners.append(on_change)
    return _shared
