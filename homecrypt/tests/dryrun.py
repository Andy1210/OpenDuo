"""The home's encryption end to end on a real ext4 with the kernel's fscrypt,
as root in a throwaway container (tests/dryrun.sh starts it): a loop image
mounted at /mnt/fs, a user `duo` (home: /mnt/fs/home/duo). On the phone,
tests/dryrun-phone.sh runs it in a private mount namespace instead, with
DRYRUN_TOP / DRYRUN_IMG / DRYRUN_MNT in place of the three paths.

Steps: enable (the lock side), the encryption at "boot" (the migration),
the files compared byte for byte with their metadata, a lock (a remount:
the kernel forgets the key) and the boot's tmpfs, the first unlock (settle),
a PIN change and the recovery key, two interrupted changes (a failed copy,
a failed clean-up after the exchange), the decryption. Throughput of the
encrypted and the plain directory on the way (this machine's CPU, not the
phone's). Exit 0 when every check held.
"""

import grp
import hashlib
import os
import pwd
import shutil
import stat
import subprocess
import sys
import time

TOP = os.environ.get("DRYRUN_TOP", "/src/homecrypt")
sys.path.insert(0, TOP)

import importlib.machinery  # noqa: E402
import importlib.util  # noqa: E402

from duohomecrypt import keys, lockside, migrate  # noqa: E402
from duohomecrypt import store as st  # noqa: E402

IMG = os.environ.get("DRYRUN_IMG", "/work/fs.img")
MNT = os.environ.get("DRYRUN_MNT", "/mnt/fs")
HOME = MNT + "/home/duo"
DEV = None
FAILS = []


