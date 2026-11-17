"""The session's trust (docs/hardware/security.md, duo-wc's src/trust.rs).

duo-wc hands its session a *trust factory*: one end of a SOCK_SEQPACKET
socketpair, on the descriptor named by DUO_WC_TRUST_FD. A packet `control` or
`wayland` on it comes back as `ok` with a fresh trusted connection attached
(SCM_RIGHTS): a control connection on which every request is allowed, or a
Wayland connection that sees the guarded globals (session lock, layer shell,
...). The session starts the shell with it (`duoctl exec-trusted
--keep-trust`); apps never get it:

- `take()` (at start) remembers the descriptor, makes it close-on-exec (no
  child inherits it: the apps the grid starts) and drops the variable from
  the environment;
- `control_socket()` is what wc.py connects with, `wayland_fd()` what the
  lock is started with (WAYLAND_SOCKET).

Without a factory (the shell started by hand) everything falls back to the
public socket: the state works, the privileged requests are refused.

`make_undumpable()`: there is no Yama in this kernel, so any process of the
same user could ptrace the shell and use its descriptors; a non-dumpable
process cannot be attached to (nor its memory read) by anyone but root.
"""

import ctypes
import os
import socket

ENV = "DUO_WC_TRUST_FD"
PR_SET_DUMPABLE = 4

_fd = None


def parse_fd(value):
    """The descriptor number in DUO_WC_TRUST_FD, or None (pure)."""
    value = (value or "").strip()
    if not value.isdigit():
        return None
    fd = int(value)
    return fd if 3 <= fd < 65536 else None


def take(environ=None):
    """Adopt the factory named in the environment (once, at start)."""
    global _fd
    environ = os.environ if environ is None else environ
    fd = parse_fd(environ.pop(ENV, None))
    if fd is None:
        return None
    try:
        os.fstat(fd)
        os.set_inheritable(fd, False)
    except OSError:
        return None
    _fd = fd
    return fd


def available():
    return _fd is not None


def ask(kind, timeout=3.0):
    """A new trusted descriptor from the factory: kind 'control' or 'wayland'.
    Raises OSError when there is no factory or it refuses."""
    if _fd is None:
        raise OSError("no trust factory (not started by the session)")
    sock = socket.socket(fileno=os.dup(_fd))
    try:
        sock.settimeout(timeout)
        sock.send(kind.encode())
        msg, fds, _flags, _addr = socket.recv_fds(sock, 256, 1)
    finally:
        sock.close()
    if msg != b"ok" or not fds:
        for extra in fds:
            os.close(extra)
        raise OSError("trust factory: %s" % (msg.decode(errors="replace") or "gone"))
    return fds[0]


def control_socket(timeout=2.0):
    """A trusted control connection (a connected socket object)."""
    sock = socket.socket(fileno=ask("control", timeout))
    sock.settimeout(timeout)
    return sock


def wayland_fd():
    """A trusted Wayland connection's descriptor, close-on-exec; pass it to a
    child with pass_fds and WAYLAND_SOCKET."""
    return ask("wayland")


def child_env(fd, environ=None):
    """The environment for a child that gets `fd` as its Wayland connection:
    WAYLAND_SOCKET set, no factory (pure)."""
    env = dict(os.environ if environ is None else environ)
    env.pop(ENV, None)
    env["WAYLAND_SOCKET"] = str(fd)
    return env


def make_undumpable():
    """prctl(PR_SET_DUMPABLE, 0); False if it could not be done."""
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        return libc.prctl(PR_SET_DUMPABLE, 0, 0, 0, 0) == 0
    except (OSError, AttributeError):
        return False


def forget_wayland_socket(environ=None):
    """libwayland unsets WAYLAND_SOCKET in the C environment when it takes the
    descriptor, but Python's os.environ keeps its copy: a child given that
    environment would try a descriptor it does not have and fail to connect.
    Called once the display is open."""
    (os.environ if environ is None else environ).pop("WAYLAND_SOCKET", None)
