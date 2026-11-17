"""What the lock screen's PIN door (shell/duo-shell/duo-lock-helper, root)
does for the home's encryption (homecrypt/README.md). The helper hands the
PIN on to these; they never print a key or a hash.

    opens(text)            the master key if `text` is the PIN or the recovery
                           key, else None (the key file decides: with the home
                           encrypted there is no other copy of the PIN)
    after_unlock(master)   the right PIN on a locked home: the key goes to
                           /run/duo2-home/unlock.key and duo2-home-settle starts
    rewrap(master, pin)    a new PIN protects the same key
    enable(user, home, pin, recovery)
                           a key, wrapped by the PIN (and a recovery key), and
                           the home's encryption at the next start
    disable(master)        the home's decryption at the next start
    cancel(master)         a change that waits for the next start is dropped
    new_recovery(master)   a new recovery key (the old one stops working)
"""

import os
import pwd
import subprocess

from . import fscrypt, keys, migrate
from . import store as st

SETTLE_UNIT = "duo2-home-settle.service"
PATH = "/usr/sbin:/usr/bin:/sbin:/bin"


class Refused(Exception):
    """Not possible now (the message says why, for the user)."""


def key_file(paths=None):
    paths = paths or st.PATHS
    try:
        return migrate.key_file(paths)
    except keys.BadKeyFile:
        return None


def encrypted(paths=None):
    """There is a key file: the PIN is checked against it (encrypted, a
    change waiting for the next start, or a try that failed: the pin file
    keeps no hash then)."""
    paths = paths or st.PATHS
    return os.path.exists(paths.key_file)


def opens(text, paths=None):
    paths = paths or st.PATHS
    kf = key_file(paths)
    if kf is None:
        return None
    master, _kind = keys.open_with(kf, text)
    return master


def after_unlock(master, paths=None, start=True):
    """The right PIN: if the home waits for its key, hand the key to the
    first unlock's step (it restarts the session on the real home)."""
    paths = paths or st.PATHS
    if not os.path.exists(paths.locked):
        return False
    st.write(paths.unlock_key, keys.pending_key_text(master), 0o600)
    if start:
        subprocess.call(
            ["systemctl", "--no-ask-password", "start", "--no-block", SETTLE_UNIT],
            env={"PATH": PATH},
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    return True


def rewrap(master, pin, paths=None):
    paths = paths or st.PATHS
    kf = key_file(paths)
    if kf is None:
        return
    kf = keys.protect(kf, master, "pin", pin)
    st.write(paths.key_file, keys.format_key_file(kf), 0o600)


def check_user_home(user, home):
    """The home to encrypt is that user's own directory under /home, not a
    link, not a mount point."""
    try:
        pw = pwd.getpwnam(user)
    except KeyError as e:
        raise Refused("no such user") from e
    if pw.pw_uid < 1000 or os.path.normpath(pw.pw_dir) != home or not home.startswith("/home/"):
        raise Refused("only a person's home under /home can be encrypted")
    if os.path.islink(home) or not os.path.isdir(home) or os.path.ismount(home):
        raise Refused("the home is not a plain directory")
    if os.stat(home).st_uid != pw.pw_uid:
        raise Refused("the home is not its user's")
    return pw


def enable(user, home, pin, recovery, paths=None, check_home=True, supported=None):
    """Prepare the encryption for the next start. -> the recovery key's
    digits, or None without one."""
    paths = paths or st.PATHS
    s = st.state(paths)
    if s["state"] != "plain":
        raise Refused("the home is already encrypted, or a change waits for the next start")
    # A key file with the home plain is a failed try's: it only checks the
    # PIN until this one replaces it.
    if supported is None:
        supported = fscrypt.fs_supports_encryption()
    if not supported:
        raise Refused("this system's kernel cannot encrypt files")
    if check_home:
        check_user_home(user, home)
    master = keys.new_master_key()
    kf = keys.new_key_file(user, home, keys.key_identifier(master), keys.POLICY_DEFAULT)
    kf["policy"] = dict(kf["policy"], flags=["PAD_32", "IV_INO_LBLK_64"])
    kf = keys.protect(kf, master, "pin", pin)
    digits = None
    if recovery:
        digits = keys.new_recovery_key()
        kf = keys.protect(kf, master, "recovery", digits)
    os.makedirs(paths.store, mode=0o755, exist_ok=True)
    st.write(paths.pending, keys.pending_key_text(master), 0o600)
    st.write(paths.key_file, keys.format_key_file(kf), 0o600)
    s.update(user=user, home=home, state="encrypt-pending", progress=0, detail="",
             recovery="yes" if recovery else "no", policy="")  # fmt: skip
    st.save_state(s, paths)
    return digits


def disable(master, paths=None):
    paths = paths or st.PATHS
    s = st.state(paths)
    if s["state"] != "encrypted":
        raise Refused("the home is not encrypted, or a change waits for the next start")
    st.write(paths.pending, keys.pending_key_text(master), 0o600)
    s.update(state="decrypt-pending", progress=0, detail="")
    st.save_state(s, paths)


def cancel(master, paths=None):
    """A change that has not started yet is dropped: the home stays as it is."""
    paths = paths or st.PATHS
    s = st.state(paths)
    if s["state"] == "encrypt-pending":
        # The key file stays: it checks the PIN until the helper has written
        # the PIN's hash again (forget() then).
        st.remove(paths.pending)
        s.update(state="plain", detail="", recovery="no", progress=0)
    elif s["state"] == "decrypt-pending":
        st.remove(paths.pending)
        s.update(state="encrypted", detail="", progress=100)
    else:
        raise Refused("nothing waits for the next start")
    st.save_state(s, paths)


def forget(paths=None):
    """The key file goes, with the home plain (the PIN's hash is in the pin
    file again, or there is no PIN)."""
    paths = paths or st.PATHS
    if st.state(paths)["state"] != "plain":
        raise Refused("the home is encrypted, or a change waits for the next start")
    st.remove(paths.key_file)


def new_recovery(master, paths=None):
    paths = paths or st.PATHS
    kf = key_file(paths)
    if kf is None:
        raise Refused("the home is not encrypted")
    digits = keys.new_recovery_key()
    kf = keys.protect(kf, master, "recovery", digits)
    st.write(paths.key_file, keys.format_key_file(kf), 0o600)
    s = st.state(paths)
    s["recovery"] = "yes"
    st.save_state(s, paths)
    return digits
