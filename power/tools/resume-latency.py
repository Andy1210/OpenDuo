#!/usr/bin/python3 -I
"""Measure how long a resume takes (on the phone, as root, detached: it
suspends the phone): resume-latency.py SECONDS [--no-panels] [--duo2-autosleep]

One suspend through logind, woken by the PMIC RTC (rtc1) SECONDS (5-120)
ahead. The RTC counts whole seconds: its second edge is located against
the system clock first, so the moment of the wake is known to a few ms.
Then, as the power key would, the panels are switched on at once and the
first new frame on each is waited for; and the WiFi gateway is pinged until
it answers. Prints: wake -> processes thawed, -> panel lit, -> WiFi.
"""

import json
import subprocess
import sys
import time

RTC = "/sys/class/rtc/rtc1/"


def rtc_now():
    with open(RTC + "since_epoch") as f:
        return int(f.read())


def rtc_offset():
    """system time - RTC time, at an RTC second edge (to ~2 ms)."""
    a = rtc_now()
    while True:
        b = rtc_now()
        t = time.time()
        if b != a:
            return t - b
        time.sleep(0.002)


def stats():
    with open("/sys/power/suspend_stats/success") as f:
        s = f.read()
    with open("/sys/power/suspend_stats/fail") as f:
        return s + f.read()


def frames():
    out = subprocess.run(["duoctl", "get_outputs"], capture_output=True, text=True).stdout
    try:
        return {o["name"]: o["frames_presented"] for o in json.loads(out)["ok"]["outputs"]}
    except (ValueError, KeyError):
        return {}


def gw():
    out = subprocess.run(
        ["ip", "-4", "route", "show", "default", "dev", "wlan0"], capture_output=True, text=True
    ).stdout.split()
    return out[out.index("via") + 1] if "via" in out else None


def main():
    secs = int(sys.argv[1])
    assert 5 <= secs <= 120
    panels = "--no-panels" not in sys.argv
    off = rtc_offset()
    wake_rtc = rtc_now() + secs
    wake = wake_rtc + off
    with open(RTC + "wakealarm", "w") as f:
        f.write("0")
    with open(RTC + "wakealarm", "w") as f:
        f.write(str(wake_rtc))
    s0 = stats()
    f0 = frames()
    print(
        "rtc offset %.3f s; wake expected at %s"
        % (off, time.strftime("%H:%M:%S", time.localtime(wake))),
        flush=True,
    )
    subprocess.run(
        [
            "busctl",
            "call",
            "org.freedesktop.login1",
            "/org/freedesktop/login1",
            "org.freedesktop.login1.Manager",
            "Suspend",
            "b",
            "false",
        ],
        check=True,
    )
    while stats() == s0:
        time.sleep(0.02)
    thaw = time.time()
    if thaw < wake - 1:
        print("woke %.1f s BEFORE the RTC alarm (another wake source)" % (wake - thaw))
    print("wake -> processes thawed: %.2f s" % (thaw - wake), flush=True)
    lit = None
    if panels:
        subprocess.run(["duoctl", "set_power", "all", "on"], stdout=subprocess.DEVNULL)
    g = gw()
    wifi = None
    t_end = time.time() + 30
    pending = set(f0) if panels else set()
    while time.time() < t_end and (pending or wifi is None):
        if pending:
            f = frames()
            for n in list(pending):
                if f.get(n, 0) > f0.get(n, 0):
                    pending.discard(n)
            if not pending and lit is None:
                lit = time.time()
                print(
                    "wake -> both panels presenting: %.2f s (thaw -> lit %.2f s)"
                    % (lit - wake, lit - thaw),
                    flush=True,
                )
        if wifi is None:
            g = g or gw()
            if (
                g
                and subprocess.run(
                    ["ping", "-n", "-q", "-c", "1", "-W", "0.3", "-I", "wlan0", g],
                    stdout=subprocess.DEVNULL,
                ).returncode
                == 0
            ):
                wifi = time.time()
                print("wake -> WiFi gateway answers: %.2f s" % (wifi - wake), flush=True)
        time.sleep(0.02)
    if wifi is None:
        print("WiFi: no answer within 30 s")


main()
