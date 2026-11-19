"""OpenDuo's updates from its own repository, the pure parts (duo2-updates,
Settings > Updates; docs/UPDATES.md).

  - the settings (/etc/duo2/updates.conf): automatic install at night, the
    daily check's notification, the channel (stable, or testing in developer
    mode), whether the daily check may use mobile data;
  - when an install may run: on the charger, or on battery at 50 % or more;
    the automatic one only at night, on WiFi and on the charger;
  - which updates are OpenDuo's own and which are Debian's;
  - the changes: OpenDuo's CHANGELOG.md sections carried by the openduo-release
    package (its OpenDuo-Changes field, packaging/), newer than what runs.
"""

import re

from . import debver

CONF = "/etc/duo2/updates.conf"
DEFAULTS = {
    "auto_install": "off",
    "notify": "on",
    "channel": "stable",
    "check_on_mobile_data": "off",
}
CHANNELS = ("stable", "testing")
MIN_BATTERY = 50
NIGHT = (1, 5)  # the automatic install starts between 01:00 and 05:00 local time
RELEASE_PACKAGE = "openduo-release"
CHANGES_FIELD = "OpenDuo-Changes"
TESTING_SOURCES = "/etc/apt/sources.list.d/openduo-testing.sources"
STABLE_SOURCES = "/etc/apt/sources.list.d/openduo.sources"


def yes(v):
    return str(v).strip().lower() in ("on", "yes", "true", "1")


def parse_conf(text):
    cfg = dict(DEFAULTS)
    for line in (text or "").splitlines():
        line = line.split("#", 1)[0].strip()
        if "=" not in line:
            continue
        k, v = (x.strip() for x in line.split("=", 1))
        if k in DEFAULTS:
            cfg[k] = v
    if cfg["channel"] not in CHANNELS:
        cfg["channel"] = "stable"
    for k in ("auto_install", "notify", "check_on_mobile_data"):
        cfg[k] = "on" if yes(cfg[k]) else "off"
    return cfg


def render_conf(cfg):
    return (
        "# OpenDuo updates (Settings > Updates writes this file; docs/UPDATES.md)\n"
        "# auto_install: install at night, on WiFi and the charger (on / off)\n"
        "auto_install = %(auto_install)s\n"
        "# notify: the daily check says when updates are there (on / off)\n"
        "notify = %(notify)s\n"
        "# channel: stable, or testing (developer mode only)\n"
        "channel = %(channel)s\n"
        "# check_on_mobile_data: the daily check also without WiFi (on / off)\n"
        "check_on_mobile_data = %(check_on_mobile_data)s\n" % cfg
    )


SETTABLE = {
    "auto": ("auto_install", ("on", "off")),
    "notify": ("notify", ("on", "off")),
    "mobile-data": ("check_on_mobile_data", ("on", "off")),
    "channel": ("channel", CHANNELS),
}


def apply_setting(cfg, key, value, developer_mode):
    """(new cfg, None) or (cfg, error)."""
    if key not in SETTABLE:
        return cfg, "unknown setting"
    field, allowed = SETTABLE[key]
    if value not in allowed:
        return cfg, "not a value for %s" % key
    if key == "channel" and value == "testing" and not developer_mode:
        return cfg, "the testing channel needs developer mode"
    new = dict(cfg)
    new[field] = value
    return new, None


def try_boot(cmdline):
    """True on a scripts/duo2-try boot (its entry adds duo2.try=<id>): duo2-update
    refuses to install there, the known-good kernel has to run."""
    return any(w.startswith("duo2.try=") or w == "duo2.try" for w in (cmdline or "").split())


def may_install(battery, on_charger, minimum=MIN_BATTERY):
    """None when an install may start now, else why not."""
    if on_charger:
        return None
    if battery is None:
        return None  # no battery reading (a test rig): nothing to protect
    if battery < minimum:
        return "the battery is at %d %%: connect the charger or wait until %d %%" % (
            battery,
            minimum,
        )
    return None


def may_auto(cfg, on_wifi, on_charger, hour):
    """None when the nightly automatic install may start, else why not."""
    if not yes(cfg.get("auto_install")):
        return "automatic install is off"
    if not (NIGHT[0] <= hour < NIGHT[1]):
        return "not at night"
    if not on_wifi:
        return "not on WiFi"
    if not on_charger:
        return "not on the charger"
    return None


def may_check(cfg, on_wifi, forced):
    if forced or on_wifi or yes(cfg.get("check_on_mobile_data")):
        return None
    return "not on WiFi (the daily check waits for WiFi)"


def is_ours(name, version=""):
    """An OpenDuo package, or one of our rebuilds of Debian's (camera/build-debs.sh)."""
    return name.startswith("openduo-") or debver.is_local(version)


def classify(plan):
    """aptplan.parse_simulation's plan -> {"ours": [...], "debian": [...],
    "removed": [...]}, each {"name", "old", "new", "security"}."""
    out = {"ours": [], "debian": [], "removed": []}
    for name, old, new, origin in plan.get("install", []):
        item = {
            "name": name,
            "old": old,
            "new": new,
            "security": "security" in (origin or "").lower(),
        }
        out["ours" if is_ours(name, new) else "debian"].append(item)
    for name, old in plan.get("remove", []):
        out["removed"].append({"name": name, "old": old})
    return out


# -- the changes -------------------------------------------------------------------------------

HEADING = re.compile(r"^## \[(?P<v>[^\]]+)\](?:\s*-\s*(?P<date>\S+))?")


