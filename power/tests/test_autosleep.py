"""duo2-autosleep's pure parts: python3 -m unittest discover -s tests
(from power/)."""

import os
import tempfile
import time
import unittest

from test_netwatch import load

asl = load("duo2-autosleep")


class TestPlan(unittest.TestCase):
    def test_lit_never_due(self):
        p = asl.Plan(20, 10)
        p.set_dark(False, 0)
        self.assertIsNone(p.due())

    def test_dark_due_after_delay(self):
        p = asl.Plan(20, 10)
        p.set_dark(True, 100)
        p.set_dark(True, 105)  # still dark: the start stays
        self.assertEqual(p.due(), 120)

    def test_lit_again_resets(self):
        p = asl.Plan(20, 10)
        p.set_dark(True, 100)
        p.set_dark(False, 110)
        p.set_dark(True, 130)
        self.assertEqual(p.due(), 150)

    def test_resume_grace_not_a_new_delay(self):
        p = asl.Plan(20, 10)
        p.set_dark(True, 100)
        p.resumed(500)
        self.assertEqual(p.due(), 510)

    def test_resume_hold_for_alarm(self):
        p = asl.Plan(20, 10)
        p.set_dark(True, 100)
        p.resumed(500, hold_until=700)
        self.assertEqual(p.due(), 700)

    def test_woken_by_power_key_then_dark_again(self):
        p = asl.Plan(20, 10)
        p.set_dark(True, 100)
        p.resumed(500)
        p.set_dark(False, 501)  # the power key lit the panels
        p.set_dark(True, 600)  # idle blanking
        self.assertEqual(p.due(), 620)


class TestAfterResume(unittest.TestCase):
    def test_no_resume_yet(self):
        self.assertIsNone(asl.after_resume(100, None, None, 15, 45))

    def test_waits_for_wifi(self):
        self.assertIn("WiFi", asl.after_resume(110, 100, None, 15, 45))
        # a WiFi answer from before this resume does not count
        self.assertIn("WiFi", asl.after_resume(110, 100, 50, 15, 45))

    def test_grace_from_wifi_back(self):
        self.assertIn("grace", asl.after_resume(110, 100, 104, 15, 45))
        self.assertIsNone(asl.after_resume(120, 100, 104, 15, 45))

    def test_no_wifi_gives_up(self):
        self.assertIn("grace", asl.after_resume(150, 100, None, 15, 45))
        self.assertIsNone(asl.after_resume(161, 100, None, 15, 45))


class TestParsers(unittest.TestCase):
    def test_is_dark(self):
        self.assertTrue(asl.is_dark({"lit": [], "ambient": None}))
        self.assertFalse(asl.is_dark({"lit": ["DSI-1"], "ambient": None}))
        self.assertFalse(asl.is_dark({"lit": [], "ambient": "peek"}))

    def test_an_idle_inhibit_works_through_the_lit_panels(self):
        # A video keeps the panels lit (duo-wc's idle inhibit): not dark, no
        # suspend. With the panels dark (the power key) an inhibitor holds
        # nothing here: audio, a logind sleep inhibitor, ... do.
        self.assertFalse(asl.is_dark({"lit": ["DSI-1"], "ambient": None, "idle_inhibit": ["mpv"]}))
        self.assertTrue(asl.is_dark({"lit": [], "ambient": None, "idle_inhibit": []}))

    def test_clocks_alarms(self):
        text = (
            "[{'name': <'Work'>, 'id': <'a1'>, 'hour': <7>, 'minute': <0>, 'days': <[1, 2]>, "
            "'snooze_minutes': <10>, 'ring_minutes': <5>, 'ring_time': <'2026-11-08T07:00:00+01'>}, "
            "{'name': <'Nap'>, 'id': <'b2'>, 'hour': <6>, 'minute': <30>, "
            "'snooze_time': <'2026-11-08T06:40:00.5+01'>}, {'name': <'Off'>, 'id': <'c3'>}]"
        )
        self.assertEqual(asl.clocks_alarms(text), [1794116400.5, 1794117600.0])
        self.assertEqual(asl.clocks_alarms("@aa{sv} []"), [])

    def test_sleep_blockers(self):
        out = (
            '{"type":"a(ssssuu)","data":[[["sleep","ModemManager","needs to reset","delay",0,1],'
            '["shutdown:sleep","PackageKit","Transaction running","block",0,2],'
            '["idle","mpv","Playing","block",1000,3]]]}'
        )
        self.assertEqual(asl.sleep_blockers(out), ["PackageKit (Transaction running)"])
        self.assertEqual(asl.sleep_blockers(""), [])

    def test_pcm_state(self):
        self.assertEqual(asl.parse_pcm_state("state: RUNNING\n"), "RUNNING")
        self.assertIsNone(asl.parse_pcm_state("closed\n"))

    def test_pcm_running_tree(self):
        with tempfile.TemporaryDirectory() as d:
            for pcm, st in (
                ("pcm0p", "state: RUNNING\n"),
                ("pcm1p", "closed\n"),
                ("pcm0c", "state: PREPARED\n"),
            ):
                os.makedirs(os.path.join(d, "card0", pcm, "sub0"))
                with open(os.path.join(d, "card0", pcm, "sub0", "status"), "w") as f:
                    f.write(st)
            self.assertEqual(asl.pcm_running(d), ["card0/pcm0p"])

    def test_usb_host(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "a600000.usb"))
            with open(os.path.join(d, "a600000.usb", "state"), "w") as f:
                f.write("not attached\n")
            self.assertEqual(asl.usb_host(d), [])
            with open(os.path.join(d, "a600000.usb", "state"), "w") as f:
                f.write("configured\n")
            self.assertEqual(asl.usb_host(d), ["a600000.usb configured"])

    def test_wake_reasons(self):
        before = {
            "169 pmic_arb 6430809 Edge pm8xxx_rtc_alarm": 1,
            "182 pmic_arb 1302614 Edge pmic_pwrkey": 0,
            "250 ipcc 131072 Edge glink-smem": 100,
        }
        after = dict(before)
        after["182 pmic_arb 1302614 Edge pmic_pwrkey"] = 2
        after["250 ipcc 131072 Edge glink-smem"] = 103
        self.assertEqual(
            sorted(asl.wake_reasons(before, after)), ["modem glink +3", "power key +2"]
        )

    def test_source_reasons(self):
        before = {"4080000.remoteproc:glink-edge.IPCRTR.-1.-1": 4, "qcom-battmgr-usb": 7}
        after = {"4080000.remoteproc:glink-edge.IPCRTR.-1.-1": 5, "qcom-battmgr-usb": 7}
        self.assertEqual(asl.source_reasons(before, after), ["modem call/SMS +1"])
        self.assertTrue(asl.modem_only(["modem glink +9", "modem call/SMS +1"]))

    def test_modem_only(self):
        self.assertTrue(asl.modem_only(["modem glink +3"]))
        self.assertTrue(asl.modem_only(["modem glink +1", "modem data (IPA) +1"]))
        self.assertFalse(asl.modem_only(["modem glink +1", "power key +2"]))
        self.assertFalse(asl.modem_only([]))
        # the sensor DSP hands over its held events at every resume: not a reason
        self.assertTrue(asl.modem_only(["modem glink +15", "modem call/SMS +1", "sensor DSP +9"]))
        self.assertFalse(asl.modem_only(["sensor DSP +2"]))
        self.assertFalse(asl.modem_only(["RTC alarm +1", "sensor DSP +2"]))

    def test_request_only(self):
        self.assertTrue(asl.request_only(["modem glink +3", "modem request +1", "sensor DSP +9"]))
        self.assertTrue(asl.modem_only(["modem glink +3", "modem request +1"]))
        self.assertFalse(
            asl.request_only(["modem glink +3", "modem request +1", "modem call/SMS +1"])
        )
        self.assertFalse(asl.request_only(["modem request +1", "modem data (IPA) +1"]))
        self.assertFalse(asl.request_only(["modem glink +3"]))
        self.assertFalse(asl.request_only(["modem request +1", "power key +1"]))

    def test_wake_source_names(self):
        # the kernel's split sources (one match each), and an older kernel's channel source
        before = {
            "4080000.remoteproc:glink-edge.IPCRTR.-1.-1:request": 2,
            "4080000.remoteproc:glink-edge.IPCRTR.-1.-1:indication": 5,
            "5c00000.remoteproc:glink-edge.IPCRTR.-1.-1:indication": 7,
        }
        after = {
            "4080000.remoteproc:glink-edge.IPCRTR.-1.-1:request": 3,
            "4080000.remoteproc:glink-edge.IPCRTR.-1.-1:indication": 5,
            "5c00000.remoteproc:glink-edge.IPCRTR.-1.-1:indication": 9,
        }
        self.assertEqual(asl.source_reasons(before, after), ["modem request +1", "sensor DSP +2"])
        self.assertEqual(
            asl.wake_source_label("4080000.remoteproc:glink-edge.IPCRTR.-1.-1"), "modem call/SMS"
        )
        self.assertIsNone(asl.wake_source_label("rtc-efi.0"))

    def test_wake_grace(self):
        sl = asl.Sleeper.__new__(asl.Sleeper)
        sl.conf = dict(asl.DEFAULTS)
        sl.check_wake = False
        sl.request_wake = True
        self.assertEqual(sl.wake_grace(), asl.DEFAULTS["request_grace"])
        sl.request_wake = False
        self.assertEqual(sl.wake_grace(), asl.DEFAULTS["modem_grace"])

    def test_wake_grace_battery_check(self):
        sl = asl.Sleeper.__new__(asl.Sleeper)
        sl.conf = dict(asl.DEFAULTS)
        sl.request_wake = sl.modem_wake = False
        sl.check_wake = True
        self.assertEqual(sl.wake_grace(), asl.DEFAULTS["request_grace"])
        self.assertTrue(sl.short_wake())

    def test_read_conf(self):
        with tempfile.NamedTemporaryFile("w", delete=False) as f:
            f.write("# c\ndelay = 30\nresume_grace=x\nssh_users = duo2admin,someone\nbogus = 1\n")
        try:
            c = asl.read_conf(f.name)
        finally:
            os.unlink(f.name)
        self.assertEqual(c["delay"], 30)
        self.assertEqual(c["resume_grace"], asl.DEFAULTS["resume_grace"])
        self.assertEqual(c["ssh_users"], "duo2admin,someone")
        self.assertNotIn("bogus", c)


