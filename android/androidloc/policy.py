"""When Android apps get the phone's position, and how exactly (pure).

duo2-android-location keeps test providers in Android ("gps", and
"network" if configured) and learns from Android's own provider-request
listener whether any Android app asks them for locations. Only then does
it ask geoclue (as the host app "duo2-android-location"): for "gps" at
geoclue's EXACT level (the modem's GNSS engine runs), for "network" alone
at NEIGHBORHOOD (the mobile cell; no satellites). When the last request
ends it stops after LINGER_S (an app that re-asks at once, as Android's
fused provider does when it changes its interval, does not restart a
GNSS session). Never while the container is frozen (Waydroid freezes it
when no Android app is on screen: its apps cannot run then), never while
the host's Location switch is off or Android apps are refused in
Settings > Privacy.

Which fix goes where: "gps" gets only satellite-grade fixes (at most
GPS_MAX_ACCURACY_M), "network" any fix while something asks it (the
mobile cell, an IP guess, or a satellite fix). With the host switch off
the providers are switched off in Android too: apps see location off,
not a provider that never answers.

The phone stays awake (a logind sleep inhibitor) while an Android app
uses the GNSS (navigation with the panels dark), if KEEP_AWAKE.
"""

from dataclasses import dataclass, field

NONE, NEIGHBORHOOD, EXACT = 0, 5, 8

GPS = "gps"
NETWORK = "network"


@dataclass
class Config:
    network: bool = True  # also a "network" test provider
    linger_s: float = 10.0
    keep_awake: bool = True
    gps_max_accuracy_m: float = 200.0


@dataclass
class State:
    android: bool = False  # Android's location service answered, providers added
    frozen: bool = False
    allowed: bool = True  # host switch on and Android apps not refused
    requests: dict = field(default_factory=dict)  # provider -> asked now (bool)


@dataclass
class Decision:
    level: int  # geoclue level to run at (NONE: no client)
    providers_enabled: bool  # the test providers' enabled flag in Android
    inhibit: bool  # hold the phone awake


def parse_config(text):
    """KEY=VALUE lines (/etc/duo2/android-location.conf, then the user's
    ~/.config/duo2/android-location.conf): NETWORK, LINGER_S, KEEP_AWAKE,
    GPS_MAX_ACCURACY_M. Unknown keys and bad values are ignored."""
    cfg = {}
    for line in (text or "").splitlines():
        line = line.split("#", 1)[0].strip()
        if "=" not in line:
            continue
        key, value = (x.strip() for x in line.split("=", 1))
        cfg[key.upper()] = value
    return cfg


def apply_config(base, cfg):
    def flag(v):
        return v.lower() in ("1", "yes", "true", "on")

    out = Config(base.network, base.linger_s, base.keep_awake, base.gps_max_accuracy_m)
    try:
        if "NETWORK" in cfg:
            out.network = flag(cfg["NETWORK"])
        if "KEEP_AWAKE" in cfg:
            out.keep_awake = flag(cfg["KEEP_AWAKE"])
        if "LINGER_S" in cfg:
            out.linger_s = min(600.0, max(0.0, float(cfg["LINGER_S"])))
        if "GPS_MAX_ACCURACY_M" in cfg:
            out.gps_max_accuracy_m = min(5000.0, max(5.0, float(cfg["GPS_MAX_ACCURACY_M"])))
    except ValueError:
        pass
    return out


def providers(cfg):
    return [GPS, NETWORK] if cfg.network else [GPS]


class Policy:
    """Feed it the state whenever something changes (and at
    next_check(), for the linger to end); it answers what should run."""

    def __init__(self, cfg=None):
        self.cfg = cfg or Config()
        self.last_wanted = {GPS: None, NETWORK: None}  # monotonic time a provider was last asked

    def _asked(self, st, provider):
        if provider == NETWORK and not self.cfg.network:
            return False
        return bool(st.requests.get(provider))

    def decide(self, st, now):
        usable = st.android and st.allowed and not st.frozen
        wanted = {}
        for p in (GPS, NETWORK):
            if usable and self._asked(st, p):
                self.last_wanted[p] = now
                wanted[p] = True
            elif not usable:
                self.last_wanted[p] = None
                wanted[p] = False
            else:
                t = self.last_wanted[p]
                wanted[p] = t is not None and now - t < self.cfg.linger_s
        if wanted[GPS]:
            level = EXACT
        elif wanted[NETWORK]:
            level = NEIGHBORHOOD
        else:
            level = NONE
        return Decision(
            level=level,
            providers_enabled=st.android and st.allowed,
            inhibit=self.cfg.keep_awake
            and level == EXACT
            and bool(st.requests.get(GPS))
            and usable,
        )

    def next_check(self, now):
        """Seconds until a linger ends (None: nothing pending)."""
        ends = [t + self.cfg.linger_s - now for t in self.last_wanted.values() if t is not None]
        ends = [e for e in ends if e > 0]
        return min(ends) if ends else None

    def route(self, st, fix_accuracy_m):
        """The providers a fix goes to now."""
        out = []
        if not (st.android and st.allowed and not st.frozen):
            return out
        if (
            self._asked(st, GPS)
            and fix_accuracy_m is not None
            and 0 < fix_accuracy_m <= self.cfg.gps_max_accuracy_m
        ):
            out.append(GPS)
        if self._asked(st, NETWORK) and fix_accuracy_m is not None and fix_accuracy_m > 0:
            out.append(NETWORK)
        return out


