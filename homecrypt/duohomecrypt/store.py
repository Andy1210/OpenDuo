"""Where the home's encryption keeps its files (homecrypt/README.md), and the
public state file's text. Root's side; the pure parts are tested.

    /var/lib/duo2-lock/          root 0755 (the lock's store: duoshell/lockstore.py)
        home.key                 root 0600: the key file (keys.py): the master key
                                 wrapped by the PIN and the recovery key
        home.pending             root 0600: the raw master key while a change of the
                                 home waits for the next start (encrypt / decrypt)
        home.state               root 0644: the state, no secret (Settings, the shell)
    /run/duo2-home/              root 0755, gone after a restart
        encrypted                the home is encrypted (user units' conditions)
        locked                   ... and its key is not in the kernel yet: the user
                                 sees an empty home on a tmpfs until the first unlock
        unlock.key               root 0600: the key, from the right PIN's check to
                                 the first unlock's step (duo2-home-settle.service)
"""

import os

STATES = (
    "plain",
    "encrypt-pending",
    "encrypting",
    "encrypted",
    "decrypt-pending",
    "decrypting",
)
# What the next start does in each state.
MIGRATING = {"encrypt-pending": "encrypt", "encrypting": "encrypt", "decrypt-pending": "decrypt",
             "decrypting": "decrypt"}  # fmt: skip
STATE_KEYS = ("user", "home", "state", "progress", "detail", "recovery", "policy")


class Paths:
    def __init__(self, store="/var/lib/duo2-lock", run="/run/duo2-home", de="/var/lib/duo2-de"):
        self.store = store
        self.key_file = os.path.join(store, "home.key")
        self.pending = os.path.join(store, "home.pending")
        self.state_file = os.path.join(store, "home.state")
        self.run = run
        self.encrypted = os.path.join(run, "encrypted")
        self.locked = os.path.join(run, "locked")
        self.unlock_key = os.path.join(run, "unlock.key")
        self.de = de
        self.alarms = os.path.join(de, "alarms")


PATHS = Paths()


def parse_state(text):
    """The state file -> dict (defaults: plain, nothing else)."""
    st = {"state": "plain", "progress": 0, "detail": "", "recovery": "no", "user": "", "home": "",
          "policy": ""}  # fmt: skip
    for line in (text or "").splitlines():
        if line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = (p.strip() for p in line.split("=", 1))
        if key == "state" and value in STATES:
            st[key] = value
        elif key == "progress":
            try:
                st[key] = max(0, min(100, int(value)))
            except ValueError:
                pass
        elif key == "recovery" and value in ("yes", "no"):
            st[key] = value
        elif key in ("user", "home", "detail", "policy"):
            st[key] = value.replace("\n", " ")[:300]
    return st


def format_state(st):
    lines = ["# duo2-home-crypt: the home's encryption (homecrypt/README.md); no secret"]
    for key in STATE_KEYS:
        value = st.get(key, "")
        lines.append("%s = %s" % (key, str(value).replace("\n", " ")[:300]))
    return "\n".join(lines) + "\n"


def read(path, limit=1 << 20):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError:
        return None
    with os.fdopen(fd) as f:
        return f.read(limit)


def write(path, text, mode):
    """Atomically, root's, never through a link (a temporary file next to it,
    fsync'd, renamed over it)."""
    d, base = os.path.split(path)
    tmp = os.path.join(d, ".%s.%d.tmp" % (base, os.getpid()))
    try:
        os.unlink(tmp)
    except FileNotFoundError:
        pass
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, mode)
    try:
        os.write(fd, text.encode() if isinstance(text, str) else text)
        os.fchmod(fd, mode)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, path)
    dfd = os.open(d, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)


def remove(path):
    """Overwrite a secret file with zeros before it goes (the flash may keep
    the old blocks anyway: the README says so), then unlink it."""
    try:
        fd = os.open(path, os.O_WRONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except FileNotFoundError:
        return
    except OSError:
        fd = None
    if fd is not None:
        try:
            size = os.fstat(fd).st_size
            os.write(fd, bytes(size))
            os.fsync(fd)
        finally:
            os.close(fd)
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass


def state(paths=None):
    paths = paths or PATHS
    return parse_state(read(paths.state_file))


def save_state(st, paths=None):
    paths = paths or PATHS
    os.makedirs(paths.store, mode=0o755, exist_ok=True)
    write(paths.state_file, format_state(st), 0o644)


def touch(path, mode=0o644):
    os.makedirs(os.path.dirname(path), mode=0o755, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, mode)
    os.close(fd)


def untouch(path):
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