def supplies(d, bat=None, usb=None, wls=None, ucsi=None):
    """A /sys/class/power_supply tree: bat = dict of the battery's files."""

    def put(name, files):
        os.makedirs(os.path.join(d, name), exist_ok=True)
        for k, v in files.items():
            with open(os.path.join(d, name, k), "w") as f:
                f.write("%s\n" % v)

    if bat is not None:
        put("qcom-battmgr-bat", dict({"type": "Battery", "present": 1}, **bat))
    if usb is not None:
        put("qcom-battmgr-usb", {"type": "USB", "online": usb})
    if wls is not None:
        put("qcom-battmgr-wls", {"type": "Wireless", "online": wls})
    if ucsi is not None:
        put("ucsi-source-psy-pmic_glink.ucsi.01", {"type": "USB", "online": ucsi})
    return d


def bat(level, external=False, charge=None, status="Discharging"):
    return {"level": level, "status": status, "charge_mah": charge, "external": external}


class TestBattery(unittest.TestCase):
    def test_read_on_battery(self):
        with tempfile.TemporaryDirectory() as d:
            supplies(
                d,
                bat={"capacity": 14, "status": "Discharging", "charge_counter": 601000},
                usb=0,
                wls=0,
                ucsi=0,
            )
            self.assertEqual(asl.read_battery(d), bat(14, charge=601.0))

    def test_read_charger(self):
        with tempfile.TemporaryDirectory() as d:
            # just plugged in: the battery still says Discharging
            supplies(
                d,
                bat={"capacity": 4, "status": "Discharging", "charge_counter": 281000},
                usb=1,
                wls=0,
            )
            b = asl.read_battery(d)
            self.assertTrue(b["external"])
            self.assertFalse(asl.battery_low(b, 3))
        with tempfile.TemporaryDirectory() as d:
            supplies(d, bat={"capacity": 2, "status": "Charging"}, usb=0, wls=1)
            self.assertTrue(asl.read_battery(d)["external"])
        with tempfile.TemporaryDirectory() as d:
            supplies(d, bat={"capacity": 2, "status": "Charging"}, usb=0, wls=0)
            self.assertTrue(asl.read_battery(d)["external"])

    def test_read_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(asl.read_battery(os.path.join(d, "none"))["level"])
            # the battery manager not answering yet: no capacity file content
            os.makedirs(os.path.join(d, "qcom-battmgr-bat"))
            with open(os.path.join(d, "qcom-battmgr-bat", "type"), "w") as f:
                f.write("Battery\n")
            b = asl.read_battery(d)
            self.assertIsNone(b["level"])
            self.assertFalse(asl.battery_low(b, 3))

    def test_low(self):
        self.assertTrue(asl.battery_low(bat(3), 3))
        self.assertTrue(asl.battery_low(bat(0), 3))
        self.assertFalse(asl.battery_low(bat(4), 3))
        self.assertFalse(asl.battery_low(bat(1, external=True), 3))
        self.assertFalse(asl.battery_low(bat(1), 0))  # switched off

    def test_bound_from_the_margin(self):
        # 300 mA from 3600 mAh: 8.3 %/h; half the margin's time
        self.assertAlmostEqual(asl.battery_bound(bat(15), 3, 300), 12 * 36 / 300.0 * 3600 * 0.5)
        self.assertAlmostEqual(asl.battery_bound(bat(5), 3, 300) / 60, 7.2)
        self.assertAlmostEqual(asl.battery_bound(bat(4), 3, 300), 216.0)
        self.assertEqual(asl.battery_bound(bat(3), 3, 300), asl.BOUND_MIN_S)
        self.assertGreater(asl.battery_bound(bat(80), 3, 300), 4.5 * 3600)
        # a higher drain shortens it, never below the floor
        self.assertLess(asl.battery_bound(bat(15), 3, 600), asl.battery_bound(bat(15), 3, 300))
        self.assertEqual(asl.battery_bound(bat(4), 3, 5000), asl.BOUND_MIN_S)
        # no bound on a charger, without a reading, switched off
        self.assertIsNone(asl.battery_bound(bat(10, external=True), 3, 300))
        self.assertIsNone(asl.battery_bound(bat(None), 3, 300))
        self.assertIsNone(asl.battery_bound(bat(10), 0, 300))

    def test_bound_keeps_it_alive(self):
        # Every sleep ends with a margin left, even at twice the assumed
        # drain: from 33 % (last night's start) down to the threshold, each
        # sleep as long as allowed (the level rounded down, as read).
        level = 33.0
        for _ in range(1000):
            if level <= 3:
                break
            s = asl.battery_bound(bat(int(level)), 3, 300)
            level -= s / 3600.0 * 600 / 36.0  # 600 mA really
            self.assertGreater(level, 0.4)

    def test_drain(self):
        self.assertAlmostEqual(
            asl.sleep_drain(bat(40, charge=1500.0), bat(39, charge=1470.0), 600), 180.0
        )
        self.assertIsNone(asl.sleep_drain(bat(40, charge=1500.0), bat(39, charge=1490.0), 300))
        self.assertIsNone(asl.sleep_drain(bat(40, charge=1500.0), bat(41, True, 1520.0), 3600))
        self.assertIsNone(asl.sleep_drain(bat(40), bat(39, charge=1490.0), 3600))
        self.assertIsNone(asl.sleep_drain(bat(40, charge=1500.0), bat(41, charge=1520.0), 3600))

    def test_check_wake_only(self):
        self.assertTrue(asl.check_wake_only(["RTC alarm +1", "modem glink +4", "sensor DSP +2"]))
        self.assertFalse(asl.check_wake_only(["RTC alarm +1", "power key +2"]))
        self.assertFalse(asl.check_wake_only(["RTC alarm +1", "modem call/SMS +1"]))
        self.assertFalse(asl.check_wake_only(["modem glink +4"]))
        self.assertFalse(asl.check_wake_only([]))


