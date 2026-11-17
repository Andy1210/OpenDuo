"""Voicemail (pure parts tested in tests/test_voicemail.py): the mailbox's
number and how it is called.

Neither ModemManager (1.24) nor GNOME Calls (48) knows a voicemail: there
is no mailbox number on ModemManager's bus (the SIM's EF_MBDN is not read)
and no message-waiting indication (ModemManager drops QMI's MWI messages
and keeps an MWI text SMS as a plain SMS, which Chatty shows), and Calls'
dial pad has no long press on 1 (libcall-ui's keypad long-presses only 0,
for "+"). So the number is a setting (Duo Settings > SIM card > Voicemail
number, ~/.config/duo-shell/voicemail.json), with the operator's documented
number offered, and the app grid's **Voicemail** (duo-voicemail) dials it
through Calls (`gnome-calls --dial`); without a number it opens that
setting.

The numbers are looked up by the SIM's network code, not by a provider's
name, so every provider on a network gets the same offer. Telekom
Deutschland's network (26201): 3311 (telekom.de, Mobilbox); some prepaid
providers on that network use 4712.
"""

import json
import os

# network code (MCC + MNC) -> [(number, what)]
KNOWN = {
    "26201": (("3311", "Telekom network, Mobilbox"), ("4712", "Telekom network, some prepaid")),
}
ALLOWED = set("0123456789+*#")


def config_path():
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "duo-shell", "voicemail.json")


def clean(number):
    """A mailbox number as dialled: digits, a leading '+', '*' and '#'
    (spaces and dashes dropped); '' when it is not a number."""
    out = "".join(ch for ch in (number or "").strip() if ch not in " -/().")
    if not out or len(out) > 32 or any(ch not in ALLOWED for ch in out) or "+" in out[1:]:
        return ""
    return out


def load(path=None):
    try:
        with open(path or config_path()) as f:
            return clean(json.load(f).get("number", ""))
    except (OSError, ValueError, AttributeError, TypeError):
        return ""


def save(number, path=None):
    path = path or config_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "w") as f:
        json.dump({"number": clean(number)}, f)
        f.write("\n")
    os.replace(path + ".tmp", path)


def suggestions(network_code=""):
    """The network's documented mailbox numbers: [(number, what)]."""
    return list(KNOWN.get((network_code or "").strip(), ()))


def dial_argv(number):
    """How the launcher calls the mailbox: GNOME Calls dials it (the running
    instance gets it; its window shows the call)."""
    n = clean(number)
    return ["gnome-calls", "--dial", n] if n else ["duo-settings", "sim"]
