"""The Android clipboard bridge's rules (pure): which text type to read,
which callers may use it (duo-clipboard-bridge).

Waydroid's Android asks its host for the clipboard over binder
(lineageos.waydroid.IClipboard: getClipboardData, sendClipboardData). The
binder devices are reachable by any host process of the user, so the bridge
answers only callers inside the Android container: their cgroup is the
container's (/lxc.payload.waydroid*, at the top of the hierarchy), which a
host process cannot move itself into (it is root's; the user's delegated
subtree is under /user.slice, whatever it names its cgroups).
"""

TEXT_TYPES = ("text/plain;charset=utf-8", "UTF8_STRING", "text/plain", "TEXT", "STRING")
OFFER_TYPES = ("text/plain;charset=utf-8", "text/plain", "UTF8_STRING", "TEXT", "STRING")
MAX_BYTES = 1 << 20  # 1 MiB of text: more is not a clipboard text


def pick_text_type(mime_types):
    """The best text type an offer has, or None (an image, files...)."""
    offered = list(mime_types or [])
    for want in TEXT_TYPES:
        if want in offered:
            return want
    for mime in offered:
        if mime.startswith("text/plain"):
            return mime
    return None


def in_container(cgroup_text):
    """Whether /proc/PID/cgroup says the process is in Waydroid's
    container."""
    for line in (cgroup_text or "").splitlines():
        path = line.split(":", 2)[-1]
        # At the top of the hierarchy (root's): the user's own delegated
        # subtree (user@UID.service/...) may name a cgroup anything.
        if path.startswith("/lxc.payload.waydroid"):
            return True
    return False


def decode(data):
    return data[:MAX_BYTES].decode("utf-8", errors="replace")