# -- geoclue's fix -> Location fields -----------------------------------------------

GCLUE_UNKNOWN_ALTITUDE = -1.7976931348623157e308


def fix_from_geoclue(props, now_wall_ms):
    """A geoclue Location object's properties (Latitude, Longitude,
    Accuracy, Altitude, Speed, Heading, Timestamp (s, us)) as the fields
    locmgr.location() writes; None for a fix that cannot be used.
    geoclue says "unknown" with -DBL_MAX (altitude) and -1 (speed,
    heading)."""
    try:
        lat = float(props["Latitude"])
        lon = float(props["Longitude"])
        acc = float(props["Accuracy"])
    except (KeyError, TypeError, ValueError):
        return None
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0) or not acc > 0:
        return None
    fix = {"latitude": lat, "longitude": lon, "accuracy": acc}
    alt = props.get("Altitude")
    if alt is not None and float(alt) > GCLUE_UNKNOWN_ALTITUDE / 2:
        fix["altitude"] = float(alt)
    speed = props.get("Speed")
    if speed is not None and float(speed) >= 0:
        fix["speed"] = float(speed)
    heading = props.get("Heading")
    if heading is not None and 0 <= float(heading) <= 360:
        fix["bearing"] = float(heading) % 360.0
    ts = props.get("Timestamp")
    time_ms = None
    if isinstance(ts, (tuple, list)) and len(ts) == 2:
        time_ms = int(ts[0]) * 1000 + int(ts[1]) // 1000
    # a fix from the future or more than a minute old: stamped now (Android
    # orders and ages fixes by time; geoclue's stamp is the modem's)
    if time_ms is None or time_ms > now_wall_ms + 1000 or time_ms < now_wall_ms - 60000:
        time_ms = now_wall_ms
    fix["time_ms"] = time_ms
    return fix


def age_ns(fix, now_wall_ms):
    return max(0, now_wall_ms - fix["time_ms"]) * 1000000


# -- who may: the host side -------------------------------------------------------------

DESKTOP_ID = "duo2-android-location"


def host_allowed(store_enabled, denied_ids):
    """The host's Location switch (the shell's location.json) and Settings
    > Privacy's per-app refusal (geoclue's 91-duo2-apps.conf)."""
    return bool(store_enabled) and DESKTOP_ID not in (denied_ids or ())


def parse_denied(text):
    """Desktop ids with allowed=false in geoclue's config (as
    duo-settings' privacy.parse_denied)."""
    denied, section, allowed = set(), None, None
    for line in (text or "").splitlines() + ["[]"]:
        line = line.split("#", 1)[0].strip()
        if line.startswith("[") and line.endswith("]"):
            if section and allowed is False:
                denied.add(section)
            section, allowed = line[1:-1].strip(), None
        elif "=" in line and section:
            key, value = (x.strip() for x in line.split("=", 1))
            if key == "allowed":
                allowed = value.lower() in ("true", "1", "yes")
    return denied


def parse_store_enabled(text):
    import json

    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        return True
    if not isinstance(data, dict):
        return True
    return bool(data.get("enabled", True))


def status(st, decision, counts, first_fix_s, last_accuracy_m, error):
    """The status file (no position in it): what the test script reads."""
    return {
        "android": st.android,
        "frozen": st.frozen,
        "allowed": st.allowed,
        "requests": {k: bool(v) for k, v in sorted(st.requests.items())},
        "geoclue_level": decision.level,
        "providers_enabled": decision.providers_enabled,
        "keeping_awake": decision.inhibit,
        "fixes_sent": dict(counts),
        "first_fix_s": None if first_fix_s is None else round(first_fix_s, 1),
        "last_accuracy_m": None if last_accuracy_m is None else int(round(last_accuracy_m)),
        "error": error,
    }
