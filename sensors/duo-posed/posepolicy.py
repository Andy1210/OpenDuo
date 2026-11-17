# SPDX-License-Identifier: GPL-3.0-or-later
"""duo-posed's decisions, pure (no sensors, no sockets): unit-tested by
test_posepolicy.py on any machine.

Conventions
- Accelerometer readings are in m/s^2 in the frame of the panel the sensor
  sits behind, Android style: x to the panel's right, y to its top, z out of
  its face; at rest the sensor reads +g along the axis pointing UP.
- Rotations are duo-wc's (`duoctl set_rotation`): 90 = the picture is drawn
  for the device turned 90 degrees clockwise (the panel's left edge up), so
  the left edge (-x) up -> 90, the right edge (+x) up -> 270, upside down -> 180.
- Poses are duo-wc's: "dual", "single DSI-1", "single DSI-2", "span", plus
  "blank" (both panels off: the phone is closed) and None (no opinion: keep).
"""

import math

G = 9.80665
ROTATIONS = (0, 90, 180, 270)
# centre of each rotation as the angle atan2(x, y) of the up direction
_CENTRE = {0: 0.0, 270: 90.0, 180: 180.0, 90: -90.0}


def _angdiff(a, b):
    d = (a - b + 180.0) % 360.0 - 180.0
    return abs(d)


def tilt(acc):
    """How far the panel stands up: |in-plane gravity| / |g|, 0 lying flat .. 1 upright."""
    x, y, z = acc
    n = math.sqrt(x * x + y * y + z * z)
    if n < 0.3 * G:
        return 0.0  # free fall or garbage: no opinion
    return math.sqrt(x * x + y * y) / n


def rotation_candidate(acc, current, min_tilt=0.45, hysteresis_deg=15.0):
    """The rotation this reading asks for, or None (keep the current one).

    A panel lying (nearly) flat says nothing. Otherwise the up direction's angle
    in the panel plane picks the nearest quarter turn; to LEAVE the current
    rotation the angle must come within (45 - hysteresis) degrees of another
    quarter, so a phone held at 45 degrees does not flap."""
    if acc is None or tilt(acc) < min_tilt:
        return None
    x, y, _ = acc
    ang = math.degrees(math.atan2(x, y))
    best = min(ROTATIONS, key=lambda r: _angdiff(ang, _CENTRE[r]))
    if current is None or best == current:
        return best
    if _angdiff(ang, _CENTRE[best]) <= 45.0 - hysteresis_deg:
        return best
    return current


class Debounce:
    """A value must stay the same for `settle` seconds before it is taken."""

    def __init__(self, settle):
        self.settle = settle
        self.value = None  # the accepted value
        self.pending = None
        self.since = 0.0

    def feed(self, value, now):
        """Returns the accepted value when it changes on this call, else None."""
        if value is None or value == self.value:
            self.pending = None
            return None
        if value != self.pending:
            self.pending = value
            self.since = now
        if now - self.since >= self.settle:
            self.value = value
            self.pending = None
            return value
        return None


# Microsoft's posture classifier on the SLPI (sns_posture, msg 7788).
# "left"/"right": the panel the posture is about (which one faces the user).
# Verified with the owner by hand (2026-11-07) for flat, closed, folded back
# (both sides), book and peek. Tent is different: turned around on the table
# the phone lies the same way against gravity, so the posture (tent_right both
# ways) cannot know which panel faces the user: "single active" keeps the
# panel in use.
POSTURE_POSE = {
    "closed": "blank",
    "peek_right": None,
    "peek_left": None,  # 5-75 degrees: keep
    "read": "dual",
    "compose": "dual",  # concave (book, laptop)
    "open_dpm": "dual",
    "open_dlm": "dual",  # flat, portrait / landscape
    "brochure_right": "single active",
    "brochure_left": "single active",
    "tent_right": "single active",
    "tent_left": "single active",
    "flip_p_right": "single right",
    "flip_l_right": "single right",
    "flip_p_left": "single left",
    "flip_l_left": "single left",
    "ramp_right": "single right",
    "ramp_left": "single left",
    "unknown": None,
    "undetermined": None,
}


