#!/usr/bin/python3
"""A fake lpac: a chip with two test profiles (made-up numbers), for trying
duo2-esim and Duo Settings' eSIM page without touching the real eUICC.
duo2-esim runs it instead of lpac while root's /etc/duo2/esim-fake exists.
Its state is in $FAKE_LPAC_STATE (default /run/duo2-esim-fake.json): what
was enabled, renamed, deleted, downloaded. Output as lpac's (JSON lines)."""

import json
import os
import sys

STATE = os.environ.get("FAKE_LPAC_STATE", "/run/duo2-esim-fake.json")
EID = "89049032000000000000000000000042"
START = [
    {
        "iccid": "89000000000000000011",
        "isdpAid": "A0000005591010FFFFFFFF8900001000",
        "profileState": "enabled",
        "profileNickname": None,
        "serviceProviderName": "Test Operator A",
        "profileName": "Test A",
        "iconType": "none",
        "icon": None,
        "profileClass": "operational",
    },
    {
        "iccid": "89000000000000000022",
        "isdpAid": "A0000005591010FFFFFFFF8900001100",
        "profileState": "disabled",
        "profileNickname": "Travel",
        "serviceProviderName": "Test Operator B",
        "profileName": "Test B",
        "iconType": "none",
        "icon": None,
        "profileClass": "operational",
    },
]


def load():
    try:
        with open(STATE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return [dict(p) for p in START]


def save(profiles):
    with open(STATE, "w") as f:
        json.dump(profiles, f)


def reply(code=0, message="success", data=None):
    print(
        json.dumps(
            {
                "type": "lpa",
                "payload": {
                    "code": code,
                    "message": message,
                    "data": data if data is not None else {},
                },
            }
        )
    )
    return code


def find(profiles, iccid):
    return next((p for p in profiles if p["iccid"] == iccid), None)


def main(argv):
    profiles = load()
    if argv[:2] == ["chip", "info"]:
        return reply(
            data={
                "eidValue": EID,
                "EuiccConfiguredAddresses": {
                    "defaultDpAddress": None,
                    "rootDsAddress": "lpa.ds.gsma.com",
                },
                "EUICCInfo2": {
                    "svn": "2.2.0",
                    "euiccFirmwareVer": "4.6.0",
                    "extCardResource": {"freeNonVolatileMemory": 312345},
                },
            }
        )
    if argv[:2] == ["profile", "list"]:
        return reply(data=profiles)
    if argv[:2] == ["notification", "process"]:
        return reply(data=None)
    if (
        argv[:2]
        in (
            ["profile", "enable"],
            ["profile", "disable"],
            ["profile", "delete"],
            ["profile", "nickname"],
        )
        and len(argv) >= 3
    ):
        p = find(profiles, argv[2])
        if p is None:
            return reply(-1, "profile not found")
        if argv[1] == "enable":
            for other in profiles:
                other["profileState"] = "disabled"
            p["profileState"] = "enabled"
        elif argv[1] == "disable":
            p["profileState"] = "disabled"
        elif argv[1] == "delete":
            if p["profileState"] == "enabled":
                return reply(-1, "profile is enabled")
            profiles.remove(p)
        else:
            p["profileNickname"] = argv[3] if len(argv) > 3 and argv[3] else None
        save(profiles)
        print(
            json.dumps(
                {"type": "progress", "payload": {"code": 0, "message": argv[1], "data": None}}
            )
        )
        return reply(data=None)
    if argv[:2] == ["profile", "download"]:
        return download(profiles, argv[2:])
    return reply(-1, "unknown command")


def progress(message, data=None):
    print(
        json.dumps({"type": "progress", "payload": {"code": 0, "message": message, "data": data}})
    )


def download(profiles, args):
    """lpac's download, step by step. The matching id plays a failure:
    OFFLINE (no internet), REFUSED (the server says no), NEEDCONF (a
    confirmation code is needed and none was given), GHOST (lpac says done
    but nothing is installed); anything else installs "Test C"."""
    code = args[args.index("-a") + 1] if "-a" in args else ""
    matching = code.split("$")[2] if code.count("$") >= 2 else ""
    if matching == "NEEDCONF" and "-c" not in args:
        progress("es10b_cancel_session")
        return reply(-1, "confirmation_code", "required")
    progress("es10b_get_euicc_challenge_and_info", "rsp.example.com")
    progress("es9p_initiate_authentication", "rsp.example.com")
    if matching == "OFFLINE":
        progress("es10b_cancel_session")
        return reply(-1, "es9p_initiate_authentication", "HTTP transport failed")
    progress("es10b_authenticate_server", "rsp.example.com")
    progress("es9p_authenticate_client", "rsp.example.com")
    if matching == "REFUSED":
        progress("es10b_cancel_session")
        return reply(-1, "es9p_authenticate_client", "The Matching ID 12345678 was refused")
    n = 3 + len(profiles)
    new = {
        "iccid": "890000000000000000%02d" % (n * 11 % 100),
        "isdpAid": "A0",
        "profileState": "disabled",
        "profileNickname": None,
        "serviceProviderName": "Test Operator C",
        "profileName": "Test C",
        "iconType": "none",
        "icon": None,
        "profileClass": "operational",
    }
    progress(
        "es8p_meatadata_parse",
        {k: new[k] for k in ("iccid", "serviceProviderName", "profileName", "profileClass")},
    )
    progress("es10b_prepare_download", "rsp.example.com")
    progress("es9p_get_bound_profile_package", "rsp.example.com")
    progress("es10b_load_bound_profile_package", "rsp.example.com")
    if matching != "GHOST":
        profiles.append(new)
        save(profiles)
    return reply(data={"seqNumber": 1, "bppCommandId": None, "errorReason": None})


if __name__ == "__main__":
    main(sys.argv[1:])
    sys.exit(0)