class FakeArgs:
    dry_run = False
    test_max = 0
    poweroff_test = 0


class TestPowerOff(unittest.TestCase):
    """Sleeper.check_battery with the battery, the calls, the notifications
    and systemctl stubbed."""

    def sleeper(self, levels, calls=(), **args):
        sl = asl.Sleeper.__new__(asl.Sleeper)
        sl.a = FakeArgs()
        for k, v in args.items():
            setattr(sl.a, k, v)
        sl.conf = dict(asl.DEFAULTS, user="someone")
        sl.poweroff_at = sl.poweroff_note = None
        sl.call_logged = sl.powering_off = False
        self.events = []
        it = iter(levels)
        self.saved = (
            asl.read_battery,
            asl.calls_active,
            asl.notify,
            asl.close_notification,
            asl.subprocess.run,
            asl.log,
            asl.torch_off,
        )
        asl.torch_off = lambda root=None: []
        asl.read_battery = lambda root=None: next(it)
        asl.calls_active = lambda: list(calls)
        asl.notify = lambda *a, **k: self.events.append(("notify",) + a[1:2]) or 7
        asl.close_notification = lambda user, nid: self.events.append(("close", nid))

        class Done:
            returncode = 0
            stderr = ""

        asl.subprocess.run = lambda argv, **k: self.events.append(("run", argv[:2])) or Done()
        asl.log = lambda msg: None
        return sl

    def tearDown(self):
        if getattr(self, "saved", None):
            (
                asl.read_battery,
                asl.calls_active,
                asl.notify,
                asl.close_notification,
                asl.subprocess.run,
                asl.log,
                asl.torch_off,
            ) = self.saved
            self.saved = None

    def test_dark_powers_off_at_once(self):
        sl = self.sleeper([bat(3)])
        self.assertTrue(sl.check_battery(False, 100))
        self.assertEqual(self.events, [("run", ["systemctl", "poweroff"])])
        self.assertTrue(sl.powering_off)
        self.assertTrue(sl.check_battery(False, 101))  # and no suspend from now on

    def test_lit_notice_then_power_off(self):
        sl = self.sleeper([bat(3), bat(3), bat(2)])
        self.assertTrue(sl.check_battery(True, 100))
        self.assertEqual(self.events, [("notify", "Battery empty")])
        self.assertTrue(sl.check_battery(True, 115))  # still counting down
        self.assertEqual(len(self.events), 1)
        self.assertTrue(sl.check_battery(True, 130))
        self.assertEqual(self.events[-1], ("run", ["systemctl", "poweroff"]))

    def test_charger_calls_it_off(self):
        sl = self.sleeper([bat(3), bat(3, external=True, status="Charging")])
        sl.check_battery(True, 100)
        self.assertFalse(sl.check_battery(True, 110))
        self.assertEqual(self.events, [("notify", "Battery empty"), ("close", 7)])
        self.assertIsNone(sl.poweroff_at)

    def test_not_low(self):
        sl = self.sleeper([bat(4), bat(50), bat(1, external=True), bat(None)])
        for t in (1, 2, 3, 4):
            self.assertFalse(sl.check_battery(True, t))
        self.assertEqual(self.events, [])

    def test_call_first(self):
        sl = self.sleeper([bat(3), bat(2), bat(1)], calls=["active"])
        self.assertTrue(sl.check_battery(False, 1))
        self.assertTrue(sl.check_battery(False, 2))
        self.assertEqual(self.events, [])
        self.assertTrue(sl.check_battery(False, 3))  # 1 %: the call too
        self.assertEqual(self.events, [("run", ["systemctl", "poweroff"])])

    def test_dry_run_and_test_runs_only_log(self):
        for args in ({"dry_run": True}, {"test_max": 60}, {"dry_run": True, "poweroff_test": 99}):
            sl = self.sleeper([bat(3), bat(3)], **args)
            self.assertTrue(sl.check_battery(True, 1))
            self.assertTrue(sl.check_battery(True, 100))
            self.assertEqual(self.events, [], args)
            self.assertFalse(sl.powering_off)
            self.tearDown()

    def test_torch_off_before_power_off(self):
        sl = self.sleeper([bat(3)])
        asl.torch_off = lambda root=None: self.events.append(("torch_off",)) or ["white:flash"]
        sl.check_battery(False, 1)
        self.assertEqual(self.events, [("torch_off",), ("run", ["systemctl", "poweroff"])])

    def test_dry_run_leaves_the_torch(self):
        sl = self.sleeper([bat(3)], dry_run=True)
        asl.torch_off = lambda root=None: self.events.append(("torch_off",)) or []
        sl.check_battery(False, 1)
        self.assertEqual(self.events, [])

    def test_poweroff_test_is_real(self):
        sl = self.sleeper([bat(80)], poweroff_test=99)
        sl.conf["poweroff_level"] = 99
        sl.check_battery(False, 1)
        self.assertEqual(self.events, [("run", ["systemctl", "poweroff"])])


