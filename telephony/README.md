# telephony: modem, SIM slots, calls, SMS, mobile data

The Surface Duo 2's modem (SM8350 MPSS, `MPSS.HI.4.0`) is reached over QRTR
(node 0); the data path is the IPA network device `rmnet_ipa0`. Background and
measurements are in the sections below.

```
rmtfs (starts the modem) ──> duo2-modem-lpm ──> ModemManager (qcom-soc plugin:
                                                 qrtr0 + rmnet_ipa0)
                                                   │ D-Bus
             duo2-wwan (enable, SIM slot, data) ───┤
             GNOME Calls, Chatty (+ mmsd-tng) ─────┘
```

| File | Installed as | What |
|------|--------------|------|
| `duo2-modem-lpm` + `systemd/duo2-modem-lpm.service` | `/usr/local/sbin`, `/etc/systemd/system` | the modem comes up in the DMS mode `shutting-down` and waits for the AP's first request (Android's RIL makes it); ModemManager cannot read that mode, so it is put into `low-power` after every start of rmtfs, before ModemManager probes |
| `systemd/ModemManager-duo2.conf` | `ModemManager.service.d/duo2.conf` | ModemManager after the hook (both ports present) |
| `duo2-sim` | `/usr/local/sbin` | `status` (no identifiers printed), `primary 1\|2`, `auto` |
| `duo2-wwan` + `systemd/duo2-wwan.service` + `wwan.conf` | `/usr/local/sbin`, `/etc/duo2/wwan.conf` | enables a modem with a ready SIM once, picks a SIM slot when none is primary, connects data and configures the bearer's device (WiFi first), airplane mode, the reject cause #7 radio cycle, smart 5G; no NetworkManager |
| `duo2-gnss-assist` + `systemd/duo2-gnss-assist.{service,timer}` | `/usr/local/sbin`, `/etc/systemd/system` (timer NOT enabled) | XTRA assistance data for the GNSS engine: downloads the file from the server the modem names (WiFi only) and injects it through ModemManager (`mmcli --location-inject-assistance-data`); no NV item, no SUPL server set; `status` is read-only. docs/hardware/location.md |
| `tests/test_gnss_assist.py` | (not installed) | its pure parts |
| `duo2-sms-storage` | `/usr/local/sbin` | read-only: how many SMS the modem keeps in its storages (SIM, modem memory), index and tag only, never contents or numbers |
| `tests/test_wwan.py`, `tests/test_emergency.py` | (not installed) | duo2-wwan's pure parts, and its emergency-call handling played through with a stand-in ModemManager and a fake clock (no call is placed): `python3 -m unittest discover -s tests` here |
| `50-duo2-modemmanager.rules` | `/etc/polkit-1/rules.d` | the GUI user's apps may use ModemManager (Voice, Messaging, Contacts, Device.Control, USSD, CellBroadcast, Time): duo-wc's session has no active logind session, so ModemManager's "active session" defaults refused Calls (`not authorized for org.freedesktop.ModemManager1.Voice`) |
| `30-wwan.network` | `/etc/systemd/network` | networkd leaves the modem's devices alone |
| `duo2-callaudio` + `systemd/user/duo2-callaudio.service` + `callaudio.conf` + `dbus/org.mobian_project.CallAudio.service` | `/usr/local/lib/duo2-telephony`, `/etc/systemd/user`, `/etc/duo2/callaudio.conf`, `/usr/local/share/dbus-1/services` | the audio side of a call (below, "Call audio"): callaudiod's D-Bus interface for Calls (earpiece / speaker by card profile, mute), holds the sound card's voice front ends open while a call is up, mirrors the call volume into the WSA gain (capped per output) and the mute into the DSP's uplink |
| `81voltd/` (`build.sh`, `Dockerfile`, `patches/`) + `systemd/81voltd.service` | `/usr/local/sbin/81voltd`, `/etc/systemd/system` (NOT enabled) | the IMS Data service for VoLTE (QRTR service 770): our build of 81voltd 1.2.0 with six patches; below, "VoLTE" |
| `duo2-volte`, `duo2-imsa` | `/usr/local/sbin` (put there by hand for the trial, not in the image yet) | the VoLTE trial: `status` / `start` / `watch` / `undo`, and a read-only IMSA probe (IMS registration) |
| `debug/imsd-logger` | (not installed) | debug only: serves QRTR service 770 with no-op answers and logs the modem's requests |
| `tests/test_bearers.py` | (not installed) | duo2-wwan and other programs' bearers (IMS), with a stand-in ModemManager |
| `session-start` + `systemd/user/*` | `/usr/local/lib/duo2-telephony`, `/etc/systemd/user` | Calls (`--daemon`) and Chatty (`--gapplication-service`) in the background once duo-wc's session is up |