def pose_from_posture(posture, outputs, overrides=None):
    """posture name -> duo-wc pose (or "blank" / None). `outputs` maps
    "left"/"right" (and "active", the panel in use, when known) to the
    connector names (DSI-1 / DSI-2); "single active" without an "active"
    entry keeps the current pose (None)."""
    table = dict(POSTURE_POSE)
    if overrides:
        table.update(overrides)
    p = table.get(posture)
    if p is None or p in ("blank", "dual", "span", "keep"):
        return None if p == "keep" else p
    if p.startswith("single "):
        side = p.split()[1]
        if side == "active" and not outputs.get("active"):
            return None
        return "single " + outputs.get(side, side)
    return p


class HingeClassifier:
    """Hinge angle (degrees, 0 closed .. 180 flat .. 360 folded back) -> pose
    class, with hysteresis: a band is left only `margin` degrees past its edge.

    Classes: "closed", "peek", "open" (concave + flat: dual), "convex" (tent /
    brochure: one panel), "flipped" (folded back: one panel)."""

    BANDS = (
        ("closed", 0.0, 12.0),
        ("peek", 12.0, 40.0),
        ("open", 40.0, 215.0),
        ("convex", 215.0, 330.0),
        ("flipped", 330.0, 361.0),
    )

    def __init__(self, margin=6.0, bands=None):
        self.margin = margin
        self.bands = bands or self.BANDS
        self.current = None

    def classify(self, angle):
        if angle is None:
            return self.current
        if self.current is not None:
            for name, lo, hi in self.bands:
                if name == self.current and lo - self.margin <= angle < hi + self.margin:
                    return self.current
        for name, lo, hi in self.bands:
            if lo <= angle < hi:
                self.current = name
                return name
        return self.current


def pose_from_hinge(cls, front):
    """Hinge class -> pose; `front` is the connector to keep in the one-panel
    poses (the panel facing the user, e.g. the one touched last)."""
    return {
        "closed": "blank",
        "peek": None,
        "open": "dual",
        "convex": "single " + front if front else None,
        "flipped": "single " + front if front else None,
    }.get(cls)


def hinge_from_accels(acc_left, acc_right, min_proj=0.5):
    """The hinge angle (degrees: 0 closed, 180 flat, 360 folded back) from the
    two halves' gravity readings, or None when they cannot tell.

    The hinge runs along each panel's y axis, on the LEFT panel's right edge
    (+x) and the RIGHT panel's left edge. Opening by `a` turns the right
    panel's frame about y, so gravity's angle in the x-z plane differs by
    180 - hinge between the halves. That plane holds the information only
    while gravity is not along the hinge: with the hinge (nearly) vertical, a
    book held upright, both projections vanish and the angle is unknown (the
    SLPI's own hinge sensor, when it works, also uses the gyros and magnets).
    Swapping which accelerometer is "left" turns every angle a into 360 - a
    (closed <-> folded back)."""
    if acc_left is None or acc_right is None:
        return None
    for a in (acc_left, acc_right):
        if math.hypot(a[0], a[2]) < min_proj * G:
            return None
    al = math.degrees(math.atan2(acc_left[0], acc_left[2]))
    ar = math.degrees(math.atan2(acc_right[0], acc_right[2]))
    alpha = (ar - al + 180.0) % 360.0 - 180.0
    return (180.0 - alpha) % 360.0


class Smooth:
    """Exponential smoothing of a 3-vector (the accelerometers deliver 26 Hz)."""

    def __init__(self, k=0.3):
        self.k = k
        self.v = None

    def feed(self, v):
        if self.v is None:
            self.v = tuple(v)
        else:
            self.v = tuple(p + self.k * (n - p) for p, n in zip(self.v, v))
        return self.v

    def reset(self):
        self.v = None


def facing_output(acc_by_output, fallback):
    """Of two panels, the one whose face points more upward (larger +z), when
    the difference is clear; else `fallback`. Only a hint: held vertically,
    gravity cannot tell which side the user is on."""
    zs = {o: a[2] for o, a in acc_by_output.items() if a is not None}
    if len(zs) < 2:
        return fallback
    (o1, z1), (o2, z2) = sorted(zs.items(), key=lambda kv: -kv[1])
    if z1 - z2 > 0.6 * G:
        return o1
    return fallback


def rotation_source(pose, acc_by_output):
    """Which panel's accelerometer decides the rotation in a pose: the panel
    itself in "single X"; in dual/span the more upright of the two (the other
    may lie flat as a keyboard)."""
    if pose and pose.startswith("single "):
        return pose.split()[1]
    best, best_t = None, -1.0
    for o, a in sorted(acc_by_output.items()):
        if a is None:
            continue
        t = tilt(a)
        if t > best_t:
            best, best_t = o, t
    return best