class TestWaitSuspend(unittest.TestCase):
    """wait_suspend over a scripted (success, fail) sequence and a fake
    clock: every read takes 0.2 s."""

    def wait(self, seq):
        t = [0.0]
        reads = iter(seq)
        last = [seq[0]]

        def read():
            last[0] = next(reads, last[0])
            return last[0]

        def nap(s):
            t[0] += s

        return asl.wait_suspend((10, 1), read=read, clock=lambda: t[0], nap=nap)

    def test_slept(self):
        self.assertEqual(self.wait([(10, 1), (10, 1), (11, 1)]), ((11, 1), "slept"))

    def test_first_state_failed_then_the_retry(self):
        # mem -> -EBUSY, then freeze went through: one sleep, not a failure
        self.assertEqual(self.wait([(10, 1), (10, 2), (10, 2), (11, 2)]), ((11, 2), "retried"))

    def test_failure_and_success_seen_together(self):
        self.assertEqual(self.wait([(11, 2)]), ((11, 2), "retried"))

    def test_failed_without_a_retry(self):
        self.assertEqual(self.wait([(10, 1), (10, 2)]), ((10, 2), "failed"))

    def test_nothing(self):
        self.assertEqual(self.wait([(10, 1)]), ((10, 1), "nothing"))


# --- WiFi in sleep (WoWLAN) -------------------------------------------------

import json as _json
import socket as _socket
import struct as _struct

# documentation addresses only (RFC 5737 / RFC 3849), a locally administered MAC
MAC = "02:00:5e:10:00:01"
V4 = "192.0.2.50"
V6 = "2001:db8:1:2:aaaa:bbbb:cccc:dddd"


def ip_json(extra=()):
    addrs = [
        {"family": "inet", "local": V4, "prefixlen": 24, "scope": "global"},
        {"family": "inet6", "local": V6, "prefixlen": 64, "scope": "global"},
        {
            "family": "inet6",
            "local": "2001:db8:1:2::77",
            "prefixlen": 64,
            "scope": "global",
            "deprecated": True,
        },
        {
            "family": "inet6",
            "local": "2001:db8:9:9::1",
            "prefixlen": 64,
            "scope": "global",
            "tentative": True,
        },
        {"family": "inet6", "local": "fe80::1", "prefixlen": 64, "scope": "link"},
    ] + list(extra)
    return _json.dumps([{"ifname": "wlan0", "addr_info": addrs}])


def matches(pattern, frame):
    """Does an iw WoWLAN pattern (OFFSET+xx:-:...) match an Ethernet frame?"""
    off, cells = pattern.split("+", 1)
    off = int(off)
    for i, c in enumerate(cells.split(":")):
        if c == "-":
            continue
        if off + i >= len(frame) or frame[off + i] != int(c, 16):
            return False
    return True


def eth(ethertype, payload, dst=MAC):
    return (
        bytes.fromhex(dst.replace(":", ""))
        + bytes.fromhex("020000000002")
        + _struct.pack("!H", ethertype)
        + payload
    )


def ipv4(proto, dst, payload):
    h = _struct.pack(
        "!BBHHHBBH4s4s",
        0x45,
        0,
        20 + len(payload),
        0,
        0,
        64,
        proto,
        0,
        _socket.inet_pton(_socket.AF_INET, "198.51.100.7"),
        _socket.inet_pton(_socket.AF_INET, dst),
    )
    return eth(0x0800, h + payload)


def ipv6(nh, dst, payload):
    h = _struct.pack(
        "!IHBB16s16s",
        6 << 28,
        len(payload),
        nh,
        64,
        _socket.inet_pton(_socket.AF_INET6, "2001:db8:ffff::5"),
        _socket.inet_pton(_socket.AF_INET6, dst),
    )
    return eth(0x86DD, h + payload)


TCP = _struct.pack("!HHIIBBHHH", 443, 40000, 1, 1, 0x50, 0x18, 1000, 0, 0)
ECHO = bytes([8, 0, 0, 0, 0, 1, 0, 1])
ECHO6 = bytes([128, 0, 0, 0, 0, 1, 0, 1])


