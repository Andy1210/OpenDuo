"""Encrypting or decrypting the home at the next start (duo2-home-crypt boot,
before anything of the user runs), with its progress on the boot splash.

Crash-safe: the home in use is never written. A copy is made next to it
(HOME's parent/.NAME.openduo-new: encrypted when encrypting, plain when
decrypting) with rsync, which a new start resumes; then the two directories
change places in one step (renameat2 RENAME_EXCHANGE); then the old one is
deleted and the free space trimmed. The state file says where it is; a
start in the middle of it looks at the directories themselves (is HOME
encrypted with our key yet?) and goes on from there.

Before it copies: enough free space for a second home (its size plus 5 %,
at least 512 MiB more), and a battery at 30 % or a charger; otherwise the
change waits for a later start (the state stays "pending", the reason in
the state file's detail).
"""

import ctypes
import ctypes.util
import errno
import os
import re
import shutil
import subprocess
import time

from . import fscrypt, keys
from . import store as st

PATH = "/usr/sbin:/usr/bin:/sbin:/bin"
MIN_BATTERY = 30
SPARE_MIN = 512 << 20
RENAME_EXCHANGE = 2
AT_FDCWD = -100


class Postponed(Exception):
    """Not now (space, battery): the change waits for a later start."""


class Failed(Exception):
    """The change cannot be made: back to how it was, the reason kept."""


# -- the system (a test stands in for it) ---------------------------------------------


class System:
    def run(self, argv, timeout=None, on_line=None):
        """(exit code, output); on_line(text) gets each line (or rsync's
        carriage-return-separated progress) while it runs."""
        try:
            p = subprocess.Popen(
                argv,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env={"PATH": PATH, "LANG": "C.UTF-8"},
                stdin=subprocess.DEVNULL,
            )
        except OSError as e:
            return -1, str(e)
        out, buf = [], b""
        while True:
            chunk = p.stdout.read1(4096) if hasattr(p.stdout, "read1") else p.stdout.read(4096)
            if not chunk:
                break
            buf += chunk
            parts = re.split(rb"[\r\n]", buf)
            buf = parts.pop()
            for part in parts:
                text = part.decode("utf-8", "replace")
                if on_line and text.strip():
                    on_line(text)
                out.append(text)
        if buf:
            out.append(buf.decode("utf-8", "replace"))
        rc = p.wait(timeout=timeout)
        return rc, "\n".join(out[-50:])

    def message(self, text, percent=None):
        """The boot splash's message line and progress bar (rootfs/plymouth:
        duo2.script); nothing without a splash."""
        try:
            if subprocess.call(["plymouth", "--ping"], stderr=subprocess.DEVNULL) != 0:
                return
            subprocess.call(["plymouth", "display-message", "--text=" + text])
            if percent is not None:
                subprocess.call(["plymouth", "system-update", "--progress=%d" % percent])
        except OSError:
            pass

    def battery(self):
        """(lowest charge in %, a charger is on) or None without a battery."""
        base = "/sys/class/power_supply"
        charges, charger = [], False
        try:
            names = os.listdir(base)
        except OSError:
            return None
        for n in names:
            d = os.path.join(base, n)
            kind = st.read(os.path.join(d, "type")) or ""
            if kind.strip() == "Battery":
                try:
                    charges.append(int((st.read(os.path.join(d, "capacity")) or "").strip()))
                except ValueError:
                    pass
                if (st.read(os.path.join(d, "status")) or "").strip() in ("Charging", "Full"):
                    charger = True
            elif (st.read(os.path.join(d, "online")) or "").strip() == "1":
                charger = True
        return (min(charges), charger) if charges else None

    def free_bytes(self, path):
        s = os.statvfs(path)
        return s.f_bavail * s.f_frsize

    def used_bytes(self, path):
        rc, out = self.run(["du", "-sxb", "--", path])
        if rc != 0:
            raise Failed("could not measure the home: %s" % out.strip()[-200:])
        return int(out.split()[0])

    def features(self, path):
        """The ext4 features of the filesystem `path` is on (dumpe2fs on its
        block device, found from the file's st_dev)."""
        dev = block_device(os.stat(path).st_dev)
        if dev is None:
            return set()
        try:
            out = subprocess.run(
                ["dumpe2fs", "-h", dev],
                capture_output=True,
                text=True,
                timeout=60,
                env={"PATH": PATH, "LANG": "C.UTF-8"},
            ).stdout
        except (OSError, subprocess.SubprocessError):
            return set()
        m = re.search(r"^Filesystem features:\s*(.*)$", out, re.M)
        return set(m.group(1).split()) if m else set()

    def trim(self, path):
        self.run(["fstrim", path])


