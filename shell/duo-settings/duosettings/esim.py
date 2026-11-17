"""The eSIM page's pure parts: the helper's answer, the activation code's
shape, identifiers by their last digits."""

import json
import re

CODE = re.compile(
    r"^(?:LPA:)?1\$[A-Za-z0-9.-]+(?::[0-9]+)?\$[A-Za-z0-9._-]*(?:\$[0-9.]*)?(?:\$1)?$"
)


def looks_like_code(code):
    """An activation code as the helper takes it (LPA:1$<SM-DP+>$<id>)."""
    return bool(CODE.match((code or "").strip()))


def mask(value, keep=4):
    value = value or ""
    return ("•••• " + value[-keep:]) if len(value) > keep else value


def parse(out):
    try:
        data = json.loads((out or "").strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"ok": False, "error": "no answer"}
    return data if isinstance(data, dict) else {"ok": False, "error": "no answer"}