class TestWowlan(unittest.TestCase):
    def addrs(self):
        return asl.parse_addrs(ip_json())

    def test_parse_addrs(self):
        v4, v6 = self.addrs()
        self.assertEqual(v4, [_socket.inet_pton(_socket.AF_INET, V4)])
        # one /64 for the stable and the deprecated address, none for the
        # tentative one or the link-local one
        self.assertEqual(v6, [_socket.inet_pton(_socket.AF_INET6, V6)[:8]])
        self.assertEqual(asl.parse_addrs(""), ([], []))
        self.assertEqual(asl.parse_addrs("not json"), ([], []))

    def args(self, wake):
        v4, v6 = self.addrs()
        return asl.wowlan_args("phy0", wake, MAC, v4, v6)

    def pats(self, wake):
        argv, _ = self.args(wake)
        return argv[argv.index("patterns") + 1 :] if "patterns" in argv else []

    def test_tcp_patterns_match_tcp_to_us_only(self):
        p = self.pats("tcp")
        self.assertEqual(len(p), 2)
        to_us = [ipv4(6, V4, TCP), ipv6(6, V6, TCP), ipv6(6, "2001:db8:1:2::77", TCP)]
        not_us = [
            ipv4(6, "192.0.2.51", TCP),
            ipv4(17, V4, TCP),
            ipv6(17, V6, TCP),
            ipv6(6, "2001:db8:1:3::1", TCP),
            ipv4(1, V4, ECHO),
        ]
        for f in to_us:
            self.assertTrue(any(matches(x, f) for x in p))
        for f in not_us:
            self.assertFalse(any(matches(x, f) for x in p))

    def test_ping_patterns(self):
        p = self.pats("ping")
        self.assertTrue(any(matches(x, ipv4(1, V4, ECHO)) for x in p))
        self.assertTrue(any(matches(x, ipv6(0x3A, V6, ECHO6)) for x in p))
        # an echo reply, or TCP, does not
        self.assertFalse(any(matches(x, ipv4(1, V4, bytes([0]) + ECHO[1:])) for x in p))
        self.assertFalse(any(matches(x, ipv4(6, V4, TCP)) for x in p))

    def test_unicast_pattern(self):
        p = self.pats("unicast")
        self.assertEqual(p, ["0+" + MAC])
        self.assertTrue(matches(p[0], ipv4(17, V4, TCP)))
        self.assertFalse(matches(p[0], eth(0x0806, b"\0" * 28, dst="ff:ff:ff:ff:ff:ff")))

    def test_within_the_firmware_limits(self):
        # ath11k on the WCN6851: patterns of 1-134 bytes at offsets <= 114
        for x in self.pats("tcp,ping,unicast"):
            off, cells = x.split("+", 1)
            self.assertLessEqual(int(off), 114)
            self.assertLessEqual(len(cells.split(":")), 134)

    def test_flags_and_summary(self):
        argv, what = self.args("tcp,disconnect,magic")
        self.assertEqual(
            argv[:7], ["iw", "phy", "phy0", "wowlan", "enable", "disconnect", "magic-packet"]
        )
        self.assertEqual(argv[7], "patterns")
        self.assertIn("tcp (1 IPv4, 1 IPv6 /64)", what)
        # the log line carries no address
        self.assertNotIn("192", what)
        self.assertNotIn("db8", what)
        self.assertNotIn(MAC, what)

    def test_flags_only(self):
        argv, _ = asl.wowlan_args("phy0", "disconnect", MAC, [], [])
        self.assertEqual(argv, ["iw", "phy", "phy0", "wowlan", "enable", "disconnect"])

    def test_nothing_to_wake_for(self):
        argv, why = asl.wowlan_args("phy0", "tcp,ping", MAC, [], [])
        self.assertIsNone(argv)
        self.assertIn("nothing", why)
        argv, why = asl.wowlan_args("phy0", "bogus", MAC, [], [])
        self.assertIsNone(argv)
        self.assertIn("bogus", why)

    def test_pattern_cap(self):
        v4 = [bytes([192, 0, 2, i]) for i in range(30)]
        argv, what = asl.wowlan_args("phy0", "tcp", MAC, v4, [])
        self.assertEqual(len(argv) - argv.index("patterns") - 1, asl.WOWLAN_MAX_PATTERNS)
        self.assertIn("cut", what)

    def test_shown(self):
        self.assertTrue(asl.wowlan_shown("WoWLAN is enabled:\n * wake up on disconnect\n"))
        self.assertFalse(asl.wowlan_shown("WoWLAN is disabled.\n"))
        self.assertIsNone(asl.wowlan_shown(""))

    def test_verdict(self):
        self.assertIsNone(asl.wowlan_verdict(None, None, 100, 45))
        self.assertIsNone(asl.wowlan_verdict(100, None, 120, 45))
        self.assertEqual(asl.wowlan_verdict(100, 101.5, 102, 45), "ok")
        # an answer from before this resume does not count
        self.assertIsNone(asl.wowlan_verdict(100, 90, 120, 45))
        self.assertEqual(asl.wowlan_verdict(100, 90, 145, 45), "failed")

    def test_wifi_only(self):
        self.assertTrue(asl.wifi_only(["WiFi (PCIe PME) +1", "modem glink +3", "sensor DSP +2"]))
        self.assertTrue(asl.wifi_only(["WiFi (WAKE#) +1"]))
        self.assertFalse(asl.wifi_only(["WiFi (PCIe PME) +1", "RTC alarm +1"]))
        self.assertFalse(asl.wifi_only(["modem glink +3"]))
        self.assertFalse(asl.wifi_only([]))

    def test_wake_irq_names(self):
        before = {
            "207 DW-PCI-MSI-0000:00:00.0 0 Edge PCIe PME, aerdrv": 1,
            "251 msmgpio 96 Level 0000:00:00.0:wakeup": 0,
        }
        after = {
            "207 DW-PCI-MSI-0000:00:00.0 0 Edge PCIe PME, aerdrv": 2,
            "251 msmgpio 96 Level 0000:00:00.0:wakeup": 1,
        }
        r = asl.wake_reasons(before, after)
        self.assertIn("WiFi (PCIe PME) +1", r)
        self.assertIn("WiFi (WAKE#) +1", r)


class TestOverrides(unittest.TestCase):
    def test_cmdline(self):
        ov = asl.cmdline_overrides(
            "root=PARTLABEL=linux duo2.try=7.3.3 duo2.autosleep.wifi_sleep=wowlan "
            "duo2.autosleep.wowlan_wake=tcp,ping duo2.autosleep.on_try=1 "
            "duo2.autosleep.= duo2.autosleepx=1"
        )
        self.assertEqual(ov, {"wifi_sleep": "wowlan", "wowlan_wake": "tcp,ping", "on_try": "1"})

    def test_set_conf_types(self):
        c = dict(asl.DEFAULTS)
        self.assertTrue(asl.set_conf(c, "wake_every", "600"))
        self.assertEqual(c["wake_every"], 600)
        self.assertFalse(asl.set_conf(c, "wake_every", "soon"))
        self.assertEqual(c["wake_every"], 600)
        self.assertTrue(asl.set_conf(c, "wifi_sleep", "wowlan"))
        self.assertFalse(asl.set_conf(c, "on_try", "1"))

    def test_run_conf_over_conf(self):
        with tempfile.TemporaryDirectory() as d:
            a, b = os.path.join(d, "a.conf"), os.path.join(d, "b.conf")
            with open(a, "w") as f:
                f.write("wake_every = 0\nwifi_sleep = off\ndelay = 30\n")
            with open(b, "w") as f:
                f.write("wake_every = 780\nwifi_sleep = wowlan\n")
            c = asl.read_conf(b, asl.read_conf(a))
        self.assertEqual((c["wake_every"], c["wifi_sleep"], c["delay"]), (780, "wowlan", 30))

    def test_read_conf_new_keys(self):
        with tempfile.NamedTemporaryFile("w", suffix=".conf", delete=False) as f:
            f.write(
                "wifi_sleep = wowlan\nwowlan_wake = tcp,ping,disconnect\nwifi_sync_every = 900\n"
                "sync_hook = /usr/local/sbin/duo2-push-poke\n"
            )
        try:
            c = asl.read_conf(f.name)
        finally:
            os.unlink(f.name)
        self.assertEqual(c["wifi_sleep"], "wowlan")
        self.assertEqual(c["wowlan_wake"], "tcp,ping,disconnect")
        self.assertEqual(c["wifi_sync_every"], 900)
        self.assertEqual(c["sync_hook"], "/usr/local/sbin/duo2-push-poke")


