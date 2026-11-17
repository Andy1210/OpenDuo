"""Is a newly installed kernel healthy? The rules (pure; duo2-boot health
gathers the facts, docs/UPDATES.md "The kernel and its way back").

A boot from a packaged kernel entry that is still being tried passes when,
within the time allowed (awake time, not counting a suspend):

  - the compositor (duo-wc) is up: the panels show the session;
  - touch works: duo2-touchd is running, or it is skipped because this phone
    has no touch runtime yet (a post-install step, not the kernel's fault);
  - the phone can reach a network: WiFi is connected or the modem is
    registered, or there is nothing it could connect to (the radios switched
    off by the user, no WiFi network saved and no usable SIM).

The verdicts:
  pass       bless the entry (the kernel becomes the known-good one);
  wait       look again later;
  fail-hard  the phone is not usable (no picture, no touch, no radio at all):
             mark the entry bad and restart into the previous kernel;
  fail-soft  no network although one is expected: do not bless, leave the
             counter (the next start tries this kernel again; once its tries
             are used up systemd-boot boots the previous one by itself).
"""

UP = ("active",)
NOT_RUN = ("skipped", "missing")  # the condition said no / no such unit
FAILED = ("failed",)

DEFAULT_TIMEOUT = 300  # s of awake time
RADIO_GRACE = 120  # s: a WiFi device or a modem should exist by then


SHOW_PROPERTIES = ("ActiveState", "SubState", "ConditionResult", "LoadState", "Result")


def unit_state(active_state, condition_result="yes", load_state="loaded", result="success"):
    """systemctl show's ActiveState, ConditionResult, LoadState, Result ->
    active / skipped / missing / failed / starting. A unit that is starting
    again after a failure (activating, auto-restart) is starting, not failed:
    a restart early in the boot is no verdict (it is one when it lasts)."""
    if load_state in ("not-found", "masked"):
        return "missing"
    if active_state == "active":
        return "active"
    if active_state in ("activating", "reloading"):
        return "starting"
    if active_state == "failed" or result not in ("success", ""):
        return "failed"
    if condition_result == "no":
        return "skipped"
    return "starting"


def unit_state_of_show(text):
    """unit_state from `systemctl show --property=<SHOW_PROPERTIES>` output.
    systemctl prints the properties in its own order, not in the order they
    were asked for, so they are read by name."""
    props = {}
    for line in (text or "").splitlines():
        key, sep, value = line.partition("=")
        if sep:
            props[key.strip()] = value.strip()
    return unit_state(
        props.get("ActiveState", ""),
        props.get("ConditionResult") or "yes",
        props.get("LoadState") or "loaded",
        props.get("Result") or "success",
    )


def network_ok(f):
    """(ok, why) for the network rule."""
    if f.get("wifi_connected"):
        return True, "WiFi connected"
    if f.get("modem_registered"):
        return True, "the modem is registered"
    wifi_idle = (not f.get("wifi_device")) or f.get("wifi_blocked") or not f.get("wifi_configured")
    modem_idle = (not f.get("modem_present")) or f.get("modem_unusable")
    if f.get("wifi_device") and wifi_idle and modem_idle:
        return True, "nothing to connect to (radios off, no saved network, no usable SIM)"
    return False, "no network yet"


def evaluate(facts, elapsed, timeout=DEFAULT_TIMEOUT):
    """(verdict, [reasons]). `facts`: {"compositor": unit_state,
    "touch": unit_state, "wifi_device", "wifi_connected", "wifi_blocked",
    "wifi_configured", "modem_present", "modem_registered", "modem_unusable":
    bools}; `elapsed`: awake seconds since the boot."""
    hard, waiting, reasons = [], [], []
    late = elapsed >= timeout

    comp = facts.get("compositor", "missing")
    if comp in UP:
        reasons.append("compositor up")
    elif comp in FAILED or comp == "missing":
        hard.append("the compositor (duo-wc) is %s" % comp)
    else:
        (hard if late else waiting).append("the compositor (duo-wc) is not up")

    touch = facts.get("touch", "missing")
    if touch in UP:
        reasons.append("touch up")
    elif touch in NOT_RUN:
        reasons.append("touch not checked (no touch runtime on this phone)")
    elif touch in FAILED:
        hard.append("touch (duo2-touchd) failed")
    else:
        (hard if late else waiting).append("touch (duo2-touchd) is not up")

    ok, why = network_ok(facts)
    radios = facts.get("wifi_device") or facts.get("modem_present")
    if ok:
        reasons.append(why)
    elif not radios and elapsed >= RADIO_GRACE:
        hard.append("no WiFi device and no modem")
    elif late:
        reasons.append("no network after %d s although one is expected" % int(elapsed))
        if not hard:
            return "fail-soft", reasons
    else:
        waiting.append(why)

    if hard:
        return "fail-hard", hard + reasons
    if waiting:
        return "wait", waiting + reasons
    return "pass", reasons
