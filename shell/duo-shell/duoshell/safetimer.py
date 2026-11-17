"""Periodic GLib callbacks that survive an exception.

PyGObject removes a timeout source whose callback raises, so one bad read
would stop the clock or the battery icon for good. `keep_running` logs the
exception and tells GLib to keep the source (True). Pure Python, no GLib.
"""

import functools
import sys
import traceback


def keep_running(fn):
    """Wrap a periodic callback: an exception is printed and the callback
    answers True (run again) instead of ending the timer."""

    @functools.wraps(fn)
    def run(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception:  # noqa: BLE001 - whatever failed, the timer must live on
            traceback.print_exc(file=sys.stderr)
            return True

    return run


def or_default(fn, default=None):
    """Wrap a worker-thread function: an exception is printed and the call
    answers `default`, so the code waiting for the result still runs."""

    @functools.wraps(fn)
    def run(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception:  # noqa: BLE001 - the caller must get an answer whatever failed
            traceback.print_exc(file=sys.stderr)
            return default

    return run
