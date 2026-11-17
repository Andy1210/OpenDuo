#!/usr/bin/python3
"""A fake ModemManager on the SESSION bus, for trying the shell's SIM PIN
dialog, the bar's signal and the data tile, and Duo Settings' SIM lock,
without a SIM (duoshell/mobile.py and duo-settings' sim.py use the session
bus while root's /etc/duo2/shell-fake-modem exists).

    fake_mm.py [pin|puk|registered]     the state it starts in

One modem with a SIM: the PIN is 1234, the PUK 12345678 (test values of
this fake, nothing real). Started `pin` or `puk` its PIN lock is on and it
waits; `registered`: the lock is off. Three wrong PINs (SendPin, EnablePin
or ChangePin alike, as on a real SIM) and it wants the PUK; the right one
(or the PUK with a new PIN) unlocks it, and two seconds later it is
registered on "Example SIM" (26201, LTE, 72 %). EnablePin switches the lock
(Modem3gpp.EnabledFacilityLocks, the "sim" bit), ChangePin the PIN (only
with the lock on, as SIMs do). It speaks only what the ModemManager GI's
proxies read: the object manager, Modem (State, UnlockRequired,
UnlockRetries, SignalQuality, AccessTechnologies, Sim, StateFailedReason),
Modem3gpp (RegistrationState, OperatorCode, OperatorName,
EnabledFacilityLocks), Simple, Sim (SendPin, SendPuk, EnablePin, ChangePin,
OperatorName, Active). It prints the states it goes through, never what was
typed.
"""

import sys

from gi.repository import Gio, GLib

BUS_NAME = "org.freedesktop.ModemManager1"
ROOT = "/org/freedesktop/ModemManager1"
MODEM = ROOT + "/Modem/0"
SIM = ROOT + "/SIM/0"
MI = "org.freedesktop.ModemManager1.Modem"
M3 = MI + ".Modem3gpp"
MS = MI + ".Simple"
SI = "org.freedesktop.ModemManager1.Sim"
ERR = "org.freedesktop.ModemManager1.Error.MobileEquipment.IncorrectPassword"

# ModemManager's numbers (MMModemState, MMModemLock, ...).
LOCKED, DISABLED, REGISTERED = 2, 3, 8
LOCK_NONE, LOCK_PIN, LOCK_PUK = 1, 2, 4
REG_HOME = 1
TECH_LTE = 1 << 14
FACILITY_SIM = 1 << 0
TEST_PIN, TEST_PUK = "1234", "12345678"

XML = """
<node>
  <interface name="org.freedesktop.DBus.ObjectManager">
    <method name="GetManagedObjects">
      <arg type="a{oa{sa{sv}}}" name="objects" direction="out"/>
    </method>
    <signal name="InterfacesAdded"><arg type="o"/><arg type="a{sa{sv}}"/></signal>
    <signal name="InterfacesRemoved"><arg type="o"/><arg type="as"/></signal>
  </interface>
  <interface name="%(MI)s">
    <property name="State" type="i" access="read"/>
    <property name="StateFailedReason" type="u" access="read"/>
    <property name="UnlockRequired" type="u" access="read"/>
    <property name="UnlockRetries" type="a{uu}" access="read"/>
    <property name="SignalQuality" type="(ub)" access="read"/>
    <property name="AccessTechnologies" type="u" access="read"/>
    <property name="Sim" type="o" access="read"/>
  </interface>
  <interface name="%(M3)s">
    <property name="RegistrationState" type="u" access="read"/>
    <property name="OperatorCode" type="s" access="read"/>
    <property name="OperatorName" type="s" access="read"/>
    <property name="EnabledFacilityLocks" type="u" access="read"/>
  </interface>
  <interface name="%(MS)s">
    <method name="Disconnect"><arg type="o" name="bearer" direction="in"/></method>
  </interface>
  <interface name="%(SI)s">
    <method name="SendPin"><arg type="s" name="pin" direction="in"/></method>
    <method name="SendPuk"><arg type="s" name="puk" direction="in"/><arg type="s" name="pin" direction="in"/></method>
    <method name="EnablePin"><arg type="s" name="pin" direction="in"/><arg type="b" name="enabled" direction="in"/></method>
    <method name="ChangePin"><arg type="s" name="old" direction="in"/><arg type="s" name="new" direction="in"/></method>
    <property name="OperatorName" type="s" access="read"/>
    <property name="Active" type="b" access="read"/>
  </interface>
</node>
""" % {"MI": MI, "M3": M3, "MS": MS, "SI": SI}