def load_tool():
    loader = importlib.machinery.SourceFileLoader("tool", os.path.join(TOP, "duo2-home-crypt"))
    spec = importlib.util.spec_from_loader("tool", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


tool = load_tool()


def sh(*argv, check=True):
    p = subprocess.run(argv, capture_output=True, text=True)
    if check and p.returncode != 0:
        raise SystemExit("%s: %s" % (" ".join(argv), p.stderr.strip()))
    return p.stdout


def ok(cond, what):
    print(("  ok   " if cond else "  FAIL ") + what, flush=True)
    if not cond:
        FAILS.append(what)


class DrySystem(migrate.System):
    def __init__(self, fail_rsync=False):
        self.fail_rsync = fail_rsync
        self.messages = []

    def battery(self):
        return None

    def message(self, text, percent=None):
        self.messages.append(text)

    def run(self, argv, timeout=None, on_line=None):
        if self.fail_rsync and argv[0] == "rsync":
            # copy a part, then fail as a full disk would
            super().run(argv[:-2] + ["--max-size=1k"] + argv[-2:], timeout)
            return 11, "rsync: write failed: No space left on device (28)"
        return super().run(argv, timeout, on_line)


def manifest(root):
    """Every entry: type, mode, owner, size, content hash, link target,
    xattrs, which entries are hard links of each other."""
    out, inodes = {}, {}
    for d, dirs, files in os.walk(root):
        dirs.sort()
        for name in sorted(dirs + files):
            p = os.path.join(d, name)
            rel = os.path.relpath(p, root)
            s = os.lstat(p)
            entry = [stat.S_IFMT(s.st_mode), stat.S_IMODE(s.st_mode), s.st_uid, s.st_gid,
                     int(s.st_mtime)]  # fmt: skip
            if stat.S_ISREG(s.st_mode):
                h = hashlib.sha256()
                with open(p, "rb") as f:
                    for chunk in iter(lambda: f.read(1 << 20), b""):
                        h.update(chunk)
                entry += [s.st_size, h.hexdigest(), s.st_blocks * 512 < s.st_size]
                inodes.setdefault(s.st_ino, []).append(rel)
            elif stat.S_ISLNK(s.st_mode):
                entry.append(os.readlink(p))
            try:
                entry.append(
                    sorted(
                        (x, os.getxattr(p, x, follow_symlinks=False))
                        for x in os.listxattr(p, follow_symlinks=False)
                    )  # fmt: skip
                )
            except OSError:
                pass
            out[rel] = entry
    links = sorted(tuple(sorted(v)) for v in inodes.values() if len(v) > 1)
    return out, links


def populate():
    pw = pwd.getpwnam("duo")
    os.makedirs(HOME + "/Documents/deep/er", exist_ok=True)
    os.makedirs(HOME + "/.config/dconf", exist_ok=True)
    os.makedirs(HOME + "/.local/share/waydroid/data", exist_ok=True)
    with open(HOME + "/Documents/big.bin", "wb") as f:
        f.write(os.urandom(64 << 20))
    with open(HOME + "/Documents/notes.txt", "w") as f:
        f.write("Grüße 日本語\n" * 100)
    os.link(HOME + "/Documents/notes.txt", HOME + "/Documents/deep/hardlink.txt")
    os.symlink("../notes.txt", HOME + "/Documents/deep/er/link")
    os.symlink("/etc/hostname", HOME + "/abs-link")
    os.mkfifo(HOME + "/.local/share/fifo")
    with open(HOME + "/sparse", "wb") as f:
        f.truncate(512 << 20)
        f.seek(300 << 20)
        f.write(b"x" * 4096)
    long_name = "n" * 250
    with open(os.path.join(HOME, long_name), "w") as f:
        f.write("long")
    with open(HOME + "/.config/dconf/user", "wb") as f:
        f.write(os.urandom(4096))
    os.setxattr(HOME + "/Documents/notes.txt", "user.duo", b"value")
    os.chmod(HOME + "/Documents/notes.txt", 0o640)
    for d, dirs, files in os.walk(HOME):
        for n in dirs + files:
            os.chown(os.path.join(d, n), pw.pw_uid, pw.pw_gid, follow_symlinks=False)
    os.chown(HOME, pw.pw_uid, pw.pw_gid)
    os.chmod(HOME, 0o700)


def remount():
    """The kernel forgets every fscrypt key of the filesystem with it: a
    lock as after a restart."""
    sh("umount", MNT)
    sh("mount", "-o", "noatime", DEV, MNT)


def throughput(directory, label):
    path = os.path.join(directory, "speed.bin")
    data = os.urandom(8 << 20)
    t = time.perf_counter()
    with open(path, "wb") as f:
        for _ in range(64):
            f.write(data)
        f.flush()
        os.fsync(f.fileno())
    write = 512 / (time.perf_counter() - t)
    return path, write


def read_speed(path):
    t = time.perf_counter()
    with open(path, "rb") as f:
        while f.read(8 << 20):
            pass
    return 512 / (time.perf_counter() - t)


def main():
    global DEV
    paths = st.Paths(store="/var/lib/duo2-lock", run="/run/duo2-home", de="/var/lib/duo2-de")
    st.PATHS = paths
    sh("truncate", "-s", "3G", IMG)
    sh("mkfs.ext4", "-q", "-O", "encrypt,stable_inodes", "-L", "linux", IMG)
    DEV = sh("losetup", "-f", "--show", IMG).strip()
    os.makedirs(MNT, exist_ok=True)
    sh("mount", "-o", "noatime", DEV, MNT)
    try:
        run_steps(paths)
    finally:
        sh("umount", "-R", MNT, check=False)
        sh("losetup", "-d", DEV, check=False)
    print("\n%s" % ("all checks held" if not FAILS else "%d FAILED: %s" % (len(FAILS), FAILS)))
    return 1 if FAILS else 0


def run_steps(paths):
    os.makedirs(MNT + "/home", exist_ok=True)
    sh("useradd", "-l", "-M", "-d", HOME, "-s", "/bin/sh", "duo")
    os.makedirs(HOME)
    populate()
    before = manifest(HOME)
    print("home: %d entries, %d hard-link groups" % (len(before[0]), len(before[1])))

    print("\n== 1. enable (Settings > Lock screen > Encrypt my data)")
    t = time.perf_counter()
    digits = lockside.enable("duo", HOME, "2468", True, paths, check_home=False, supported=True)
    print("  enable took %.0f ms (scrypt N=2^15 twice: PIN and recovery key)"
          % ((time.perf_counter() - t) * 1000))  # fmt: skip
    ok(st.state(paths)["state"] == "encrypt-pending", "state encrypt-pending")
    ok(len(digits) == keys.RECOVERY_DIGITS, "a recovery key of %d digits" % keys.RECOVERY_DIGITS)

    print("\n== 2. the next start: the migration")
    system = DrySystem()
    t = time.perf_counter()
    tool.boot(paths, system)
    print("  encrypting took %.1f s; splash: %s" % (time.perf_counter() - t, system.messages[-3:]))
    s = st.state(paths)
    kf = migrate.key_file(paths)
    ident = bytes.fromhex(kf["identifier"])
    ok(s["state"] == "encrypted", "state encrypted (%s)" % s["detail"])
    ok(migrate.is_ours(HOME, ident), "the home has our policy: %r" % migrate.policy_of(HOME))
    ok(migrate.key_present(HOME, ident), "the key is in the kernel (its identifier = ours)")
    ok(manifest(HOME) == before, "every file, link, mode, owner, xattr, hard link the same")
    ok(not any(os.path.lexists(p) for p in migrate.side_paths(HOME)), "no copy left beside it")
    ok(not os.path.exists(paths.pending), "the raw key is gone from the disk")
    ok(not os.path.exists(paths.locked), "not locked (the key came with the migration)")
    print("  policy:", s["policy"])

    print("\n== 3. throughput (this machine, not the phone)")
    os.makedirs(MNT + "/plain", exist_ok=True)
    pe, we = throughput(HOME, "encrypted")
    pp, wp = throughput(MNT + "/plain", "plain")
    master = keys.parse_pending_key(keys.pending_key_text(lockside.opens("2468", paths)))
    remount()
    migrate.add_key(HOME, master, ident)
    re, rp = read_speed(pe), read_speed(pp)
    print("  write+fsync MB/s: encrypted %.0f, plain %.0f; cold read MB/s: encrypted %.0f, plain %.0f"
          % (we, wp, re, rp))  # fmt: skip
    os.unlink(pe)
    os.unlink(pp)
    before = manifest(HOME)

    print("\n== 4. a restart: the key is gone, the boot covers the home")
    remount()
    names = os.listdir(HOME)
    ok(".config" not in names and "Documents" not in names,
       "without the key the names are not readable (%d no-key names)" % len(names))  # fmt: skip
    try:
        with open(HOME + "/Documents/notes.txt"):
            pass
        ok(False, "a file opens without the key")
    except OSError:
        ok(True, "no file opens without the key")
    tool.boot(paths, DrySystem())
    ok(
        os.path.exists(paths.locked) and os.path.exists(paths.encrypted),
        "locked and encrypted markers",
    )
    ok(tool.tmpfs_at(HOME), "an empty tmpfs over the home")
    ok(os.listdir(HOME) == [] and os.stat(HOME).st_uid == pwd.getpwnam("duo").pw_uid,
       "the user's, empty")  # fmt: skip
    with open("/etc/dconf/profile/user") as f:
        prof = f.read()
    ok("file-db:/var/lib/duo2-de/alarms" in prof, "the dconf profile lists the alarms' copy")
    ok(os.path.exists(paths.alarms), "an (empty) compiled alarms database")
    with open(HOME + "/written-before-unlock", "w") as f:
        f.write("gone at the unlock")

    print("\n== 5. the first unlock (the right PIN, then settle)")
    t = time.perf_counter()
    opened = lockside.opens("2468", paths)
    print("  PIN check (scrypt unwrap) %.0f ms" % ((time.perf_counter() - t) * 1000))
    ok(
        opened is not None and lockside.opens("2469", paths) is None,
        "the PIN opens, a wrong one not",
    )
    ok(lockside.after_unlock(opened, paths, start=False), "the key handed to settle")
    tool.settle(paths)
    ok(not tool.tmpfs_at(HOME), "the tmpfs is gone")
    ok(not os.path.exists(paths.locked) and not os.path.exists(paths.unlock_key),
       "no locked marker, no key in /run")  # fmt: skip
    ok(manifest(HOME) == before, "the real home, unchanged")
    ok(not os.path.exists(HOME + "/written-before-unlock"), "what was written before is gone")

    print("\n== 6. a new PIN, the recovery key")
    lockside.rewrap(opened, "975310", paths)
    remount()
    ok(lockside.opens("2468", paths) is None, "the old PIN no longer opens")
    m2 = lockside.opens("975310", paths)
    ok(m2 == opened, "the new PIN opens the same key")
    ok(
        lockside.opens(keys.format_recovery_key(digits), paths) == opened,
        "so does the recovery key",
    )
    migrate.add_key(HOME, m2, ident)
    ok(manifest(HOME) == before, "the home reads the same")

    print("\n== 7a. a decryption whose copy fails: stays encrypted, nothing lost")
    lockside.disable(m2, paths)
    remount()  # a restart
    tool.boot(paths, DrySystem(fail_rsync=True))
    s = st.state(paths)
    ok(
        s["state"] == "encrypted" and "Not decrypted" in s["detail"],
        "encrypted again: %s" % s["detail"][:70],
    )
    ok(not os.path.exists(paths.pending), "the pending key is gone")
    ok(not any(os.path.lexists(p) for p in migrate.side_paths(HOME)), "the partial copy removed")
    ok(migrate.key_present(HOME, ident) and manifest(HOME) == before, "the home intact")

    print("\n== 7b. a decryption whose clean-up fails after the exchange")
    lockside.disable(m2, paths)
    remount()
    real = migrate.rmtree
    _new, old_copy = migrate.side_paths(HOME)

    def flaky(p):
        # the old encrypted copy, once it is there (after the exchange): an
        # I/O error for the rest of this start
        if p == old_copy and os.path.lexists(p):
            raise OSError(5, "I/O error (made up)", p)
        real(p)

    migrate.rmtree = flaky
    try:
        tool.boot(paths, DrySystem())
    finally:
        migrate.rmtree = real
    s = st.state(paths)
    ok(
        s["state"] == "plain" and s["detail"].startswith("Decrypted"),
        "plain: %s" % s["detail"][:70],
    )
    ok(migrate.policy_of(HOME) is None and manifest(HOME) == before, "the home decrypted, the same")
    left = [p for p in migrate.side_paths(HOME) if os.path.lexists(p)]
    ok(len(left) == 1, "the encrypted copy left behind: %s" % left)
    ok(not os.path.exists(paths.key_file), "no key file")
    tool.boot(paths, DrySystem())
    ok(not any(os.path.lexists(p) for p in migrate.side_paths(HOME)), "the next start removed it")
    ok(not os.path.exists("/etc/dconf/profile/user"), "our dconf profile is gone")

    print("\n== 8. encrypt again, an encryption whose copy fails: plain, nothing lost")
    lockside.enable("duo", HOME, "975310", False, paths, check_home=False, supported=True)
    tool.boot(paths, DrySystem(fail_rsync=True))
    s = st.state(paths)
    ok(s["state"] == "plain" and "Not encrypted" in s["detail"], "plain: %s" % s["detail"][:70])
    ok(
        os.path.exists(paths.key_file) and not os.path.exists(paths.pending),
        "no pending key; the key file stays (it checks the PIN)",
    )
    ok(lockside.opens("975310", paths) is not None, "the PIN still opens it")
    ok(migrate.policy_of(HOME) is None and manifest(HOME) == before, "the home untouched")
    ok(not any(os.path.lexists(p) for p in migrate.side_paths(HOME)), "no copy left")

    print("\n== 9. encrypt, then decrypt all the way")
    lockside.enable("duo", HOME, "975310", False, paths, check_home=False, supported=True)
    tool.boot(paths, DrySystem())
    kf = migrate.key_file(paths)
    ok(st.state(paths)["state"] == "encrypted" and manifest(HOME) == before, "encrypted, the same")
    m3 = lockside.opens("975310", paths)
    lockside.disable(m3, paths)
    remount()
    tool.boot(paths, DrySystem())
    ok(st.state(paths)["state"] == "plain", "plain")
    ok(migrate.policy_of(HOME) is None and manifest(HOME) == before, "decrypted, the same")
    ok(not os.path.exists(paths.key_file) and not os.path.exists(paths.pending), "no key file left")
    ok(grp.getgrgid(os.stat(HOME).st_gid).gr_name == "duo", "the home is still the user's")
    print("  free space after: %.2f GB of %.2f GB" % (
        shutil.disk_usage(MNT).free / 1e9, shutil.disk_usage(MNT).total / 1e9))  # fmt: skip


if __name__ == "__main__":
    sys.exit(main())