def changelog_sections(md, limit=3):
    """CHANGELOG.md -> [{"version", "date", "text"}], newest first (Keep a
    Changelog: "## [0.1.0] - 2026-12-01", "## [Unreleased]")."""
    out, cur = [], None
    for line in (md or "").splitlines():
        m = HEADING.match(line)
        if m:
            if len(out) >= limit:
                break
            cur = {"version": m.group("v"), "date": m.group("date") or "", "lines": []}
            out.append(cur)
        elif cur is not None:
            if line.startswith("[") and "]: " in line:  # link references at the end
                continue
            cur["lines"].append(line)
    for s in out:
        s["text"] = "\n".join(s.pop("lines")).strip("\n")
    return out


def encode_field(text):
    """Text as an RFC 822 continuation (a control field's value)."""
    lines = (text or "").strip("\n").splitlines() or [""]
    return "\n".join(" " + (line if line.strip() else ".") for line in lines)


def decode_field(value):
    lines = []
    for line in (value or "").splitlines():
        s = line[1:] if line.startswith(" ") else line
        lines.append("" if s == "." else s)
    return "\n".join(lines).strip("\n")


def field_of(stanza, field):
    """A field of an apt-cache show / Packages stanza (multi-line kept)."""
    out, on = [], False
    for line in (stanza or "").splitlines():
        if on and (line.startswith(" ") or line.startswith("\t")):
            out.append(line)
            continue
        on = False
        k, _, v = line.partition(":")
        if k.strip().lower() == field.lower():
            on = True
            if v.strip():
                out.append(" " + v.strip())
    return "\n".join(out)


def stanza_for(text, version):
    """The stanza of apt-cache show's output with that Version."""
    for st in re.split(r"\n\s*\n", text or ""):
        for line in st.splitlines():
            if line.startswith("Version:") and line.split(":", 1)[1].strip() == version:
                return st
    return ""


def os_version_of(deb_version):
    """0.1.0~dev+20261117001503.g5b008a3 -> 0.1.0-dev (the VERSION file's form)."""
    base = (deb_version or "").split(":", 1)[-1].split("+", 1)[0]
    return base.replace("~", "-")


def newer_sections(sections, installed, candidate):
    """The sections a person updating from `installed` (VERSION form,
    0.1.0-dev) to `candidate` has not seen."""
    out = []
    cand_dev = "-" in (candidate or "")
    inst = (installed or "0").replace("-", "~")
    for s in sections:
        v = s["version"]
        if v.lower() == "unreleased":
            if cand_dev:
                out.append(s)
            continue
        if debver.newer(v.replace("-", "~"), inst):
            out.append(s)
    return out


# -- the channel -------------------------------------------------------------------------------


def testing_sources(stable_text):
    """The testing channel's sources file: the stable one with Suites: testing."""
    lines = []
    for line in (stable_text or "").splitlines():
        if line.startswith("Suites:"):
            line = "Suites: testing"
        lines.append(line)
    return (
        "# OpenDuo's testing channel (Settings > Updates, developer mode; docs/UPDATES.md).\n"
        "# Removing this file goes back to stable: what testing installed stays until\n"
        "# stable has a newer version.\n" + "\n".join(lines).strip("\n") + "\n"
    )


# -- the job as the page sees it ---------------------------------------------------------------

# `systemctl is-active`'s answers for a unit that is doing its work: a
# Type=oneshot job (duo2-updates-check/-install.service) is "activating"
# for as long as it runs, and is-active -q says no to that.
RUNNING_STATES = ("active", "activating", "deactivating", "reloading")
START_GRACE = 60  # s a "starting" record waits for its unit before it counts as stopped


def unit_running(is_active_answer):
    return (is_active_answer or "").strip() in RUNNING_STATES


def starting_record(name, now):
    """What `duo2-updates check|install` writes before it starts the unit, so
    that the page shows the job at once (the job replaces it when it runs)."""
    return {"job": name, "state": "running", "step": "starting", "started": int(now)}


def job_view(job, running, now):
    """The job as `duo2-updates status` reports it. `job`: the job record
    (job.json) or None; `running`: {job name: whether its unit runs}.

    - a unit that runs is a running job, also before its first record
      (shown as "starting");
    - a running record whose unit has stopped is failed, with the time it was
      noticed (the page shows a failure newer than the last check), unless it
      is a "starting" record still within its grace;
    - anything else as it is."""
    job = dict(job) if job else None
    busy = [n for n, r in sorted(running.items()) if r]
    if busy and not (job and job.get("state") == "running" and job.get("job") in busy):
        return starting_record(busy[0], now)
    if job and job.get("state") == "running" and not running.get(job.get("job")):
        if job.get("step") == "starting" and now - job.get("started", 0) < START_GRACE:
            return job
        job.update(state="failed", finished=int(now))
        job["error"] = job.get("error") or "the job stopped"
    return job


# What duo2-update run prints as it goes (its "== " headings) -> the install's
# phase, which the page translates.
PHASES = (
    ("== apt-get update", "refresh"),
    ("== downloading", "download"),
    ("== snapshot", "snapshot"),
    ("== apt-get dist-upgrade", "install"),
    ("== after the upgrade", "verify"),
)


def install_phase(line):
    """The phase a line of `duo2-update run` starts, or None."""
    for head, phase in PHASES:
        if (line or "").startswith(head):
            return phase
    return None