class TestWifiSleep(unittest.TestCase):
    """WifiSleep with the system stubbed: iw, ip, sysfs."""

    def setUp(self):
        self.calls = []
        self.iw_ok = True
        self.shown = "WoWLAN is disabled.\n"
        self.up = True
        self.saved = (
            asl.run,
            asl.subprocess.run,
            asl.wlan_up,
            asl.wlan_phy,
            asl._readline,
            asl.log,
        )
        self.logs = []
        t = self

        def fake_run(argv, user=None, timeout=5, env=None):
            t.calls.append(list(argv))
            if argv[:2] == ["ip", "-j"]:
                return ip_json()
            if argv[-1:] == ["show"]:
                return t.shown
            return ""

        class P:
            def __init__(self, rc):
                self.returncode = rc
                self.stderr = "" if rc == 0 else "command failed: Operation not supported (-95)"

        def fake_sub(argv, **k):
            t.calls.append(list(argv))
            return P(0 if t.iw_ok else 1)

        asl.run = fake_run
        asl.subprocess.run = fake_sub
        asl.wlan_up = lambda: t.up
        asl.wlan_phy = lambda: "phy0"
        asl._readline = lambda path: MAC if path.endswith("address") else ""
        asl.log = lambda msg: t.logs.append(msg)
        self.rp = []
        self.ws = asl.WifiSleep(dict(asl.DEFAULTS))
        self.ws._root_port = lambda on: t.rp.append(on)

    def tearDown(self):
        (asl.run, asl.subprocess.run, asl.wlan_up, asl.wlan_phy, asl._readline, asl.log) = (
            self.saved
        )

    def iw_enables(self):
        return [c for c in self.calls if c[:5] == ["iw", "phy", "phy0", "wowlan", "enable"]]

    def test_off_mode(self):
        self.assertEqual(self.ws.prepare(), "off")
        self.assertEqual(self.iw_enables(), [])
        self.assertEqual(self.rp, [False])
        self.assertFalse(self.ws.armed)

    def test_off_mode_switches_a_leftover_off(self):
        self.shown = "WoWLAN is enabled:\n * wake up on disconnect\n"
        self.ws.prepare()
        self.assertIn(["iw", "phy", "phy0", "wowlan", "disable"], self.calls)

    def test_not_on_wifi(self):
        self.ws.conf["wifi_sleep"] = "wowlan"
        self.up = False
        self.assertIsNone(self.ws.prepare())
        self.assertEqual(self.iw_enables(), [])

    def test_wowlan_armed(self):
        self.ws.conf["wifi_sleep"] = "wowlan"
        self.assertEqual(self.ws.prepare(), "wowlan")
        self.assertEqual(len(self.iw_enables()), 1)
        self.assertEqual(self.rp, [True])
        self.assertTrue(self.ws.armed)
        # nothing of the addresses in the log
        self.assertFalse(any(V4 in m or MAC in m for m in self.logs))

    def test_iw_refuses(self):
        self.ws.conf["wifi_sleep"] = "wowlan"
        self.iw_ok = False
        self.assertEqual(self.ws.prepare(), "off")
        self.assertFalse(self.ws.armed)
        self.assertEqual(self.rp, [False])

    def test_bad_mode_is_off(self):
        self.ws.conf["wifi_sleep"] = "maybe"
        self.assertEqual(self.ws.mode(), "off")

    def test_failures_switch_it_off(self):
        self.ws.conf["wifi_sleep"] = "wowlan"
        for i in range(2):
            self.ws.prepare()
            self.ws.judge(100, None, 120)  # too early: nothing
            self.assertTrue(self.ws.pending)
            self.ws.judge(100, None, 146)
            self.assertFalse(self.ws.pending)
        self.assertTrue(self.ws.off)
        self.assertEqual(self.ws.mode(), "off")
        self.assertEqual(self.ws.prepare(), "off")
        self.assertEqual(len(self.iw_enables()), 2)

    def test_a_good_sleep_resets_the_count(self):
        self.ws.conf["wifi_sleep"] = "wowlan"
        self.ws.prepare()
        self.ws.judge(100, None, 146)
        self.ws.prepare()
        self.ws.judge(200, 201, 202)
        self.ws.prepare()
        self.ws.judge(300, None, 346)
        self.assertEqual(self.ws.failures, 1)
        self.assertFalse(self.ws.off)

    def test_undo_only_what_we_set(self):
        self.ws.undo()
        self.assertEqual(self.rp, [])
        self.ws.conf["wifi_sleep"] = "wowlan"
        self.ws.prepare()
        self.shown = "WoWLAN is enabled:\n"
        self.ws.undo()
        self.assertIn(["iw", "phy", "phy0", "wowlan", "disable"], self.calls)


class TestResumeGrace(unittest.TestCase):
    def sleeper(self):
        sl = asl.Sleeper.__new__(asl.Sleeper)
        sl.conf = dict(asl.DEFAULTS)
        sl.wifi_wake = sl.sync_wake = False
        return sl

    def test_grace_by_wake(self):
        sl = self.sleeper()
        self.assertEqual(sl.resume_grace(), sl.conf["resume_grace"])
        sl.wifi_wake = True
        self.assertEqual(sl.resume_grace(), sl.conf["wifi_grace"])
        sl.wifi_wake, sl.sync_wake = False, True
        self.assertEqual(sl.resume_grace(), sl.conf["sync_grace"])


if __name__ == "__main__":
    unittest.main()


class TestTorch(unittest.TestCase):
    def leds(self, d, **brightness):
        for name, b in brightness.items():
            name = name.replace("_", ":")
            os.makedirs(os.path.join(d, name))
            with open(os.path.join(d, name, "brightness"), "w") as f:
                f.write("%s\n" % b)

    def test_lit(self):
        with tempfile.TemporaryDirectory() as d:
            self.leds(d, white_flash=255, input3__capslock=1, mmc0__=0)
            self.assertEqual(asl.torch_lit(d), ["white:flash"])

    def test_dark_and_missing(self):
        with tempfile.TemporaryDirectory() as d:
            self.leds(d, white_flash=0, yellow_torch=0)
            self.assertEqual(asl.torch_lit(d), [])
            self.assertEqual(asl.torch_lit(os.path.join(d, "nope")), [])

    def test_off(self):
        with tempfile.TemporaryDirectory() as d:
            self.leds(d, white_flash=255, white_torch=0, input3__capslock=1)
            self.assertEqual(asl.torch_off(d), ["white:flash"])
            with open(os.path.join(d, "white:flash", "brightness")) as f:
                self.assertEqual(f.read().strip(), "0")
            with open(os.path.join(d, "input3::capslock", "brightness")) as f:
                self.assertEqual(f.read().strip(), "1")
            self.assertEqual(asl.torch_lit(d), [])


