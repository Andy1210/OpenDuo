"""The developer tools (devmode/README.md "Developer tools"): bring-up and
debugging programs that are installed only while Developer mode is on.

devtools.list (root's) names them by the path they are installed at, one
per line. Their copies live in the payload, PAYLOAD/files/PATH (the payload
is root's, 0700: no other user can read or run them there). sync(on) makes
what is installed follow the switch:

  - a regular file at a listed path is the newest install of that tool (a
    component's installer put it there): it is copied into the payload;
  - on: every listed tool in the payload is installed at its path, with the
    payload copy's mode;
  - off: every listed path that holds a regular file is removed. A link or
    a directory there is someone else's and stays.

duo-devmode runs it when the switch changes and at every boot
(duo-devmode-sync.service), so a component reinstalled while Developer
mode is off loses its tools again at the next start. Nothing outside the
list is touched, and nothing is followed through a link.
"""

import os
import stat

LIST = "/usr/local/lib/duo-devmode/devtools.list"
PAYLOAD = "/usr/local/lib/openduo-devtools"
ALLOWED = ("/usr/local/bin/", "/usr/local/sbin/", "/usr/local/lib/")
MAX_SIZE = 16 << 20
SAFE = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._+-/")


def valid_path(path):
    """A listed path: absolute, normalised, plain characters, under one of
    ALLOWED, not in the payload itself."""
    return (
        isinstance(path, str)
        and 0 < len(path) < 256
        and set(path) <= SAFE
        and os.path.normpath(path) == path
        and path.startswith(ALLOWED)
        and not path.endswith("/")
        and "/." not in path
        and not (path + "/").startswith(PAYLOAD + "/")
    )


def parse_list(text):
    """devtools.list -> the paths, in order, each once (comments after #,
    invalid lines dropped)."""
    out = []
    for line in (text or "").splitlines():
        path = line.split("#", 1)[0].strip()
        if valid_path(path) and path not in out:
            out.append(path)
    return out


def _under(root, path):
    return os.path.join(root, path.lstrip("/"))


def _regular(path):
    """lstat if `path` is a regular file (not a link), else None."""
    try:
        st = os.lstat(path)
    except OSError:
        return None
    return st if stat.S_ISREG(st.st_mode) else None


def _read(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    with os.fdopen(fd, "rb") as f:
        return f.read(MAX_SIZE + 1)


def _makedirs(root, path, mode):
    """Make the directories of `path` under `root`, none of them a link."""
    parts = [p for p in os.path.dirname(path).split("/") if p]
    d = root
    for p in parts:
        d = os.path.join(d, p)
        try:
            os.mkdir(d, mode)
        except FileExistsError:
            pass
        if os.path.islink(d) or not os.path.isdir(d):
            raise OSError("%s is not a directory" % d)


def _write(path, data, mode):
    """Atomically, never through a link: a temporary file next to it, then
    a rename over it."""
    d, base = os.path.split(path)
    tmp = os.path.join(d, ".%s.devtools.%d" % (base, os.getpid()))
    try:
        os.unlink(tmp)
    except FileNotFoundError:
        pass
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        os.write(fd, data)
        os.fchmod(fd, mode)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, path)


def sync(on, root="/", list_text=None, list_path=LIST, payload=PAYLOAD):
    """Make the installed tools follow `on` (see the module's text). `root`
    is "/" on the phone, a test directory in the tests. -> {"adopted",
    "installed", "removed", "missing"}: lists of paths."""
    if list_text is None:
        try:
            with open(_under(root, list_path)) as f:
                list_text = f.read()
        except OSError:
            list_text = ""
    paths = parse_list(list_text)
    store = _under(root, payload)
    files = os.path.join(store, "files")
    result = {"adopted": [], "installed": [], "removed": [], "missing": []}
    if paths:
        os.makedirs(files, 0o700, exist_ok=True)
        if os.path.islink(store) or os.path.islink(files):
            raise OSError("the payload is a link")
        os.chmod(store, 0o700)
    for path in paths:
        dest = _under(root, path)
        copy = os.path.join(files, path.lstrip("/"))
        st = _regular(dest)
        if st is not None and st.st_size <= MAX_SIZE:
            data = _read(dest)
            have = _regular(copy)
            if (
                have is None
                or _read(copy) != data
                or stat.S_IMODE(have.st_mode) != stat.S_IMODE(st.st_mode)
            ):
                _makedirs(store, os.path.join("files", path.lstrip("/")), 0o700)
                _write(copy, data, stat.S_IMODE(st.st_mode))
                result["adopted"].append(path)
        have = _regular(copy)
        if on:
            if have is None:
                result["missing"].append(path)
                continue
            if st is None:
                if os.path.lexists(dest):
                    continue  # a link or a directory: not ours
                _makedirs(root, path, 0o755)
                _write(dest, _read(copy), stat.S_IMODE(have.st_mode))
                result["installed"].append(path)
        elif st is not None:
            os.unlink(dest)
            result["removed"].append(path)
    return result


def state(root="/", list_text=None, list_path=LIST, payload=PAYLOAD):
    """{"installed": how many listed tools are at their paths, "listed":
    how many are listed, "available": how many the payload has}."""
    if list_text is None:
        try:
            with open(_under(root, list_path)) as f:
                list_text = f.read()
        except OSError:
            list_text = ""
    paths = parse_list(list_text)
    files = os.path.join(_under(root, payload), "files")
    return {
        "installed": sum(1 for p in paths if _regular(_under(root, p)) is not None),
        "listed": len(paths),
        "available": sum(
            1 for p in paths if _regular(os.path.join(files, p.lstrip("/"))) is not None
        ),
    }
