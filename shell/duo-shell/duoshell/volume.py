"""The volume keys' rules (pure; tests/test_volume.py).

duo-wc never delivers the volume keys to a client: it announces them on its
event stream (`volume_key`: `up` / `down` / `mute`, `pressed` / `released`,
the panel for the display or none while the screen is dark). The shell
changes the default sink's volume with wpctl (PipeWire's software volume;
the hardware gains are fixed by the UCM profile) and shows it.

- A step is 5 % (wpctl's scale, the cubic one pactl shows too), on the
  5 % grid: from 27 % up goes to 30 %, down to 25 %.
- Never above 100 %: more would only clip in software.
- Down at the bottom mutes (volume 0 and muted); up unmutes, one step up.
- Mute (a plugged keyboard's key) toggles; unmuting at 0 % gives 5 %.
- Held: one step at once, the next after 400 ms, then every 100 ms, at most
  60 steps per press (a release the shell never got cannot run away).
"""

import math

STEP = 0.05
MAX = 1.0
REPEAT_DELAY_MS = 400
REPEAT_EVERY_MS = 100
REPEAT_MAX = 60
SINK = "@DEFAULT_AUDIO_SINK@"


def parse_get_volume(text):
    """`wpctl get-volume` output ("Volume: 0.30", "Volume: 0.30 [MUTED]") as
    (volume, muted), or None."""
    for line in (text or "").splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] == "Volume:":
            try:
                volume = float(parts[1])
            except ValueError:
                return None
            if math.isnan(volume) or volume < 0:
                return None
            return volume, "[MUTED]" in parts[2:]
    return None


def _grid(units):
    return round(min(MAX, max(0.0, units * STEP)), 2)


def step(volume, muted, key):
    """The (volume, muted) after one press of `key` (up, down, mute)."""
    units = volume / STEP
    if key == "up":
        return _grid(math.floor(units + 1e-6) + 1), False
    if key == "down":
        if muted:
            return _grid(math.ceil(units - 1e-6)), True
        new = _grid(math.ceil(units - 1e-6) - 1)
        return new, new <= 0.0
    if key == "mute":
        if muted:
            return (volume if volume > 0 else STEP), False
        return volume, True
    return volume, muted


def commands(before, after):
    """The wpctl calls that take the sink from `before` to `after`
    ((volume, muted) each)."""
    out = []
    if after[0] != before[0]:
        out.append(["wpctl", "set-volume", "-l", "%.1f" % MAX, SINK, "%.2f" % after[0]])
    if after[1] != before[1]:
        out.append(["wpctl", "set-mute", SINK, "1" if after[1] else "0"])
    return out


def icon_name(volume, muted):
    """The symbolic icon for a level."""
    if muted or volume <= 0:
        return "audio-volume-muted-symbolic"
    if volume < 0.34:
        return "audio-volume-low-symbolic"
    if volume < 0.67:
        return "audio-volume-medium-symbolic"
    return "audio-volume-high-symbolic"


def percent(volume):
    return int(round(volume * 100))
