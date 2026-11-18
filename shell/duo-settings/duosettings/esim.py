"""The eSIM page's pure parts: the helper's answer, the activation code's
shape (typed, pasted or read from a QR code), identifiers by their last
digits."""

import gettext
import json
import re

_ = gettext.gettext

CODE = re.compile(
    r"^(?:LPA:)?1\$[A-Za-z0-9.-]+(?::[0-9]+)?\$[A-Za-z0-9._-]*(?:\$[0-9.]*)?(?:\$1)?$"
)


def normalize(code):
    """An activation code as the helper takes it: no spaces or line breaks
    around it, the scheme as "LPA:" (a QR code may say it in lower case, a
    typed code may leave it out)."""
    code = (code or "").strip()
    if code[:4].upper() == "LPA:":
        return "LPA:" + code[4:]
    return "LPA:" + code if code.startswith("1$") else code


def looks_like_code(code):
    """An activation code as the helper takes it (LPA:1$<SM-DP+>$<id>)."""
    return bool(CODE.match(normalize(code)))


def server(code):
    """The operator's server an activation code names ("" if none)."""
    parts = normalize(code).split("$")
    return parts[1] if len(parts) > 2 else ""


def needs_confirmation(code):
    """The code's fifth field says the operator gave a confirmation code
    with it (SGP.22 4.1)."""
    parts = normalize(code).split("$")
    return len(parts) > 4 and parts[4] == "1"


def mask(value, keep=4):
    value = value or ""
    return ("•••• " + value[-keep:]) if len(value) > keep else value


def parse(out):
    try:
        data = json.loads((out or "").strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"ok": False, "error": "no answer"}
    return data if isinstance(data, dict) else {"ok": False, "error": "no answer"}


def failure_text(res):
    """What to tell the user about a failed change: the helper's words, and
    lpac's own step and reason under them (for a bug report)."""
    err = res.get("error") or ""
    if err == "not confirmed":
        return _("Not done: it was not confirmed.")
    text = err or _("It did not work.")
    step, detail = res.get("step") or "", res.get("detail") or ""
    if step:
        text += "\n\n(%s%s)" % (step, ": " + detail if detail else "")
    return text


# A download's steps (the helper's {"progress": STEP} lines) as the page's
# progress: how far, and what to say. lpac's own step names; its cancel
# steps (a failure on the way) change nothing.
STAGES = {
    "confirmed": (0.1, "Connecting to the operator…"),
    "es10b_get_euicc_challenge_and_info": (0.15, "Connecting to the operator…"),
    "es9p_initiate_authentication": (0.25, "Connecting to the operator…"),
    "es10b_authenticate_server": (0.35, "Checking the code with the operator…"),
    "es9p_authenticate_client": (0.45, "Checking the code with the operator…"),
    "es8p_meatadata_parse": (0.55, "Downloading the profile…"),
    "es10b_prepare_download": (0.6, "Downloading the profile…"),
    "es9p_get_bound_profile_package": (0.7, "Downloading the profile…"),
    "es10b_load_bound_profile_package": (0.85, "Installing the profile on the eSIM…"),
    "checking": (0.95, "Checking the eSIM…"),
}


def progress_stage(line):
    """(fraction, text) for one line of the helper's output, or None."""
    try:
        obj = json.loads(line or "")
    except ValueError:
        return None
    step = obj.get("progress") if isinstance(obj, dict) else None
    if step not in STAGES:
        return None
    fraction, text = STAGES[step]
    return fraction, _(text)
