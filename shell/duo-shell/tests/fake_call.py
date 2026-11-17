#!/usr/bin/python3
"""A fake call and proximity sensor on the SESSION bus, for trying the
shell's proximity handling (duoshell/proximity.py reads the session bus
while root's /etc/duo2/shell-fake-call exists) without a SIM, a call or a
hand over the sensor.

    fake_call.py            then drive it with busctl --user:

    busctl --user call org.freedesktop.ModemManager1 /fake org.duo2os.FakeCall SetCall s active
          (none, dialing, ringing-out, ringing-in, active, held, terminated)
    busctl --user call org.freedesktop.ModemManager1 /fake org.duo2os.FakeCall SetNear b true

It plays the parts the shell reads: ModemManager's object manager with one
modem whose Modem.Voice lists the call, the call's State (PropertiesChanged
and StateChanged), and iio-sensor-proxy's HasProximity / ProximityNear /
ClaimProximity / ReleaseProximity (the sensor name is taken too). It prints
what happens (claims, states); nothing real is dialed.
"""

from gi.repository import Gio, GLib

MM = "org.freedesktop.ModemManager1"
ROOT = "/org/freedesktop/ModemManager1"
MODEM = ROOT + "/Modem/0"
CALLP = ROOT + "/Call/1"
VOICE = "org.freedesktop.ModemManager1.Modem.Voice"
CALL = "org.freedesktop.ModemManager1.Call"
SP = "net.hadess.SensorProxy"
SP_PATH = "/net/hadess/SensorProxy"
PROPS = "org.freedesktop.DBus.Properties"

STATES = {
    "unknown": 0,
    "dialing": 1,
    "ringing-out": 2,
    "ringing-in": 3,
    "active": 4,
    "held": 5,
    "waiting": 6,
    "terminated": 7,
}

XML = """
<node>
  <interface name="org.freedesktop.DBus.ObjectManager">
    <method name="GetManagedObjects"><arg type="a{oa{sa{sv}}}" direction="out"/></method>
    <signal name="InterfacesAdded"><arg type="o"/><arg type="a{sa{sv}}"/></signal>
    <signal name="InterfacesRemoved"><arg type="o"/><arg type="as"/></signal>
  </interface>
  <interface name="%(VOICE)s">
    <property name="Calls" type="ao" access="read"/>
    <signal name="CallAdded"><arg type="o"/></signal>
    <signal name="CallDeleted"><arg type="o"/></signal>
  </interface>
  <interface name="%(CALL)s">
    <property name="State" type="i" access="read"/>
    <signal name="StateChanged"><arg type="i"/><arg type="i"/><arg type="u"/></signal>
  </interface>
  <interface name="%(SP)s">
    <property name="HasProximity" type="b" access="read"/>
    <property name="ProximityNear" type="b" access="read"/>
    <method name="ClaimProximity"/>
    <method name="ReleaseProximity"/>
  </interface>
  <interface name="org.duo2os.FakeCall">
    <method name="SetCall"><arg type="s" direction="in"/></method>
    <method name="SetNear"><arg type="b" direction="in"/></method>
  </interface>
</node>
""" % {"VOICE": VOICE, "CALL": CALL, "SP": SP}


class Fake:
    def __init__(self, bus):
        self.bus = bus
        self.state = None  # None: no call
        self.near = False
        self.claims = set()
        info = Gio.DBusNodeInfo.new_for_xml(XML)
        self.ifaces = {i.name: i for i in info.interfaces}
        reg = bus.register_object
        reg(ROOT, self.ifaces["org.freedesktop.DBus.ObjectManager"], self.method, None, None)
        reg(MODEM, self.ifaces[VOICE], self.method, self.get, None)
        reg(CALLP, self.ifaces[CALL], self.method, self.get, None)
        reg(SP_PATH, self.ifaces[SP], self.method, self.get, None)
        reg("/fake", self.ifaces["org.duo2os.FakeCall"], self.method, None, None)
        bus.signal_subscribe(
            "org.freedesktop.DBus",
            "org.freedesktop.DBus",
            "NameOwnerChanged",
            "/org/freedesktop/DBus",
            None,
            Gio.DBusSignalFlags.NONE,
            self.owner,
        )

    def calls(self):
        return [CALLP] if self.state is not None else []

    def get(self, _c, _s, path, iface, prop):
        if iface == VOICE and prop == "Calls":
            return GLib.Variant("ao", self.calls())
        if iface == CALL and prop == "State":
            return GLib.Variant("i", self.state if self.state is not None else 0)
        if iface == SP and prop == "HasProximity":
            return GLib.Variant("b", True)
        if iface == SP and prop == "ProximityNear":
            return GLib.Variant("b", self.near)
        return None

    def method(self, _c, sender, path, iface, name, params, inv):
        if name == "GetManagedObjects":
            objs = {MODEM: {VOICE: {"Calls": GLib.Variant("ao", self.calls())}}}
            inv.return_value(GLib.Variant("(a{oa{sa{sv}}})", (objs,)))
        elif name == "ClaimProximity":
            self.claims.add(sender)
            print("sensor claimed by", sender, flush=True)
            inv.return_value(None)
        elif name == "ReleaseProximity":
            self.claims.discard(sender)
            print("sensor released by", sender, flush=True)
            inv.return_value(None)
        elif name == "SetCall":
            self.set_call(params.unpack()[0])
            inv.return_value(None)
        elif name == "SetNear":
            self.near = params.unpack()[0]
            print("near" if self.near else "far", "(claims: %d)" % len(self.claims), flush=True)
            self.changed(SP_PATH, SP, {"ProximityNear": GLib.Variant("b", self.near)})
            inv.return_value(None)
        else:
            inv.return_dbus_error("org.freedesktop.DBus.Error.UnknownMethod", name)

    def owner(self, _c, _s, _p, _i, _n, params):
        name, _old, new = params.unpack()
        if not new and name in self.claims:
            self.claims.discard(name)
            print("sensor claim of", name, "gone with it", flush=True)

    def set_call(self, word):
        old = self.state
        new = None if word == "none" else STATES[word]
        self.state = new
        print("call:", word, flush=True)
        if (old is None) != (new is None):
            self.changed(MODEM, VOICE, {"Calls": GLib.Variant("ao", self.calls())})
            self.bus.emit_signal(
                None,
                MODEM,
                VOICE,
                "CallAdded" if new is not None else "CallDeleted",
                GLib.Variant("(o)", (CALLP,)),
            )
        if new is not None:
            self.changed(CALLP, CALL, {"State": GLib.Variant("i", new)})
            self.bus.emit_signal(
                None, CALLP, CALL, "StateChanged", GLib.Variant("(iiu)", (old or 0, new, 0))
            )

    def changed(self, path, iface, props):
        self.bus.emit_signal(
            None, path, PROPS, "PropertiesChanged", GLib.Variant("(sa{sv}as)", (iface, props, []))
        )


def main():
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    fake = Fake(bus)
    for name in (MM, SP):
        Gio.bus_own_name_on_connection(
            bus, name, Gio.BusNameOwnerFlags.NONE, None, lambda _c, n: print("lost", n, flush=True)
        )
    print("fake call and sensor on the session bus; no call, far", flush=True)
    GLib.MainLoop().run()
    return fake


if __name__ == "__main__":
    main()
