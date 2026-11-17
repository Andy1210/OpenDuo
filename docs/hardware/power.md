# Power: sleep, wake sources, charging

## Automatic sleep (`power/duo2-autosleep`)

The daemon listens to duo-wc's event stream (admin socket, as root) and, when
both panels are dark, suspends after `delay` (20 s) with s2idle through
logind (so ModemManager's delay inhibitor and the system-sleep hooks run).
Configuration: `/etc/duo2/autosleep.conf`. Log: `journalctl -u duo2-autosleep`.
Switch: `sudo duo2-autosleep-switch on|off|status|hold 2h` (the existence of
the hold file `/etc/duo2/autosleep-hold` means off; a timed `hold` is for
remote work, a permanent switch is easy to forget).

It does not sleep while: the hold file exists; a call dials, rings or is
active; audio plays or records (any ALSA PCM `RUNNING`/`DRAINING`) or an MPRIS
player is `Playing`; a logind `sleep` inhibitor blocks; a USB host has
configured the gadget; an administrator ssh session is open; a Clocks alarm
is due within `delay + alarm_lead`; the phone is charging
(`stay_awake_charging`); or a test kernel is running (`duo2.try=` on the
command line: a trial kernel must stay reachable). An app that keeps the
screen awake keeps the panels lit, so the daemon does not sleep either;
navigation with dark panels must take a logind sleep inhibitor itself
(`duo2-android-location` does this for Android apps).

After a resume it waits for the WiFi gateway to answer (`wifi_wait`, 45 s),
then `resume_grace` (15 s); a modem-only wake (GLINK message or IPA) gets
`modem_grace` (8 s) without waiting for WiFi, so a ringing call keeps the
phone awake. Every sleep is logged: how long, what woke it, when WiFi
answered.

Lessons that shaped it: the WiFi chip is powered down in sleep and needs
~4.5-7 s to come back, so a grace shorter than that re-suspended the phone
before it was reachable; `RuntimeMaxSec` counts CLOCK_MONOTONIC, which stands
still in sleep. Timers that must run across a sleep use CLOCK_BOOTTIME. There
are no automated sleep loops: test with a single sleep bounded by an RTC
alarm, run through `systemd-run` (your own ssh session would otherwise keep
the phone awake):
`sudo systemd-run --unit=as-test /usr/local/sbin/duo2-autosleep --test-max 60 --ignore-hold --no-ssh-inhibit --delay 5`
(`--dry-run --ignore-hold` only logs).

`power/duo2-try-netwatch` (only with `duo2.try=` on the command line) reboots
into the known-good kernel when a trial kernel has no WiFi gateway 5 minutes
after boot or loses it for 10 minutes.

## Wake sources

| Source | Path |
|---|---|
| RTC (rtcwake, Clocks alarms, `WakeSystem=` timers) | the PMIC RTC (`rtc1`, `pm8xxx_rtc_alarm`); the EFI RTC cannot wake |
| power button | PMIC PON `pmic_pwrkey` |
| volume up | `gpio-keys` (`wakeup-source` in the device tree) |
| modem (call, SMS) | the modem's GLINK edge as `wakeup-source` (kernel patch) |
| charger plug/unplug | battmgr USB/WLS notification as a hard wakeup (kernel patch) |
| WiFi (WoWLAN) | PCIe PME with the link kept up (kernel patch) |

- GLINK interrupts are `IRQF_NO_SUSPEND`, so they run during sleep, but the
  kernel never arms such an IRQ as a wakeup; the patch reports a hard wakeup
  event for every message of a `wakeup-source` edge. The modem is chatty
  (signal, serving cell, network time), so a modem wake can leave the phone
  barely asleep; `power/duo2-qrtr.conf` (`options qrtr wake_services=5,9,400`)
  limits the QMI services whose indications may wake the phone to WMS
  (SMS), Voice (incoming call) and the sensor DSP's sensor service (only the
  sensors `duo-posed` asks for while asleep: opening and closing the phone).
- The SLPI (sensor core) edge is not a wake source: it woke the phone at the
  start of every sleep, because the sensors stream continuously.
- Each edge's wakeup can be switched under its `power/wakeup`;
  `/sys/power/pm_wakeup_irq` names the interrupt that woke the phone.
- Clocks alarms: before a sleep the daemon arms a `CLOCK_REALTIME_ALARM`
  timerfd 45 s before the next ring (the kernel's alarmtimer programs the
  PMIC RTC).
- The EFI RTC's wakeup source stayed active forever and blocked reads of
  `/sys/power/wakeup_count`; a patch releases it when the time read fails.

## WoWLAN

The firmware supports wake on disconnect, magic packet and 22 patterns. By
default the PCIe controller resets the chip (PERST#) when the host
suspends, which loses the WoW state; the kernel patch keeps the link up
(D3hot + L1) while an endpoint is armed to wake. The cost: the PCIe power
domain stays on, so the deepest sleep state is not reached; the gain: ssh
and push reach the phone during sleep and the ~2 s firmware reload at resume
disappears. The test tools are `power/tools/wol.py` and `iw phy0 wowlan`.

## Charging

- `power/duo2-charge-hold` switches the Surface firmware's charge hold
  (Microsoft's optimized-charging hold): on, the firmware keeps the battery
  short of full, for a phone that lives on its charger. The kernel offers it
  as the battery's `charge_types` ("Standard" or "Long Life"; where it holds
  is the firmware's choice). The firmware forgets it on every boot, so udev
  applies the remembered setting when the battery appears. The phone stays
  awake while charging (`stay_awake_charging`).
- `power/duo2-pen-charger` drives the Slim Pen 2 charging transmitter in
  the left half's back cover (Pen Cover); see [touch.md](touch.md).
- `power/duo2-torch-off` makes sure the flash LED (the quick-menu
  flashlight) is off, through the kernel's LED class only.

## Measurements that matter

Resume to usable: processes thawed 3.5 s, both panels show a new frame at
4.0 s, the WiFi gateway answers at 4.3 s (most of it is the WiFi chip's
firmware restart; `power/tools/resume-latency.py`). On the charger the ADSP
and the modem keep the deeper states from being reached, so sleep
consumption must be measured on battery.