# duo2-touchd (the touch daemon) is told what Android tells the stock touch
# HAL (vendor.surface.touchpen): which panels are lit, the hinge angle, the
# display orientation and the handedness. Microsoft's heat processor picks
# its geometry from them: both panels lit and the hinge under 235 degrees ->
# one stitched surface; one panel lit -> that panel alone.
TOUCH_HINGE_STEP = 1.0  # degrees; the daemon applies the stock service's own steps


def touch_display(lit, blanked, left, right, posture=None):
    """duo-wc's lit outputs -> "both" / "left" / "right", or None while the
    screen is dark or nothing is lit (the daemon then keeps the last state:
    a touch must still wake the panels).

    Standing as a tent or a brochure one panel is dark, but a tap on it must
    still arrive (duo-wc then switches to it: the posture cannot tell which
    panel faces the user), so the heat processor is told both are on: told
    "one panel" it reports no contacts from the other."""
    if blanked or not lit:
        return None
    if posture and posture.startswith(("tent", "brochure")):
        return "both"
    on_l, on_r = left in lit, right in lit
    if on_l and on_r:
        return "both"
    if on_l:
        return "left"
    if on_r:
        return "right"
    return None


def android_rotation(wc_rotation):
    """duo-wc's rotation -> Android's display orientation in degrees (what the
    stock HAL gets, setDisplayOrientation). duo-wc's 90 is the device turned
    clockwise (left edge up); Android's ROTATION_90 is the device turned
    counter-clockwise (the picture turned clockwise), so 90 <-> 270."""
    if wc_rotation is None:
        return None
    return (360 - int(wc_rotation)) % 360


def touch_pose(display, hinge, wc_rotation, hand):
    """The fields duo2-touchd gets; None = not known (the line is left out and
    the daemon keeps what it had)."""
    return {
        "display": display,
        "hinge": None if hinge is None else round(float(hinge), 1),
        "rotation": android_rotation(wc_rotation),
        "hand": hand if hand in ("left", "right") else None,
    }


def touch_pose_text(pose):
    lines = ["# duo-posed -> duo2-touchd: what Android tells the stock touch HAL"]
    for key in ("display", "hinge", "rotation", "hand"):
        v = pose.get(key)
        if v is not None:
            lines.append("%s %s" % (key, v))
    return "\n".join(lines) + "\n"


def touch_pose_changed(old, new, hinge_step=TOUCH_HINGE_STEP):
    """Worth writing again: a field appeared or changed (the hinge by at least
    `hinge_step` degrees)."""
    if old is None:
        return any(v is not None for v in new.values())
    for key in ("display", "rotation", "hand"):
        if new.get(key) is not None and new.get(key) != old.get(key):
            return True
    h0, h1 = old.get("hinge"), new.get("hinge")
    return h1 is not None and (h0 is None or abs(h1 - h0) >= hinge_step)


# The user's panel choice (Duo Settings, the quick menu: `panels` in
# duo-posed.conf): "both" lets the sensors pick, "left" / "right" keeps to
# that panel whatever the fold says (the other one stays dark); a closed
# phone still blanks.
PANELS = ("both", "left", "right")


def panel_override(pose, panels, outputs):
    """The pose to apply: the sensors' `pose` under the user's `panels`."""
    if pose is None or pose == "blank" or panels not in ("left", "right"):
        return pose
    return "single " + outputs.get(panels, panels)


def fresh_session(seen, generation):
    """(seen, fresh): `fresh` is True exactly once per new duo-wc event
    stream generation (the first snapshot after a (re)connect), which is when
    everything this daemon cached as "sent" is void."""
    return generation, generation != seen


def stream_state(msg):
    """The state snapshot in one line of duo-wc's event stream: the
    subscribe reply's `ok.state` or a `state` event's `state`. None for
    every other line: other events carry fields of their own under the same
    names (volume_key has `"state": "pressed"`), and taking one of those for
    the snapshot made forward_posture fail on a string."""
    if not isinstance(msg, dict):
        return None
    if "event" in msg:
        st = msg.get("state") if msg.get("event") == "state" else None
    else:
        ok = msg.get("ok")
        st = ok.get("state") if isinstance(ok, dict) else None
    return st if isinstance(st, dict) else None