class TestTimedHold(unittest.TestCase):
    def test_read(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "until")
            self.assertIsNone(asl.read_hold_until(p))
            with open(p, "w") as f:
                f.write("1763052012\n# duo2-autosleep keeps the phone awake until ...\n")
            self.assertEqual(asl.read_hold_until(p), 1763052012.0)
            for bad in ("", "\n", "soon\n", "# a comment first\n1763052012\n"):
                with open(p, "w") as f:
                    f.write(bad)
                self.assertIsNone(asl.read_hold_until(p), bad)

    def test_left(self):
        now = 1_000_000
        self.assertEqual(asl.hold_left(None, now), 0)
        self.assertEqual(asl.hold_left(now + 7200, now), 7200)
        self.assertEqual(asl.hold_left(now, now), 0)  # lapsed this second
        self.assertEqual(asl.hold_left(now - 5, now), 0)
        # an end further ahead than the longest hold: a clock that was wrong
        self.assertEqual(
            asl.hold_left(now + asl.HOLD_MAX_S + asl.HOLD_SLACK_S, now),
            asl.HOLD_MAX_S + asl.HOLD_SLACK_S,
        )
        self.assertEqual(asl.hold_left(now + asl.HOLD_MAX_S + asl.HOLD_SLACK_S + 1, now), 0)
        self.assertEqual(asl.hold_left(now + 30 * 86400, now), 0)

    def test_text(self):
        now = 1_000_000
        self.assertEqual(asl.hold_text(None, now), "none")
        self.assertIn("2 h 0 min more", asl.hold_text(now + 7200, now))
        self.assertIn("3 min", asl.hold_text(now + 180, now))
        self.assertTrue(asl.hold_text(now - 60, now).startswith("lapsed at "))
        self.assertTrue(asl.hold_text(now + 3 * 86400, now).startswith("ignored"))

    def test_duration_text(self):
        self.assertEqual(asl.duration_text(45), "45 s")
        self.assertEqual(asl.duration_text(190), "3 min 10 s")
        self.assertEqual(asl.duration_text(1500), "25 min")
        self.assertEqual(asl.duration_text(6730), "1 h 52 min")


class TestChargingHold(unittest.TestCase):
    ON = dict(asl.DEFAULTS)
    OFF = dict(asl.DEFAULTS, stay_awake_charging="off")

    def test_default_on(self):
        self.assertEqual(asl.DEFAULTS["stay_awake_charging"], "on")

    def test_external_holds(self):
        for b in (
            bat(50, external=True, status="Charging"),
            bat(100, external=True, status="Discharging"),  # full on the charger reads so
            bat(80, external=True, status="Not charging"),  # the charge hold at 80 %
            bat(None, external=True),
        ):
            held, why = asl.charging_hold(self.ON, b)
            self.assertTrue(held, b)
            self.assertIn("external power", why)

    def test_battery_or_off(self):
        self.assertEqual(asl.charging_hold(self.ON, bat(50)), (False, None))
        self.assertEqual(
            asl.charging_hold(self.OFF, bat(50, external=True, status="Charging")), (False, None)
        )
        for v in ("no", "0", "false", "OFF"):
            self.assertFalse(
                asl.charging_hold(
                    dict(asl.DEFAULTS, stay_awake_charging=v), bat(50, external=True)
                )[0]
            )

    def test_weak_source_lets_it_sleep(self):
        held, why = asl.charging_hold(self.ON, bat(8, external=True, status="Discharging"))
        self.assertFalse(held)
        self.assertIn("not charging at 8 %", why)
        self.assertFalse(
            asl.charging_hold(self.ON, bat(10, external=True, status="Not charging"))[0]
        )
        # charging, even slowly: it holds
        self.assertTrue(asl.charging_hold(self.ON, bat(5, external=True, status="Charging"))[0])
        self.assertTrue(asl.charging_hold(self.ON, bat(11, external=True, status="Discharging"))[0])


class Args:
    def __init__(self, **kw):
        self.dry_run = self.ignore_hold = self.no_ssh_inhibit = False
        self.test_max = self.poweroff_test = 0
        self.delay = self.resume_grace = self.wifi_sleep = self.wowlan_wake = self.charging_hold = (
            None
        )
        self.__dict__.update(kw)