Packages (rootfs/Dockerfile, `install.sh`): `modemmanager`,
`gir1.2-modemmanager-1.0`, `gnome-calls`, `callaudiod` (installed, its
D-Bus name is taken by duo2-callaudio), `feedbackd` (Calls
rings and vibrates through it: the PM8350B haptics, the phone's own theme
`/usr/share/feedbackd/themes/microsoft,surface-duo2.json`; there is no LED),
`gnome-contacts`, `chatty`, `mmsd-tng` (MMS, not configured).

The ringtone and the vibration follow the sound mode (duo-shell README
"Sound and vibration": feedbackd's profile and strength); the ringtone is
chosen in Duo Settings > Sound, do not disturb silences calls, and the
mailbox has a launcher (below, "Ringtone, do not disturb, voicemail"). Until 2026-11-08
the phone's theme named its parent with `parent-theme` (feedbackd 0.8 reads
`parent-name`), so the default theme was never merged: only `quiet` existed,
every event logged `fbd_feedback_profile_get_feedback: assertion` twice and
an incoming call had no ringtone (only the `quiet` vibration could play). A
call that rings in lights the panels and shows on the lock screen and on the
closed phone's glance / peek strips (who calls); the power key or a volume
key silences its ringing without declining it, through Calls' own
`org.gnome.Calls.Call.Silence` (its ringer ends the feedbackd event: the
ringtone and the vibration together). Both: duo-shell README "Incoming
calls".

Install on a running phone: `telephony/install.sh`. The image has it all
(rootfs/install-components.sh runs `install-files.sh`).

## Call audio

A call's audio stays in the ADSP: the modem hands it to the DSP's voice
services (MVM, CVP; kernel: q6voice, the sound card's VoiceMMode1 and
VoiceMMode2 front ends, PCM devices 3 and 4), whose vocproc reads the
microphone from the VA codec DMA port and plays the other party into the
WSA codec DMA port (earpiece or loudspeakers). docs/hardware/audio.md
has the details; here the parts in userspace:

- **GNOME Calls** asks for call mode, the speaker and mute over D-Bus
  (`org.mobian_project.CallAudio`, callaudiod's interface). **duo2-callaudio**
  answers it instead of callaudiod (`dbus/org.mobian_project.CallAudio.service`
  in `/usr/local/share/dbus-1/services` shadows callaudiod's activation file):
  PipeWire 1.4 makes two card profiles of the UCM **Voice Call** verb
  (`Qualcomm/sm8350/SurfaceDuo2-VoiceCall.conf`), `Voice Call (Earpiece, Mic)`
  and `Voice Call (Mic, Speaker)`, because both outputs share a PCM, and
  callaudiod 0.1.10 only switches sink ports. Call mode = the earpiece
  profile, the call screen's speaker button = the speaker profile (WirePlumber
  0.5.11+ does this switching itself), default mode = HiFi. A call that comes
  up without anyone asking switches by itself.
- **duo2-callaudio** (user service, the GUI user's) also: while ModemManager has a
  call that is dialing, ringing out or active and the card is in a Voice Call
  profile, it opens, prepares and starts both voice front ends in both
  directions (no data flows; preparing starts the DSP's voice path, starting
  unmutes the amplifiers; both subscriptions' sessions, the call's one carries
  the audio). An output change reopens them (the amplifiers' SoundWire ports
  only change when the back end starts again: a short gap), and so does the
  moment a dialed call is answered (paths set up before the connection
  stayed silent in the first calls). Verified 2026-11-08 with the owner:
  earpiece and speaker both ways, mute, volume keys, DTMF. Only members of
  the `audio` group run it (`ConditionGroup=audio`): the unit is enabled
  globally, and the administrator's ssh login would otherwise start a
  second instance. The Voice Call
  sink's volume (the volume keys) becomes the WSA digital gain:
  `EARPIECE_MAX` / `SPEAKER_MAX` at 100 %, `RANGE_DB` lower at 1 %, silence at
  0 % or muted. The mute (Calls' button, or the source's mute) becomes the
  vocproc's uplink mute (`VoiceMMode1/2 Capture Switch`). `TX_TOPOLOGY` picks
  the DSP's uplink processing (`none` or `ecns`). Log: `journalctl --user -u
  duo2-callaudio` (as the GUI user). For tests without a call:
  `DUO2_CALLAUDIO_ASSUME_CALL=1` and `DUO2_CALLAUDIO_BUSNAME=` (another bus
  name, beside a running instance), or `scripts/duo2-voice-check`.

```sh
systemctl --user status duo2-callaudio        # as the GUI user
journalctl --user -u duo2-callaudio -f        # "call up", "hw:...,DEV=3: voice path open", gains
pactl list cards | grep -A3 'Active Profile'  # "Voice Call" during a call
amixer -c MicrosoftSurfac cget name='VoiceMMode1 Capture Switch'
```


## Emergency calls

Reviewed 2026-11-09 by configuration and code (ModemManager 1.24.0, GNOME
Calls 48.2, feedbackd 0.8.2 sources; our stack). **No emergency number was
dialled, ever, not even as a test (the owner's rule):** what a real
emergency call does on this modem is reasoned, not measured.

**How a call goes out.** Calls (its dial pad) or our lock screen asks
ModemManager for a call (`Voice.CreateCall`, then `Call.Start`); the GUI
user may (polkit's Voice, `50-duo2-modemmanager.rules`). ModemManager has
one rule of its own (`mm-iface-modem-voice.c`, 3GPP TS 22.101): while the
modem is not registered ("emergency-only": no SIM, a locked SIM, airplane
mode, no coverage of the SIM's network) it lets through only **112 and
911**, the SIM's own list (EF_ECC) and, **without a SIM**, also 000, 08,
110, 999, 118, 119; registered, everything. It flags nothing as an
emergency call: the modem's call manager recognises 112 / 911 (always) and
the network's list itself and places them as emergency calls (here over CS:
no IMS). ModemManager keeps the Voice interface on a modem
that failed for its SIM or waits for the PIN precisely for this, so Calls
has its origin and its dial pad works in every state.

**What ModemManager does not do: switch the radio on.** This modem comes up
in `shutting-down`, duo2-modem-lpm puts it in low power (RF off) and only an
enable (duo2-wwan, for a ready SIM) brings it online. So before
2026-11-09, without a SIM, with a SIM waiting for its PIN, in airplane mode
or during duo2-wwan's reject-#7 radio cycle, 112 was dialled into a modem
with its radio off (and nothing in ModemManager or Calls turns it on; whether
the modem firmware would go online by itself for an emergency origination
in low power is unknown and not relied on).

| case | ModemManager | before | now |
| --- | --- | --- | --- |
| SIM ready, registered (home, roaming) | any number | works | unchanged |
| SIM ready, its network out of reach (searching) | 112, 911 | radio on: the modem may use any network for an emergency call | unchanged; 110 etc. only once registered |
| no SIM | `failed: sim-missing`, Voice kept; 112, 911 + no-SIM list | radio off | **radio online** (`EMERGENCY_RADIO`): the modem camps on any network in limited service, as every phone ("emergency calls only"); ModemManager itself allows "on" in the failed state for this |
| SIM waiting for its PIN / PUK | `locked`, Voice kept; 112, 911 | radio off | **radio online** over QMI (DMS operating mode `online`, as duo2-modem-lpm sets low power: RAM, no NV write; ModemManager refuses "on" while locked) |
| airplane mode | `disabled`, low power; 112, 911 | radio off | dialling an emergency number **switches airplane mode off** (as Android does: the switch file, so the bar and the quick menu follow; WiFi and Bluetooth stay as they are), enables the modem and, if that first attempt failed for the radio, **dials the same number once more** as soon as the radio is up (registered, or 8 s on for 112 / 911) |
| reject-#7 radio cycle (at most 3 per modem appearance, ~5 s each) | `disabled` meanwhile | an emergency call in that window failed | the cycle's wait is cut short, then the same redial |
| `ENABLE=no`, `mmcli --disable` | `disabled` | radio off | enabled for the call, the same redial |
| locked screen | | duo-wc shows nothing but the lock: no way to dial | **"Emergency call"** on the lock screen (swipe and PIN lock): an emergency keypad (below) |
| SIM PIN dialog (unlocked session, SIM waiting) | | covered the panel ("Not now" hides it) | **"Emergency call"** on the dialog: the same keypad there |

The redial (duo2-wwan, `redial_due`) happens at most once per attempt and
only for a call that never rang and ended for an error (ModemManager's
reasons `unknown` / `error` / `audio-setup-failed`: what a dial with the
radio off gives) within 90 s; never after a hang-up (`terminated`), a busy
or refused call, a call that rang or was up. A national number (110 with a
SIM in) waits for the registration (ModemManager refuses it unregistered).
The new call is created through ModemManager; Calls sees it, starts it and
shows it (if nobody started it after 3 s, duo2-wwan does). Log: `sudo
journalctl -u duo2-wwan -b | grep -i -E 'emergency|radio on'`.

**The lock screen** (duo-shell README "Lock screen"): an "Emergency call"
pill (red) below the PIN keypad, or above the swipe lock's hint; it opens an
emergency keypad on the whole panel: 112 and the country's own numbers as
one-tap buttons (by the network's and the SIM's MCC: Germany 110; Hungary
104, 105, 107; Austria, Switzerland, Czechia, Slovakia, Poland, France,
Italy, UK; national numbers only while ModemManager is registered or in
airplane mode, see above), the keys and a green call key. Only emergency
numbers are dialled (anything else: "Only emergency numbers from the lock
screen"); a tap on a suggestion types it, the call key dials (two taps: a
pocket does not call 112 by brushing one key). The lock sends the number to
the shell, which checks it again and asks ModemManager for the call; the
call then shows on the lock screen's call card (Speaker / Hang up; a PIN
lock stays locked), as an incoming one does. `emergencymodel.py` (pure,
`tests/test_emergencymodel.py`); duo2-wwan's table of numbers is kept in
step by a test.

**Nothing else in the stack delays it** (checked in the code):
duo2-autosleep holds the phone awake for any call that is not over
(dialling included) and its empty-battery power-off waits for the call to
end; smart 5G never switches the mode preference while a call is dialling,
ringing, active or held (and a switch takes about a second with no
registration gap); the QRTR seccomp filter is on the apps, not on
ModemManager; duo2-callaudio opens the voice path for every call
ModemManager has (whoever made it); the proximity sensor darkens the panel
only on the earpiece.

**Calls' own emergency API** (`org.gnome.Calls.EmergencyCalls`, what Phosh's
lock screen uses) is not used: its `CallEmergencyContact` dials only numbers
from the SIM's EmergencyNumbers, which ModemManager reads with an AT
command (`+CRSM`, EF_ECC): this modem has no AT port, so the list is most
likely empty and that call would refuse even 112. Read-only check:
`sudo mmcli -i any | grep -i emergency` (that line only: the full output has
the ICCID / IMSI).

**Not done:** Advanced Mobile Location (AML: Android sends the caller's
position to the emergency centre by SMS during a 112 call, required in the
EU); eCall; an emergency callback mode (Android keeps the radio on and data
off for a few minutes after an emergency call: here airplane mode simply
stays off afterwards); emergency calls on the eSIM slot when the primary
session is on the other one (the modem decides); VoLTE / IMS emergency
calls (no IMS yet).

**Trying it without an emergency number** (the owner, by day): put your other
phone's number into `/etc/duo2/wwan.conf` as `EMERGENCY_TEST_NUMBER=` (digits,
no spaces), `sudo systemctl restart duo2-wwan`, airplane mode on, then call
that number from Calls: duo2-wwan logs "emergency call to the test number:
the radio is off", airplane mode goes off, the modem registers and the call
is made again by itself (the other phone rings). Empty the line again
afterwards. The lock screen's keypad can be looked at (type 3311: refused;
type 112 and do **not** press the call key; Cancel).


## Ringtone, do not disturb, voicemail (2026-11-09)

**Ringtone** (Duo Settings > Sound > Ringtone; shell/duo-settings README):
Calls rings with feedbackd's `phone-incoming-call` event. Another ringtone
is a feedbackd user theme (`~/.config/feedbackd/themes/duo2-ring-a|b.json`:
parent `$device`, i.e. the phone's theme over the default one, with the
`full` profile's `phone-incoming-call` as a Sound with `file-name`) and
GSettings `org.sigxcpu.feedbackd theme` pointing at it (feedbackd reloads
when the key changes; two names so that every change is one). The default
is the key reset. The list: every installed sound theme's
phone-incoming-call (Debian's sound-theme-freedesktop has the one on the
phone; `ocean-sound-theme` and the like would add theirs), a few other
ring-like sounds of those themes, and **your own file** (anything ffmpeg
reads; its first minute, as Ogg Vorbis in `~/.local/share/duo2/ringtones/`:
libcanberra plays only Vorbis and WAV). The sound mode still applies
("Vibration only" only vibrates).

**Do not disturb** (quick menu tile, Duo Settings > Notifications; duo-shell
README "Do not disturb"): GNOME's `show-banners` switch; notifications get
no banner, sound or vibration (critical ones and alarms still do); Calls'
ringer is silenced by feedbackd's per-app profile
(`/org/sigxcpu/feedbackd/application/org-gnome-calls/` `profile=silent`),
a call rings in quietly and lights nothing; exceptions: a caller who rang in
the last 15 minutes (on by default) and, if chosen, contacts ring through
the shell. Alarms: GNOME Clocks plays them itself, in every mode.

**Voicemail**: ModemManager 1.24 has no mailbox number and no
message-waiting indication (it drops QMI's MWI messages; an operator's MWI
text SMS arrives in Chatty as a plain SMS), and Calls 48's dial pad has no
long press on 1 (libcall-ui long-presses only 0, for "+"). So: Duo Settings >
SIM card > **Voicemail number** (offered by the SIM's network code: on
Telekom's network 3311, and 4712 for some prepaid providers) and an app-grid launcher **Voicemail**
(`duo-voicemail`: `gnome-calls --dial <number>`, or the setting when none).
The SIM's own mailbox number (EF_MBDN) and an MWI icon would need our own
QMI reads of the SIM (UIM, read-only): not done (a SIM test with the owner).

## VoLTE (trial, 2026-11-12)

**State 2026-11-12:** first run cut short: the modem
talks to 81voltd (requests 0x23, 0x2E, 0x34) but never asks for the IMS
PDN, so IMS does not register yet; undone. `debug/imsd-logger` logs what the
modem sends to service 770.

On this modem the IMS client (SIP,
registration, the codecs) runs in the modem firmware; the AP has to select
the carrier config (the modem has done that itself: with a SIM on Telekom's
network Commercial-DT-VOLTE is active, its data profiles 1-5 `internet.v6.telekom`,
`ims`, `hos`, `sos`, `festip.telekom`) and to **serve the IMS Data service**:
the modem's IMS client asks the AP (QRTR service 770, the AP is the server)
to bring up its PDN. Nothing served it before, so IMS never came up and
calls went CSFB to 2G.

**81voltd** is that server: on a start request it creates and connects a
ModemManager bearer (APN and profile from the request; ModemManager uses the
modem's profile and writes none) and reports the address back. Debian trixie
has 1.0.0; `81voltd/build.sh` builds the upstream commit `a7794dd` (1.2.0 and
its fix for undecodable QRTR packets, gitlab.postmarketos.org/modem/81voltd,
GPL-2.0-or-later) in Docker for arm64 (C23: the source uses `true` without
`<stdbool.h>`) into `out/81voltd`, with our patches:

1. its own log domain (`G_MESSAGES_DEBUG=81voltd`, no library chatter);
2. no IP address in the log;
3. **start requests for the APNs in `QVD_REFUSE_APNS` (default `sos`) are
   answered at once with an error**: with IMS registered the modem may try an
   emergency call over IMS first and ask for the `sos` PDN; refused, it
   redials over CS at once while 2G exists (owner decision D4; no emergency
   number is ever dialled to test it);
4. a bearer that cannot connect is reported to the modem (connection-down
   indication, error 13) instead of leaving it to its own timer;
5. on SIGTERM / SIGINT it disconnects and deletes its bearers;
6. **request 0x2E is answered with success plus TLV 0x10 = u32 0x5F** (as
   Android's imsdatadaemon does; a hypothesis under test;
   every other id stays on the no-op path).

The unit (`systemd/81voltd.service`: hardened, no capabilities, AF_UNIX +
AF_QIPCRTR only, no PrivateNetwork: AF_QIPCRTR works only in the initial
network namespace) is **not enabled**: a reboot is the way back.

**duo2-wwan and other programs' bearers**: only a bearer for the APN
duo2-wwan connects with (wwan.conf's, else the carrier profile's) is its data
bearer; an IMS one is never adopted (its `qmapmux0.N` link gets no address,
route or DNS: ModemManager does not bring a mux link up, and nobody else
does), and the mobile data switch disconnects only ours (Simple.Disconnect
without a bearer took every bearer down, IMS included). The log names a
foreign bearer once ("belongs to another program: left alone").
`tests/test_bearers.py`.

**duo2-imsa** (read-only): the IMS registration and the voice / SMS service
status. qmicli's IMSA queries get `InvalidOperation` from an unbound client,
and over QRTR a client is its process's socket, so qmicli cannot bind in one
run and query in the next; duo2-imsa binds (the client's own state) and asks
on one socket. Exit 0 = registered.

**The trial** (`sudo duo2-volte ...`, with 81voltd, duo2-volte and duo2-imsa on the phone and the
current duo2-wwan):

```sh
sudo duo2-volte status        # read-only, no identifiers
sudo duo2-volte start         # rmtfs -r, an IMS carrier config, no call, the
                              # fixed duo2-wwan; then 81voltd, IMS bearer, registration
sudo systemd-run --unit=duo2-volte-watch --collect /usr/local/sbin/duo2-volte watch 5400
                              # during calls: service per RAT, IMS voice, a ping
                              # over the data device -> /run/duo2-volte/watch.log
sudo duo2-volte undo          # 81voltd stopped, left-over IMS bearers deleted,
                              # the modem restarted as at boot (rmtfs restart:
                              # what the IMS stack wrote goes with rmtfs's memory)
journalctl -u 81voltd -b      # the modem's requests (APN, family; no addresses)
```

Nothing of the trial writes the modem's partitions: rmtfs runs read-only and
`start` refuses otherwise; 81voltd and the bearers are runtime state.
`qmicli --pdc-load-config` (libqmi 1.36) would crash: it g_free()s the
mmapped file contents (reproduced on the build machine); not needed, the
config is in the modem.

## SIM slots

Physical slot 1 is the nano-SIM tray, physical slot 2 the built-in eSIM
(eUICC). The modem runs both at once (dual SIM dual standby, as the stock
Android configured it: `persist.radio.multisim.config=dsds`): physical 1 =
logical 1, physical 2 = logical 2. ModemManager, and so Calls and Chatty, use
the **primary GW provisioning session** only; `duo2-sim primary N` moves it to
the SIM in slot N (and restarts ModemManager). ModemManager's own
`--set-primary-sim-slot` is not for this modem: it assumes one active slot,
reports slot 2 as "primary" whenever both are active, and would remap logical
slot 2. An eSIM profile needs an LPA first (e.g. lpac, which has a QMI-over-QRTR
backend; not packaged).

## Mobile data

`duo2-wwan` uses ModemManager's simple connect. The bearer is a multiplexed
rmnet link on `rmnet_ipa0` (`qmapmux0.N`, created by ModemManager, mux id 1,
QMAPv5 with checksum offload both ways, as the modem's WDA data format says:
`qmicli -d qrtr://0 --wda-get-data-format="ep-type=embedded,ep-iface-number=1"`);
duo2-wwan puts the bearer's addresses on it, the smaller of its IPv4 / IPv6
MTUs (at most `rmnet_ipa0`'s 1500 minus the 8 bytes of MAP + checksum
header: rmnet refuses more, duo2-wwan then takes the most it may),
`default dev ... metric 4096` for IPv4, SLAAC for IPv6 (the RA's own default
route off), and the DNS servers on the link in systemd-resolved.
Settings: `wwan.conf`.

**WiFi first** (2026-11-07): while another device has an IPv4 default route
(WiFi's DHCP route, metric 1024), the bearer stays connected (idle costs
nothing, data is there the moment WiFi goes) but is only the backup: its IPv4
route sits below WiFi's, it has **no IPv6 default route** and is **not a DNS
default route** in resolved (`resolvectl default-route qmapmux0.0 no`). The
owner's WiFi has no IPv6, so with an IPv6 default on the mobile link every
IPv6 connection (apps try IPv6 first) went over the mobile network beside the
WiFi. duo2-wwan follows WiFi's default route on netlink: when it goes, the
link gets its IPv6 default and DNS at once ("WiFi gone: qmapmux0.0 carries
everything"), when it comes back they go again. Measured by deleting
WiFi's default route by hand (the phone stays reachable on the LAN): IPv4,
IPv6 and DNS over `qmapmux0.0` (an HTTP HEAD to deb.debian.org over both),
restored the same moment WiFi's route came back.

**Measured** (a SIM on Telekom LTE in Germany, 2026-11-07 evening, bound to
`qmapmux0.0`): ping 8.8.8.8 22 ms, 2001:4860:4860::8888 13-26 ms; DNS over
the network's servers (192.0.0.1, 2a01:598:7ff:0:10:74:210:221) 120-160 ms;
TCP to 1.1.1.1:80 and [2606:4700:4700::1111]:80 16 ms; 10 MB download
46.6 Mbit/s over IPv4, 26 Mbit/s over IPv6 (39.7 Mbit/s IPv4 again later).
Data back 2 s after a radio cycle (`--disable`, `--set-power-state-low`,
`--enable`: 5 s from the start), 2 s after `systemctl restart duo2-wwan`
(the bearer stays up, its device is set up again), at once after the
mobile data switch off / on. The first minutes after the package went
active (18:10-18:12) passed no traffic at all (TX counted, nothing back but
the network's RAs) with exactly this setup; at 18:15 everything worked, no
change made. Most likely the network held the traffic until the data
option was in place (an SMS came in at 18:14); not proven. Nothing in the
kernel, rmnet or ModemManager needed changing.

**Airplane mode** (2026-11-07): `/var/lib/duo2/airplane` (`on` / `off`,
written by the shell's quick menu through `duo-shell-helper airplane`, which
also rfkill-blocks WiFi and Bluetooth and brings back the ones that were on
afterwards), watched like the data switch: on disables the modem (it goes to
low power with that, RF off; a disabled, locked or SIM-less one is put
there), mobile data is off; off enables it again (power up, registration,
data). The file survives a reboot: at boot the modem stays in the low power
duo2-modem-lpm left it in. Measured: on -> low power in 2 s, off -> data
back in 3 s; with the switch on, a restart of duo2-wwan leaves the modem in
low power. Airplane off/on is also the user's way out of a reject cause #7
state (below).

**Smart 5G** (2026-11-08, `SMART_5G=yes` in wwan.conf, the default): 5G NR
(NSA) only while mobile data is on, a panel is lit and WiFi is not the default
route; otherwise the modem's mode preference without NR. The reason is power:
with NR in the preference this modem stays in EN-DC and never power-collapses,
even with no data on the bearer (measured with the AP awake and the panels
dark, 5 min each: modem asleep 0-23 % with NR, 94 % with LTE only; in suspend
it also gates the DDR's low-power mode: 10 min suspended, ~285 mA with NR,
~184 mA LTE only, modem asleep 40 % / 80 %, DDR low power 39 % / 74 %).
duo2-wwan's own QMI client (QRTR, NAS Set System Selection Preference) changes
only the RAT mode preference (0x5c = GSM + UMTS + LTE + 5G NR -> 0x1c) with the
change duration "until power cycle": RAM only, the modem's own setting comes
back when it restarts, and duo2-wwan applies its choice again when the modem
appears (its state in `/run/duo2/wwan-nr.json`, so a restart of duo2-wwan
does not mistake its own change for the modem's setting). Never
`mmcli --set-allowed-modes`: ModemManager 1.24 sends it as PERMANENT, a modem
NV write. NR goes 30 s after the panels went dark (a glance changes nothing),
at once before a suspend (a logind delay inhibitor), 2 s after mobile data is
switched off; it comes back 3 s after a panel lit up; never while a call is
dialling, ringing, active, held or waiting (deferred, checked every 10 s).
Measured switch cost (Telekom LTE + NR NSA): NR -> LTE 0.8 s, LTE
-> NR 1.8 s (the NR leg comes back with the next connection: at once when
there is traffic, later when the modem is idle), no registration, packet
service or bearer gap either way. **The hotspot** (2026-11-09, hotspot/README.md):
devices connected to the phone's hotspot or USB tethering count as data in
use: NR is allowed while they are there, also with the panels dark and the
phone itself on WiFi (their traffic is the bearer's whatever the phone's
own default route), when the user's "Use 5G for the hotspot" is on (the
hotspot state file's `use_5g`, watched) and `SMART_5G_HOTSPOT=yes` (the
default); 30 s after the last one left, NR goes as usual. Not tried on the
phone yet. A mode preference set right now reads back
the old value for a moment (the modem applies it asynchronously).

**Reject cause #7, automatic** (2026-11-07): registered (home, or roaming
with ROAMING=yes), mobile data wanted, but no packet service
(`PacketServiceState` detached) for 3 min: duo2-wwan does the low-power
cycle itself (disable, low power, 3 s, enable), at most every 30 min and 3
times per modem appearance (then it says so and leaves it), never during a
call (a call postpones it by a minute). The log names ModemManager's
`NetworkRejection` cause when there is one ("network reject:
gprs-not-allowed, cause #7"). Not seen live yet: the SIM's data works now.

**The switch** (2026-11-07): the shell's quick menu has a Mobile data tile;
it writes `/var/lib/duo2/mobile-data` (`on` / `off`, through
duo-shell-helper as root), which duo2-wwan watches: off disconnects the
bearer at once (no retries), on connects when registered. Without the file,
`DATA` in wwan.conf decides.

**APN notes**: with an empty `APN`, duo2-wwan connects with the APN of the
modem's initial EPS bearer, i.e. the carrier profile (MBN) the modem chose
for the SIM (on Telekom's network: `internet.v6.telekom`, IPv4v6, verified
2026-11-07); ModemManager refuses a 3GPP connection without an APN. A
fresh registration or packet attach cancels the retry backoff.

**A SIM put in while running**: ModemManager probes it at once and gives up
(`sim-missing`, "GW primary session index unknown"), the modem opens the
primary session by itself 30-60 s later; `duo2-sim auto` (run by duo2-wwan)
waits for that and restarts ModemManager.

**At boot with the SIM in** (2026-11-07, two reboots by another agent's
kernel tries): the same failure, but the modem did not open the session by
itself within 90 s; it opened it right after `duo2-sim` asked for it, while
answering the request with a refusal (the session was there 0.2 s later,
both boots). `duo2-sim auto` now waits 30 s, asks, and looks again for 30 s
after a refusal; with the session there it restarts ModemManager. Measured:
data up 150 s after boot (before the fix: the modem stayed `failed:
sim-missing` until ModemManager was restarted by hand).

**Reject cause #7** (GPRS / EPS services not allowed, e.g. a SIM whose data
is not provisioned yet): per 3GPP the modem then treats the SIM as having
no data until the radio is switched off; ModemManager restarts and
`--disable` do not clear it, low power does:
`mmcli -m any --disable && mmcli -m any --set-power-state-low && mmcli -m any --enable`.

Fallbacks (from mobile-broadband-provider-info, not tried with a SIM yet):
ALDI TALK (o2, 262-03) `internet.eplus.de` / `eplus` / `gprs`;
Telekom (262-01) `internet.telekom`, with the user name and password the
provider gives (not needed: the carrier profile's `internet.v6.telekom`
works, IPv4 + IPv6).

## SMS storage

Chatty takes each received SMS and deletes it from ModemManager; ModemManager
then logged "failed deleting SMS message ...: Couldn't delete N parts" for
every one (1-3 parts, 2026-11-07). Checked (`sudo duo2-sms-storage`): the
modem's storages are empty (SIM and modem memory, GW mode; CDMA is refused,
QMI error 47), and ModemManager offered nothing again after its restarts
(it reads every stored message when it enables the modem: a part left
behind would come back to Chatty as a new message). So nothing stays and
nothing is imported twice; the failing delete is cosmetic as far as can be
seen. The modem's routes (`qmicli -d qrtr://0 --wms-get-routes`) store class
0/1 messages in modem memory and class 2 on the SIM, everything else is
transfer-only. Why the delete fails is in ModemManager's debug log only
("couldn't delete SMS part with index N: ..."): `sudo mmcli -G debug` before
the next SMS shows it (back with `-G info`; ModemManager hides personal
data in its log). Modem memory is most likely in the modem's EFS, which
`rmtfs -r` keeps only in RAM: a modem restart would empty it anyway.

## The SIM PIN and the bar (duo-shell)

The shell (shell/duo-shell, `mobile.py`, `mobilemodel.py`, `simpin.py`)
follows ModemManager on the system bus: a modem `locked` with `sim-pin` or
`sim-puk` gets a PIN dialog over everything on the active panel (after the
session's own lock screen): a keypad, the attempts left (UnlockRetries),
after three wrong PINs the PUK and a new PIN twice; sent with
`Sim.SendPin` / `SendPuk` (the GUI user may: `Device.Control` in the polkit
rule). The digits are never logged, stored or shown. The status bar shows
the signal bars and the operator (the SIM's name, else the network's) when
a SIM is present, a locked-SIM icon while it waits for its PIN (tapping it
opens the dialog). Tried with a fake ModemManager
(`shell/duo-shell/tests/fake_mm.py`), not with a SIM yet.

Tested without a SIM: ModemManager finds the modem through QRTR (`qcom-soc`,
ports `qrtr0` + `rmnet_ipa0`), ends in `failed: sim-missing`, power state
`low`; a restart of the modem (`systemctl restart rmtfs`) runs the hook again
and ModemManager rebuilds the modem; an rmnet link on `rmnet_ipa0` is created
and deleted (`qmicli --link-add/--link-delete`); duo2-wwan's device setup and
teardown on such a link with documentation addresses (WiFi kept the default
route). Untested until a SIM is in: registration, the bearer itself, calls, SMS.

## eSIM (2026-11-07 night)

The built-in eUICC is physical slot 2. Its profiles are managed with
**lpac** (github.com/estkme-group/lpac, v2.3.0; Debian does not package it),
built from source for the phone by `lpac/build.sh` (Docker, aarch64 trixie,
the QMI-over-QRTR APDU backend and curl; the source is fetched at the tag
and checked against its commit; the binary lands in `out/lpac`, not in git)
and installed as `/usr/local/bin/lpac` (`install.sh` brings it, the image
recipe builds it when missing).

`duo2-esim` (root, `/usr/local/sbin`; the GUI user's sudoers rule
`/etc/sudoers.d/duo2-esim`) is the one door: `info` (EID, free memory,
versions), `list` (profiles, no icons), `add CODE [CONFIRMATION]`, `enable`,
`disable`, `rename`, `delete`. It runs lpac with `LPAC_APDU=qmi_qrtr
LPAC_APDU_QMI_UIM_SLOT=2`, **as a transient service that systemd (PID 1)
starts** (`systemd-run --pipe --wait`, unit `duo2-esim-lpac-PID-N`,
`RuntimeMaxSec`, read-only system, no home), not as its own child: the GUI
user's programs carry a seccomp filter against QRTR sockets (duo-wc.service
and the user manager, `RestrictAddressFamilies=~AF_QIPCRTR`, inherited
across sudo; docs/hardware/security.md 9.), so an lpac started by the
helper would fail (`euicc_init`). What it runs is decided here, after the
argument checks, as before. **Every change is asked of the user in the
shell first** (duo-wc's `confirm`, action `esim`, 45 s; no answer = no):
any app can run the helper through sudo, the question makes it the user's
decision. After a download / enable / disable / delete the chip's
notifications for the operator are sent (`notification process -a -r`), as
a phone's LPA does. Arguments are checked (an ICCID is 18-20 digits, an
activation code `LPA:1$<SM-DP+>$<matching id>`, a nickname 64 printable
characters); the question names the profile and, for a download, only the
SM-DP+ host (never the matching id). Nothing logs an EID or an ICCID.

Testing without the chip: while root's `/etc/duo2/esim-fake` exists, the
helper runs `tests/fake_lpac.py` instead of lpac: a chip with two made-up
profiles, its state in `/run/duo2-esim-fake.json`. `tests/test_esim.py`
runs the helper's commands against the fake (`python3 -m unittest discover
-s tests` here). Duo Settings' eSIM page is the UI
(`shell/duo-settings/duosettings/esimpage.py`).