# -- small parts ------------------------------------------------------------------------


def block_device(st_dev, sysfs="/sys/dev/block"):
    """/dev/NAME of a device number (its uevent's DEVNAME), or None."""
    text = st.read(os.path.join(sysfs, "%d:%d" % (os.major(st_dev), os.minor(st_dev)), "uevent"))
    for line in (text or "").splitlines():
        if line.startswith("DEVNAME="):
            name = line.split("=", 1)[1].strip()
            if name and "/" not in name.replace("mapper/", "") and ".." not in name:
                return "/dev/" + name
    return None


def side_paths(home):
    parent, name = os.path.split(home.rstrip("/"))
    return os.path.join(parent, ".%s.openduo-new" % name), os.path.join(
        parent, ".%s.openduo-old" % name
    )


def space_needed(used):
    return used + max(SPARE_MIN, used // 20)


def check_power(battery):
    """None when it may go on, else the reason to wait."""
    if battery is None:
        return None
    charge, charger = battery
    if charger or charge >= MIN_BATTERY:
        return None
    return "the battery is at %d %%: plug in a charger (or charge it to %d %%)" % (
        charge,
        MIN_BATTERY,
    )


RSYNC_PROGRESS = re.compile(r"^\s*[\d,.]+\s+(\d{1,3})%")


def rsync_percent(line):
    m = RSYNC_PROGRESS.match(line)
    return int(m.group(1)) if m else None


def exchange(a, b):
    """Swap two directories in one step (renameat2 RENAME_EXCHANGE)."""
    libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
    fn = libc.renameat2
    fn.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    if fn(AT_FDCWD, a.encode(), AT_FDCWD, b.encode(), RENAME_EXCHANGE) != 0:
        e = ctypes.get_errno()
        raise OSError(e, os.strerror(e), a)


def policy_of(path):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError:
        return None
    try:
        return fscrypt.get_policy(fd)
    finally:
        os.close(fd)


def is_ours(path, identifier):
    p = policy_of(path)
    return p is not None and p.identifier == identifier


def fs_fd(home):
    return os.open(os.path.dirname(home.rstrip("/")) or "/", os.O_RDONLY | os.O_DIRECTORY)


def add_key(home, master, identifier):
    fd = fs_fd(home)
    try:
        got = fscrypt.add_key(fd, master)
    finally:
        os.close(fd)
    if got != identifier:
        raise Failed("the kernel's key identifier differs from the key file's")


def remove_key(home, identifier):
    fd = fs_fd(home)
    try:
        return fscrypt.remove_key(fd, identifier)
    finally:
        os.close(fd)


def key_present(home, identifier):
    fd = fs_fd(home)
    try:
        return fscrypt.key_status(fd, identifier) == "present"
    except OSError:
        return False
    finally:
        os.close(fd)


def ensure_features(system, home, policy):
    """The filesystem's `encrypt` feature (and `stable_inodes` for the
    IV_INO_LBLK_64 policy), set through the kernel when missing."""
    need_stable = "IV_INO_LBLK_64" in policy.get("flags", [])
    # the parent: the home itself may be a mount point (the locked tmpfs)
    have = system.features(os.path.dirname(home.rstrip("/")) or "/")
    if "encrypt" in have and (not need_stable or "stable_inodes" in have):
        return
    fd = fs_fd(home)
    try:
        fscrypt.set_ext4_features(fd, encrypt=True, stable_inodes=need_stable)
    except OSError as e:
        raise Failed(
            "the filesystem cannot take encryption (%s): its encrypt%s feature is off"
            % (e.strerror or e, " and stable_inodes" if need_stable else "")
        ) from e
    finally:
        os.close(fd)


def make_encrypted_dir(path, identifier, policy, uid, gid):
    """An empty encrypted directory; HCTR2 for the names when the kernel has
    it, else AES-256-CTS (the policy dict is updated to what it got)."""
    flags = fscrypt.flags_value(policy.get("flags", ["PAD_32"]))
    for names in (policy.get("filenames", "AES-256-HCTR2"), "AES-256-CTS"):
        os.mkdir(path, 0o700)
        os.chown(path, uid, gid)
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            fscrypt.set_policy(fd, identifier, policy.get("contents", "AES-256-XTS"), names, flags)
            probe = os.open(
                ".openduo-probe", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=fd
            )
            os.close(probe)
            os.unlink(".openduo-probe", dir_fd=fd)
            policy["filenames"] = names
            return
        except OSError as e:
            if names == "AES-256-CTS" or e.errno not in (errno.EINVAL, errno.ENOPKG, errno.ENOENT):
                raise
        finally:
            os.close(fd)
        shutil.rmtree(path)


def rmtree(path):
    if os.path.lexists(path):
        shutil.rmtree(path)


# -- the two ways -------------------------------------------------------------------------


class Migration:
    def __init__(self, system=None, paths=None, now=time.monotonic):
        paths = paths or st.PATHS
        self.sys = system or System()
        self.paths = paths
        self.now = now
        self.last = None

    def progress(self, state, what, percent):
        s = st.state(self.paths)
        s["state"], s["progress"] = state, percent
        if self.last is None or percent != self.last[1] or what != self.last[0]:
            st.save_state(s, self.paths)
            self.sys.message("%s: %d %%" % (what, percent), percent)
            self.last = (what, percent)

    def copy(self, src, dst, state, what):
        def line(text):
            p = rsync_percent(text)
            if p is not None:
                self.progress(state, what, min(p, 99))

        argv = ["rsync", "-aHAXS", "--numeric-ids", "--delete", "--no-inc-recursive",
                "--info=progress2", src.rstrip("/") + "/", dst.rstrip("/") + "/"]  # fmt: skip
        rc, out = self.sys.run(argv, on_line=line)
        if rc != 0:
            raise Failed("copying the home failed (rsync %d): %s" % (rc, out.strip()[-300:]))

    def preconditions(self, home, copying):
        why = check_power(self.sys.battery())
        if why:
            raise Postponed(why)
        if copying:
            need = space_needed(self.sys.used_bytes(home))
            free = self.sys.free_bytes(os.path.dirname(home.rstrip("/")))
            if free < need:
                raise Failed(
                    "not enough free space: %.1f GB needed, %.1f GB free" % (need / 1e9, free / 1e9)
                )

    def encrypt(self, kf, master, uid, gid):
        home = kf["home"]
        ident = bytes.fromhex(kf["identifier"])
        new, old = side_paths(home)
        swapped = is_ours(home, ident)
        self.preconditions(home, copying=not swapped)
        ensure_features(self.sys, home, kf["policy"])
        if not key_present(home, ident):
            add_key(home, master, ident)
        if not swapped:
            rmtree(old)
            if os.path.lexists(new) and not is_ours(new, ident):
                rmtree(new)  # a copy from another attempt, or a plain one
            if not os.path.lexists(new):
                make_encrypted_dir(new, ident, kf["policy"], uid, gid)
            self.progress("encrypting", "Encrypting your data", 0)
            self.copy(home, new, "encrypting", "Encrypting your data")
            os.sync()
            exchange(home, new)
            os.rename(new, old)
            os.sync()
        self.progress("encrypting", "Removing the unencrypted copy", 99)
        rmtree(old)
        rmtree(new)
        self.sys.trim(os.path.dirname(home.rstrip("/")) or "/")
        return kf

    def decrypt(self, kf, master, uid, gid):
        home = kf["home"]
        ident = bytes.fromhex(kf["identifier"])
        new, old = side_paths(home)
        # The home was the encrypted one: plain now, the two have changed places.
        swapped = policy_of(home) is None
        self.preconditions(home, copying=not swapped)
        if not key_present(home, ident):
            add_key(home, master, ident)
        if not swapped:
            rmtree(old)
            if os.path.lexists(new) and policy_of(new) is not None:
                rmtree(new)
            if not os.path.lexists(new):
                os.mkdir(new, 0o700)
                os.chown(new, uid, gid)
            self.progress("decrypting", "Decrypting your data", 0)
            self.copy(home, new, "decrypting", "Decrypting your data")
            os.sync()
            exchange(home, new)
            os.rename(new, old)
            os.sync()
        self.progress("decrypting", "Removing the encrypted copy", 99)
        rmtree(old)
        rmtree(new)
        try:
            remove_key(home, ident)
        except OSError:
            pass
        self.sys.trim(os.path.dirname(home.rstrip("/")) or "/")
        return kf


def key_file(paths=None):
    paths = paths or st.PATHS
    text = st.read(paths.key_file)
    if text is None:
        return None
    return keys.parse_key_file(text)