class TestLoadConf(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.conf = os.path.join(self.d.name, "autosleep.conf")
        self.run = os.path.join(self.d.name, "run.conf")

    def tearDown(self):
        self.d.cleanup()

    def load(self, a=None, cmdline=""):
        return asl.load_conf(a, conf_path=self.conf, run_conf=self.run, cmdline=cmdline)

    def test_layers(self):
        with open(self.conf, "w") as f:
            f.write("delay = 30\nstay_awake_charging = off\nwake_every = 0\n")
        with open(self.run, "w") as f:
            f.write("wake_every = 120\n")
        c, on_try = self.load(
            cmdline="duo2.autosleep.stay_awake_charging=on duo2.autosleep.on_try=1"
        )
        self.assertEqual(
            (c["delay"], c["wake_every"], c["stay_awake_charging"], on_try), (30, 120, "on", True)
        )
        c, _ = self.load(Args(charging_hold="off", delay=0))
        self.assertEqual((c["stay_awake_charging"], c["delay"]), ("off", 0))

    def test_test_suspend_leaves_the_charger_out(self):
        c, _ = self.load(Args(test_max=60))
        self.assertEqual(c["stay_awake_charging"], "off")
        c, _ = self.load(Args(test_max=60, charging_hold="on"))
        self.assertEqual(c["stay_awake_charging"], "on")
        c, _ = self.load(Args(dry_run=True))  # a dry run decides as usual
        self.assertEqual(c["stay_awake_charging"], "on")


class TestReasons(unittest.TestCase):
    """Sleeper.reasons with the system stubbed: the holds and their order,
    the torch, unplugging, the settings read again."""

    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        p = lambda n: os.path.join(self.d.name, n)  # noqa: E731
        self.saved = {
            k: getattr(asl, k)
            for k in (
                "HOLD",
                "HOLD_UNTIL",
                "CONF",
                "RUN_CONF",
                "read_battery",
                "torch_lit",
                "calls_active",
                "pcm_running",
                "mpris_playing",
                "logind_blockers",
                "usb_host",
                "ssh_sessions",
                "next_clocks_alarm",
                "log",
                "read_cmdline",
            )
        }
        asl.HOLD, asl.HOLD_UNTIL, asl.CONF, asl.RUN_CONF = (
            p("hold"),
            p("until"),
            p("autosleep.conf"),
            p("run.conf"),
        )
        self.battery = bat(60)
        self.torch = []
        self.logs = []
        asl.read_battery = lambda root=None: self.battery
        asl.torch_lit = lambda root=None: list(self.torch)
        for k in ("calls_active", "pcm_running", "logind_blockers", "usb_host"):
            setattr(asl, k, lambda *a, **kw: [])
        asl.mpris_playing = asl.ssh_sessions = lambda *a: []
        asl.next_clocks_alarm = lambda *a: None
        asl.log = self.logs.append
        asl.read_cmdline = lambda: ""
        self.write_conf("on")

    def tearDown(self):
        for k, v in self.saved.items():
            setattr(asl, k, v)
        self.d.cleanup()

    def write_conf(self, charging):
        with open(asl.CONF, "w") as f:
            f.write("delay = 20\nstay_awake_charging = %s\n" % charging)

    def sleeper(self, **args):
        a = Args(**args)
        conf, _ = asl.load_conf(a)
        sl = asl.Sleeper.__new__(asl.Sleeper)
        sl.a, sl.conf = a, conf
        sl.users = {"duo2admin"}
        sl.try_boot = sl.modem_wake = sl.check_wake = sl.request_wake = False
        sl.wifi_wake = sl.sync_wake = False
        sl.resume_t = sl.wifi_back = None
        sl.wifi = asl.WifiSleep(conf)
        sl.stamp = asl.conf_stamp()
        return sl

    def hold_until(self, end):
        with open(asl.HOLD_UNTIL, "w") as f:
            f.write("%d\n# test\n" % end)

    def test_charger_holds_unplugging_releases(self):
        sl = self.sleeper()
        self.battery = bat(60, external=True, status="Charging")
        self.assertEqual(sl.reasons(1000), ["on external power (stay awake while charging)"])
        self.battery = bat(60)  # unplugged: the normal rules at the next check
        self.assertEqual(sl.reasons(1010), [])

    def test_torch_with_and_without_the_charger(self):
        sl = self.sleeper()
        self.torch = ["white:flash"]
        self.assertEqual(sl.reasons(1000), ["torch white:flash"])
        self.battery = bat(60, external=True, status="Charging")
        self.assertEqual(sl.reasons(1000), ["on external power (stay awake while charging)"])
        self.write_conf("off")
        self.assertEqual(sl.reasons(1000), ["torch white:flash"])
        self.torch = []
        self.assertEqual(sl.reasons(1000), [])

    def test_timed_hold_and_its_lapse(self):
        sl = self.sleeper()
        now = time.time()
        self.hold_until(now + 180)
        r = sl.reasons(now)
        self.assertEqual(len(r), 1)
        self.assertTrue(r[0].startswith("timed hold until "), r)
        self.assertEqual(sl.reasons(now + 170), r)  # still held, said the same (logged once)
        self.assertEqual(sl.reasons(now + 181), [])  # lapsed: it may sleep
        # both: each is said
        self.battery = bat(60, external=True, status="Charging")
        self.assertEqual(len(sl.reasons(now + 10)), 2)

    def test_a_wrong_clock_does_not_hold_forever(self):
        sl = self.sleeper()
        self.hold_until(time.time() + 40 * 86400)
        self.assertEqual(sl.reasons(time.time()), [])

    def test_hold_file_first(self):
        sl = self.sleeper()
        with open(asl.HOLD, "w") as f:
            f.write("held\n")
        self.battery = bat(60, external=True, status="Charging")
        self.hold_until(time.time() + 600)
        self.assertEqual(sl.reasons(time.time()), ["hold file %s" % asl.HOLD])
        # a test run ignores the hold file, not a timed hold (another agent's)
        sl = self.sleeper(ignore_hold=True, dry_run=True)
        self.assertTrue(sl.reasons(time.time())[0].startswith("timed hold"))

    def test_setting_read_again(self):
        sl = self.sleeper()
        self.battery = bat(60, external=True, status="Charging")
        self.assertTrue(sl.reasons(1000))
        self.write_conf("off")  # the switch / Duo Settings
        self.assertEqual(sl.reasons(1000), [])
        self.assertTrue(any("stay_awake_charging = off" in m for m in self.logs), self.logs)
        self.write_conf("on")
        self.assertTrue(sl.reasons(1000))

    def test_test_suspend_on_the_charger(self):
        sl = self.sleeper(test_max=60, ignore_hold=True, no_ssh_inhibit=True)
        self.battery = bat(60, external=True, status="Charging")
        self.assertEqual(sl.reasons(1000), [])
        self.write_conf("off")
        self.write_conf("on")  # a reload keeps the run's choice
        self.assertEqual(sl.reasons(1000), [])

    def test_weak_source_sleeps(self):
        sl = self.sleeper()
        self.battery = bat(8, external=True, status="Discharging")
        self.assertEqual(sl.reasons(1000), [])


class TestLowBatteryWithHolds(unittest.TestCase):
    """The empty-battery power-off does not ask the holds: on battery at the
    threshold it powers off under a timed hold or a lit torch; a charger
    calls it off (and then the charging hold keeps the phone up)."""

    sleeper = TestPowerOff.sleeper
    tearDown = TestPowerOff.tearDown

    def test_timed_hold_and_torch_do_not_stop_it(self):
        with tempfile.TemporaryDirectory() as d:
            saved = asl.HOLD_UNTIL
            asl.HOLD_UNTIL = os.path.join(d, "until")
            try:
                with open(asl.HOLD_UNTIL, "w") as f:
                    f.write("%d\n" % (time.time() + 3600))
                sl = self.sleeper([bat(3)])
                asl.torch_off = lambda root=None: (
                    self.events.append(("torch_off",)) or ["white:flash"]
                )
                self.assertTrue(sl.check_battery(False, 1))
                self.assertEqual(self.events, [("torch_off",), ("run", ["systemctl", "poweroff"])])
            finally:
                asl.HOLD_UNTIL = saved

    def test_charger_at_the_threshold(self):
        b = bat(3, external=True, status="Charging")
        sl = self.sleeper([b])
        self.assertFalse(sl.check_battery(False, 1))
        self.assertEqual(self.events, [])
        self.assertTrue(asl.charging_hold(sl.conf, b)[0])
