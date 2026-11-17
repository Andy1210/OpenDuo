"""Periodic GLib callbacks that survive an exception.

PyGObject removes a timeout source whose callback raises, so one bad call
would stop a page following its state for good. `keep_running` logs the
exception and tells GLib to keep the source (True). `keep_running_after`
wraps a call whose own result is not the answer (a poll that returns
nothing): it runs it, then answers True. Pure Python, no GLib.
(The same idea as duo-shell's duoshell/safetimer.py.)
"""

import functools
import sys
import traceback


def keep_running_after(fn):
    """Wrap a periodic callback whose return value does not matter: it is
    called, an exception is printed, and the answer is always True (run again)."""

    @functools.wraps(fn)
    def run(*args, **kwargs):
        try:
            fn(*args, **kwargs)
        except Exception:  # noqa: BLE001 - whatever failed, the timer must live on
            traceback.print_exc(file=sys.stderr)
        return True

    return run


def keep_running(fn):
    """Wrap a periodic callback: its answer passes through, an exception is
    printed and the answer is True (run again) instead of ending the timer."""

    @functools.wraps(fn)
    def run(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception:  # noqa: BLE001
            traceback.print_exc(file=sys.stderr)
            return True

    return run


def guarded(fn):
    """Wrap a worker-thread function: an exception is printed and swallowed,
    so the thread ends quietly instead of with a traceback and half a job."""

    @functools.wraps(fn)
    def run(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception:  # noqa: BLE001
            traceback.print_exc(file=sys.stderr)
            return None

    return run
