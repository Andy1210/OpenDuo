"""The proximity sensor during a call: which panels go dark (pure).

While a call is being dialed, rings out or is up (not while it rings in:
the answer button has to be reachable) and its sound comes from the
earpiece (not the loudspeaker), something near the sensor (an ear, a
cheek) darkens the panel the call is on and its touches are not delivered;
far again, the call over, or the speaker on, it comes back (duo-wc's
`set_proximity`, a hold it ends by itself when the shell stops renewing it).
Outside calls nothing happens and the sensor is not even claimed (it costs
power on the sensor DSP).

The panel the call is on: the one with the Calls window (both of them when
it spans); with no Calls window on the panels (the call was started
elsewhere, Calls in the background) every lit panel, since the phone at an
ear covers them all.

"Far" counts after FAR_DELAY_S (a sensor flickers at the edge of its range,
and a panel coming back for a tenth of a second is worse than a short wait);
"near" at once, as phones do.
"""

# ModemManager's MMCallState.
CALL_UNKNOWN, CALL_DIALING, CALL_RINGING_OUT, CALL_RINGING_IN = 0, 1, 2, 3
CALL_ACTIVE, CALL_HELD, CALL_WAITING, CALL_TERMINATED = 4, 5, 6, 7
HOLD_STATES = frozenset({CALL_DIALING, CALL_RINGING_OUT, CALL_ACTIVE})

# callaudiod's interface (duo2-callaudio): AudioMode 1 = call.
AUDIO_MODE_CALL = 1

CALLS_APP_IDS = ("org.gnome.Calls",)

FAR_DELAY_S = 0.25
# The hold is renewed this often, and lasts this long in duo-wc without a
# renewal (a shell that died with the phone at an ear cannot leave a panel
# dark for longer).
RENEW_S = 10
TTL_MS = 30000


def call_wants_sensor(call_states, speaker):
    """Whether the proximity sensor matters now: a call dialing, ringing
    out or up, heard on the earpiece. `speaker` is callaudiod's
    SpeakerState (True: the loudspeakers; None: unknown, as the earpiece
    is the default route of a call)."""
    return any(s in HOLD_STATES for s in call_states) and speaker is not True


def sensor_recheck(call_wants, has_sensor, asked):
    """Whether to ask iio-sensor-proxy again whether it has a proximity
    sensor: a call wants one, the last answer said there is none, and this
    call has not asked yet. The proxy tells only the clients holding a claim
    when its sensors appear, so an answer read while it was still starting
    (it comes up a minute after the shell) is never corrected by itself."""
    return bool(call_wants and not has_sensor and not asked)


def describe(call_states, speaker, has_sensor):
    """One log line for what the sensor decision is made from."""
    states = ",".join(str(s) for s in sorted(call_states)) or "none"
    route = {True: "loudspeaker", False: "earpiece", None: "unknown"}[speaker]
    return "calls %s, route %s, sensor %s" % (states, route, "yes" if has_sensor else "no")


def call_panels(windows, candidates):
    """The panels the call is on: those of the Calls windows that are not
    minimized (a span covers every candidate), else every candidate.
    `windows`: duo-wc's state windows; `candidates`: the panels that may be
    darkened (the lit ones plus the ones held dark already)."""
    candidates = sorted(set(candidates))
    found = set()
    for w in windows or []:
        if w.get("app_id") not in CALLS_APP_IDS or w.get("minimized"):
            continue
        placement = w.get("placement")
        if placement == "span":
            return candidates
        if isinstance(placement, dict) and placement.get("output") in candidates:
            found.add(placement["output"])
    return sorted(found) if found else candidates


def hold_panels(wanted, near, windows, lit, held):
    """The panels to hold dark: none unless the sensor matters and says
    near."""
    if not (wanted and near):
        return []
    return call_panels(windows, set(lit or []) | set(held or []))


class Debounce:
    """The sensor's raw near / far to the one acted on: near at once, far
    only once it has been far for `far_delay` seconds."""

    def __init__(self, far_delay=FAR_DELAY_S):
        self.far_delay = far_delay
        self.raw = False
        self.since = None
        self.near = False

    def feed(self, raw, now):
        """A reading at `now` (seconds, monotonic). Returns the seconds
        until the next `settle` is worth asking, or None."""
        raw = bool(raw)
        if raw != self.raw:
            self.raw = raw
            self.since = now
        if raw:
            self.near = True
            return None
        return self.settle(now)

    def settle(self, now):
        """Apply the delay: returns the seconds still to wait, or None once
        the state is final."""
        if self.raw or not self.near:
            return None
        waited = now - (self.since if self.since is not None else now)
        if waited >= self.far_delay:
            self.near = False
            return None
        return self.far_delay - waited

    def reset(self):
        """The sensor was released: nothing is near."""
        self.raw = False
        self.since = None
        self.near = False
