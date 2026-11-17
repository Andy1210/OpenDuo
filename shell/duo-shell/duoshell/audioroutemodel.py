"""Where sound goes when an output comes (pure; tests/test_audioroutemodel.py).

A phone's rule: a Bluetooth or USB audio output that connects becomes the
output, and what plays moves to it; when it goes, sound goes back to where
it was (the speaker). WirePlumber 0.5.8 does the second half by itself: it
keeps every output the user (or this module) chose as a stack in
~/.local/state/wireplumber/default-nodes and falls back down that stack
when the chosen one disappears. It has no setting for the first half: an
output it has never been told about only counts by its priority, below any
chosen one that is still there, so a headset never became the output while
the speaker had once been chosen. audioroute.py watches the outputs
(pipewire-pulse's event stream) and makes a newly connected one the default
(`pactl set-default-sink`: WirePlumber moves the streams that follow the
default, and stacks the headset over the speaker).

- Only an output that APPEARS while the shell runs: what is there when the
  shell starts stays as it is (a restart of the shell changes nothing), and
  the user's own choice in Settings > Sound stands until the next arrival.
- Bluetooth (device.api bluez5) and USB (device.bus usb) outputs only:
  never a virtual sink (filter-chain, a null sink) or the phone's own card.
- Not during a call: duo2-callaudio makes the call's sink (the Voice Call
  profile's, which the volume keys then move) the default while a call
  runs, and a call cannot reach a Bluetooth output. An output that arrives
  then waits, and becomes the output when the call has ended.
"""

KINDS = ("bluetooth", "usb")


def kind(sink):
    """'bluetooth' or 'usb' for an output of that kind (an entry of
    `pactl -f json list sinks`), else None."""
    props = (sink or {}).get("properties") or {}
    if props.get("device.api") == "bluez5" or props.get("device.bus") == "bluetooth":
        return "bluetooth"
    if props.get("device.bus") == "usb":
        return "usb"
    return None


def in_call(default):
    """The default sink is the call's (duo2-callaudio's Voice Call sinks)."""
    return "Voice_Call" in (default or "")


class Router:
    """Fed every look at the outputs; says which one to make the default."""

    def __init__(self):
        self.known = None  # the sink names seen at the last look
        self.waiting = []  # arrivals not applied yet (a call), oldest first
        self.kinds = {}

    def update(self, sinks, default):
        """`sinks`: `pactl -f json list sinks`; `default`: the default sink's
        name. Returns (name, kind) to make the default, or None."""
        names = [s.get("name") for s in sinks or [] if isinstance(s, dict) and s.get("name")]
        if self.known is None:
            self.known = set(names)
            return None
        for s in sinks or []:
            if not isinstance(s, dict):
                continue
            name = s.get("name")
            k = kind(s)
            if name and k and name not in self.known:
                if name in self.waiting:
                    self.waiting.remove(name)
                self.waiting.append(name)
                self.kinds[name] = k
        self.known = set(names)
        self.waiting = [n for n in self.waiting if n in self.known]
        self.kinds = {n: k for n, k in self.kinds.items() if n in self.waiting}
        if not self.waiting or in_call(default):
            return None
        name = self.waiting[-1]  # the newest arrival wins
        k = self.kinds.get(name)
        self.waiting = []
        self.kinds = {}
        return None if name == default else (name, k)