class Fake:
    def __init__(self, start):
        self.info = Gio.DBusNodeInfo.new_for_xml(XML)
        self.pin_left, self.puk_left = 3, 10
        self.state, self.lock = LOCKED, LOCK_PIN
        self.registered = False
        self.pin = TEST_PIN
        self.sim_lock = True
        if start == "puk":
            self.pin_left, self.lock = 0, LOCK_PUK
        elif start == "registered":
            self.state, self.lock, self.registered = REGISTERED, LOCK_NONE, True
            self.sim_lock = False
        self.bus = None

    # -- properties -------------------------------------------------------------------

    def props(self, iface):
        if iface == MI:
            return {
                "State": GLib.Variant("i", self.state),
                "StateFailedReason": GLib.Variant("u", 0),
                "UnlockRequired": GLib.Variant("u", self.lock),
                "UnlockRetries": GLib.Variant(
                    "a{uu}", {LOCK_PIN: self.pin_left, LOCK_PUK: self.puk_left}
                ),
                "SignalQuality": GLib.Variant("(ub)", (72 if self.registered else 0, True)),
                "AccessTechnologies": GLib.Variant("u", TECH_LTE if self.registered else 0),
                "Sim": GLib.Variant("o", SIM),
            }
        if iface == M3:
            return {
                "RegistrationState": GLib.Variant("u", REG_HOME if self.registered else 0),
                "OperatorCode": GLib.Variant("s", "26201" if self.registered else ""),
                "OperatorName": GLib.Variant("s", "Telekom.de" if self.registered else ""),
                "EnabledFacilityLocks": GLib.Variant("u", FACILITY_SIM if self.sim_lock else 0),
            }
        if iface == SI:
            return {
                "OperatorName": GLib.Variant("s", "Example SIM"),
                "Active": GLib.Variant("b", True),
            }
        return {}

    def changed(self, path, iface):
        self.bus.emit_signal(
            None,
            path,
            "org.freedesktop.DBus.Properties",
            "PropertiesChanged",
            GLib.Variant("(sa{sv}as)", (iface, self.props(iface), [])),
        )

    def say(self):
        print(
            "fake_mm: state %d, lock %d, PIN attempts %d, PUK attempts %d, registered %s, "
            "SIM lock %s"
            % (
                self.state,
                self.lock,
                self.pin_left,
                self.puk_left,
                self.registered,
                "on" if self.sim_lock else "off",
            ),
            flush=True,
        )

    # -- D-Bus ---------------------------------------------------------------------------

    def method(self, conn, sender, path, iface, name, params, inv):
        if iface == "org.freedesktop.DBus.ObjectManager" and name == "GetManagedObjects":
            objs = {MODEM: {i: self.props(i) for i in (MI, M3)}}
            objs[MODEM][MS] = {}
            inv.return_value(GLib.Variant("(a{oa{sa{sv}}})", (objs,)))
            return
        if iface == SI and name == "SendPin":
            pin = params.unpack()[0]
            if self.lock != LOCK_PIN:
                inv.return_dbus_error(
                    "org.freedesktop.ModemManager1.Error.Core.WrongState", "No PIN asked"
                )
            elif pin == self.pin:
                inv.return_value(None)
                self.unlocked()
            else:
                self.wrong(inv)
            return
        if iface == SI and name in ("EnablePin", "ChangePin"):
            first, second = params.unpack()
            if self.lock != LOCK_NONE:
                inv.return_dbus_error(
                    "org.freedesktop.ModemManager1.Error.Core.WrongState", "The SIM is locked"
                )
            elif name == "ChangePin" and not self.sim_lock:
                inv.return_dbus_error(
                    "org.freedesktop.ModemManager1.Error.MobileEquipment.SimFailure",
                    "The SIM PIN is not enabled",
                )
            elif first != self.pin:
                self.wrong(inv)
            else:
                self.pin_left = 3
                if name == "EnablePin":
                    self.sim_lock = bool(second)
                else:
                    self.pin = second
                print("fake_mm: %s done" % name, flush=True)
                inv.return_value(None)
                self.changed(MODEM, MI)
                self.changed(MODEM, M3)
                self.say()
            return
        if iface == SI and name == "SendPuk":
            puk, _new = params.unpack()
            if self.lock != LOCK_PUK:
                inv.return_dbus_error(
                    "org.freedesktop.ModemManager1.Error.Core.WrongState", "No PUK asked"
                )
            elif puk == TEST_PUK:
                self.pin_left = 3
                self.pin = _new
                inv.return_value(None)
                self.unlocked()
            else:
                self.puk_left -= 1
                self.changed(MODEM, MI)
                self.say()
                inv.return_dbus_error(ERR, "Incorrect password")
            return
        if iface == MS and name == "Disconnect":
            inv.return_value(None)
            return
        inv.return_dbus_error("org.freedesktop.DBus.Error.UnknownMethod", name)

    def wrong(self, inv):
        """A wrong PIN (SendPin, EnablePin, ChangePin): one attempt less;
        the third blocks the SIM (PUK), as on a real card."""
        self.pin_left -= 1
        if self.pin_left <= 0:
            self.pin_left = 0
            self.lock, self.state, self.registered, self.sim_lock = LOCK_PUK, LOCKED, False, True
            self.changed(MODEM, M3)
        self.changed(MODEM, MI)
        self.say()
        inv.return_dbus_error(ERR, "Incorrect password")

    def unlocked(self):
        self.state, self.lock = DISABLED, LOCK_NONE
        self.changed(MODEM, MI)
        self.say()

        def register():
            self.state, self.registered = REGISTERED, True
            self.changed(MODEM, MI)
            self.changed(MODEM, M3)
            self.say()
            return False

        GLib.timeout_add(2000, register)

    def get_prop(self, conn, sender, path, iface, name):
        return self.props(iface).get(name)

    def acquired(self, conn, _name):
        self.bus = conn
        node = self.info
        conn.register_object(
            ROOT,
            node.lookup_interface("org.freedesktop.DBus.ObjectManager"),
            self.method,
            None,
            None,
        )
        for iface in (MI, M3, MS):
            conn.register_object(
                MODEM, node.lookup_interface(iface), self.method, self.get_prop, None
            )
        conn.register_object(SIM, node.lookup_interface(SI), self.method, self.get_prop, None)

    def named(self, _conn, _name):
        print("fake_mm: on the session bus as %s" % BUS_NAME, flush=True)
        self.say()


def main(argv):
    fake = Fake(argv[0] if argv else "pin")
    Gio.bus_own_name(
        Gio.BusType.SESSION,
        BUS_NAME,
        Gio.BusNameOwnerFlags.NONE,
        fake.acquired,
        fake.named,
        lambda *_a: (print("fake_mm: name lost"), sys.exit(1)),
    )
    loop = GLib.MainLoop()
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, 15, loop.quit)
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, 2, loop.quit)
    loop.run()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
