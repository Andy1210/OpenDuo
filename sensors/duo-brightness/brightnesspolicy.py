# SPDX-License-Identifier: GPL-3.0-or-later
"""Automatic brightness, the pure part (duo-brightness; unit-tested).

The light comes in lux; the panels' backlight goes out as a fraction of its
maximum (0..1, never 0). In between, as Android's adaptive brightness does:

* a curve from lux to brightness, in a perceptual unit (0..100, "how bright
  it looks": the backlight's luminance to the power 1/2.2), interpolated over
  log(lux);
* the user's own brightness as an offset on that curve: whatever the user set
  by hand (the slider) at some light is the curve plus an offset there, and
  that offset is kept for every light after it;
* a smoothed light reading (a short time constant brightening, a longer one
  darkening) and a hysteresis band around the light the brightness was last
  set for: the brightness moves only for a real change of the light, after it
  held for a moment (brightening sooner than darkening), not for a hand
  passing over the sensor;
* a ramp, in the perceptual unit: brightening quickly, darkening slowly; a
  panel coming on (or the first reading) starts at its target at once.
"""

import math

GAMMA = 2.2

# lux -> perceptual brightness (0..100). Indoors dim (5 lux) ~30, an office
# (300) ~65, daylight in the shade (3000) ~90, sun 100.
CURVE = (
    (0.0, 12.0),
    (1.0, 18.0),
    (5.0, 28.0),
    (20.0, 38.0),
    (60.0, 47.0),
    (150.0, 55.0),
    (400.0, 66.0),
    (1000.0, 76.0),
    (3000.0, 89.0),
    (10000.0, 100.0),
)

MIN_FRACTION = 0.01  # the lowest backlight the policy sets (1 %)
BRIGHTEN_TAU = 0.8  # s, the light's smoothing towards more light
DARKEN_TAU = 3.0  # s, towards less
BRIGHTEN_BAND = 0.15  # the light must be 15 % above the reference ...
DARKEN_BAND = 0.25  # ... or 25 % below it ...
ABS_BAND = 2.0  # ... and at least 2 lux away (dim rooms are noisy)
BRIGHTEN_HOLD = 0.6  # s, for this long
DARKEN_HOLD = 3.0
BRIGHTEN_RATE = 40.0  # perceptual units per second
DARKEN_RATE = 10.0
OFFSET_LIMIT = 60.0  # the user's offset stays within +-60 units


def to_perceptual(fraction):
    """Backlight fraction (0..1) -> perceptual 0..100."""
    return 100.0 * max(0.0, min(1.0, fraction)) ** (1.0 / GAMMA)


def to_fraction(perceptual):
    """Perceptual 0..100 -> backlight fraction, never below MIN_FRACTION."""
    p = max(0.0, min(100.0, perceptual)) / 100.0
    return max(MIN_FRACTION, p**GAMMA)


def curve(lux, points=CURVE):
    """The perceptual brightness for a light, interpolated over log(1 + lux)."""
    lux = max(0.0, lux)
    x = math.log10(1.0 + lux)
    if x <= math.log10(1.0 + points[0][0]):
        return points[0][1]
    for (l0, b0), (l1, b1) in zip(points, points[1:]):
        x0, x1 = math.log10(1.0 + l0), math.log10(1.0 + l1)
        if x <= x1:
            return b0 + (b1 - b0) * (x - x0) / (x1 - x0)
    return points[-1][1]


def target(lux, offset):
    """The perceptual target for a light and the user's offset."""
    return max(0.0, min(100.0, curve(lux) + offset))


def offset_for(user_fraction, lux):
    """The offset that makes the curve give what the user set at this light."""
    off = to_perceptual(user_fraction) - curve(lux)
    return max(-OFFSET_LIMIT, min(OFFSET_LIMIT, off))


class Light:
    """The smoothed light and the hysteresis decision.

    feed(lux, now) takes a reading; settled(now) says the light the
    brightness should follow now (the reference), changing it only when the
    smoothed light has left the band around it for long enough."""

    def __init__(self):
        self.smooth = None  # smoothed lux
        self.last = None  # time of the last reading
        self.reference = None  # the lux the brightness follows
        self.out_since = None  # since when the smoothed light is out of the band
        self.out_dir = 0

    def feed(self, lux, now):
        lux = max(0.0, float(lux))
        if self.smooth is None:
            self.smooth = lux
            self.reference = lux
        else:
            dt = max(0.0, now - self.last)
            tau = BRIGHTEN_TAU if lux > self.smooth else DARKEN_TAU
            a = 1.0 - math.exp(-dt / tau) if tau > 0 else 1.0
            self.smooth += a * (lux - self.smooth)
        self.last = now

    def reset(self):
        """A panel came on (or the sensor came back): the next reading is
        taken as it is."""
        self.__init__()

    def settled(self, now):
        if self.smooth is None:
            return None
        ref, s = self.reference, self.smooth
        up = s > ref * (1 + BRIGHTEN_BAND) and s - ref >= ABS_BAND
        down = s < ref * (1 - DARKEN_BAND) and ref - s >= ABS_BAND
        direction = 1 if up else -1 if down else 0
        if direction == 0:
            self.out_since, self.out_dir = None, 0
            return ref
        if direction != self.out_dir or self.out_since is None:
            self.out_since, self.out_dir = now, direction
        hold = BRIGHTEN_HOLD if direction > 0 else DARKEN_HOLD
        if now - self.out_since >= hold:
            self.reference = s
            self.out_since, self.out_dir = None, 0
        return self.reference


class Ramp:
    """The brightness on its way to the target, in perceptual units."""

    def __init__(self):
        self.value = None

    def jump(self, target):
        self.value = target

    def step(self, target, dt):
        """Move towards the target; returns True while still moving."""
        if self.value is None:
            self.value = target
            return False
        diff = target - self.value
        if abs(diff) < 0.05:
            self.value = target
            return False
        rate = BRIGHTEN_RATE if diff > 0 else DARKEN_RATE
        move = rate * max(0.0, dt)
        self.value = target if abs(diff) <= move else self.value + math.copysign(move, diff)
        return self.value != target


def level(fraction, maximum):
    """A backlight level for a fraction of `maximum` (never 0)."""
    return max(1, min(maximum, int(round(fraction * maximum))))
