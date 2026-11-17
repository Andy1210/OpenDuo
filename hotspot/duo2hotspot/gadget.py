"""USB tethering: a second CDC-NCM function in the phone's one USB gadget
(tests/test_gadget.py, on a directory standing in for configfs).

The gadget (`/sys/kernel/config/usb_gadget/duo2`) is made by the rescue
initrd (or duo2-usb-gadget): CDC-ACM (the serial console, ttyGS0) and CDC-NCM
(usb0, the development network 10.15.19.1/24). Tethering adds
`functions/ncm.tether` (network device tether0) AFTER those two, so their
interface numbers, addresses and the host's names for them stay as they
are. A function can only be added to an unbound gadget: the gadget is
unbound for the change and bound again at once (the host sees the phone
unplugged and plugged in again for about a second, as at every suspend,
power/usb-gadget.sleep; usb0 itself stays with its address). CDC-NCM works
with Linux, macOS and Windows 11 (Windows 10 2004 and later have the class
driver too); RNDIS (older Windows) is not offered.
"""

import hashlib
import os

GADGET = "/sys/kernel/config/usb_gadget/duo2"
FUNCTION = "ncm.tether"
IFNAME = "tether%d"


def macs(machine_id):
    """(dev_addr, host_addr): stable, locally administered, from the
    machine id (a host keeps one network profile for the phone)."""
    h = hashlib.sha256((machine_id or "").strip().encode() + b"\0duo2-hotspot usb").digest()

    def mac(b, tweak):
        first = (b[0] & 0xFC) | 0x02  # unicast, locally administered
        return ":".join("%02x" % x for x in [first, b[1], b[2], b[3], b[4], b[5] ^ tweak])

    return mac(h, 0x00), mac(h, 0x01)


class GadgetError(Exception):
    pass


class Gadget:
    def __init__(self, root=GADGET, function=FUNCTION, rmdir=os.rmdir):
        """rmdir: configfs removes a function's attributes with its
        directory (tests stand in with shutil.rmtree)."""
        self.root = root
        self.function = function
        self.rmdir = rmdir

    def _read(self, *parts):
        try:
            with open(os.path.join(self.root, *parts)) as f:
                return f.read().strip()
        except OSError:
            return None

    def _write(self, value, *parts):
        with open(os.path.join(self.root, *parts), "w") as f:
            f.write(value + "\n")

    def present(self):
        return os.path.isdir(self.root)

    def udc(self):
        return self._read("UDC") or ""

    def config(self):
        try:
            names = sorted(os.listdir(os.path.join(self.root, "configs")))
        except OSError:
            names = []
        if not names:
            raise GadgetError("the gadget has no configuration")
        return names[0]

    def link_path(self):
        return os.path.join(self.root, "configs", self.config(), self.function)

    def active(self):
        """Whether the tethering function is in the gadget's configuration."""
        try:
            return os.path.islink(self.link_path())
        except GadgetError:
            return False

    def _rebind(self, change, udc):
        """Unbind, change(), bind again; the bind always happens (the
        development link must come back), a failed change is undone first by
        the caller's change itself."""
        self._write("", "UDC")
        try:
            change()
        finally:
            self._write(udc, "UDC")

    def add(self, dev_addr, host_addr, ifname=IFNAME):
        if not self.present():
            raise GadgetError("no USB gadget (%s)" % self.root)
        udc = self.udc()
        if not udc:
            raise GadgetError("the USB gadget is not bound to the controller")
        if self.active():
            return False
        fdir = os.path.join(self.root, "functions", self.function)
        made = False
        if not os.path.isdir(fdir):
            os.mkdir(fdir)
            made = True
            # Before the first bind only (the device is registered then).
            for attr, value in (
                ("ifname", ifname),
                ("dev_addr", dev_addr),
                ("host_addr", host_addr),
            ):
                try:
                    self._write(value, "functions", self.function, attr)
                except OSError:
                    pass  # an older kernel without `ifname`: usbN
        link = self.link_path()

        def change():
            os.symlink(fdir, link)

        try:
            self._rebind(change, udc)
        except OSError as e:
            if made:
                try:
                    self.rmdir(fdir)
                except OSError:
                    pass
            raise GadgetError("adding the USB tethering function failed: %s" % e) from e
        return True

    def remove(self):
        if not self.present():
            return False
        fdir = os.path.join(self.root, "functions", self.function)
        link = None
        try:
            link = self.link_path()
        except GadgetError:
            pass
        if link and os.path.islink(link):
            udc = self.udc()
            if udc:
                self._rebind(lambda: os.unlink(link), udc)
            else:
                os.unlink(link)
        if os.path.isdir(fdir):
            try:
                self.rmdir(fdir)
            except OSError as e:
                raise GadgetError("removing the USB tethering function failed: %s" % e) from e
        return True

    def netdev(self):
        """The function's network device name (tether0), once bound."""
        return self._read("functions", self.function, "ifname")


def ssh_over(ss_out, local_addrs):
    """Whether `ss -Htn state established` shows an ssh session whose phone
    side is one of these addresses (the USB development network)."""
    for line in (ss_out or "").splitlines():
        words = line.split()
        # Recv-Q Send-Q Local:Port Peer:Port (state omitted by the filter)
        locs = [w for w in words if ":" in w]
        if len(locs) < 2:
            continue
        local = locs[0]
        host, _, port = local.rpartition(":")
        host = host.strip("[]")
        if port == "22" and host in local_addrs:
            return True
    return False
