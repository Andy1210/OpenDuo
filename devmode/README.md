# devmode: Developer mode and bug reports

Settings > Developer is for people who help develop OpenDuo. It is on every
installation and **off by default**:

| Switch | What it does |
|---|---|
| Developer mode | the main switch; off turns SSH and USB networking off too |
| SSH | sshd, key login only (no passwords, no root login), as the administrator `duo2admin` (sudo without a password). The firewall (`firewall/`) lets it in on the WiFi (`wlan0`) and the USB network (`usb0`) only: never over mobile data, from the hotspot's devices or from Android |
| Authorized keys | the public keys that may log in: paste one, or pick a `.pub` file (from Downloads, a USB stick...); listed with their fingerprints, removable. They are the administrator's `~/.ssh/authorized_keys` |
| USB networking | the USB gadget's network function: on a USB cable the phone is `10.15.19.1` and gives the computer an address (`usb0`) |
| Log touch positions | the touch daemon writes every touch's and the pen's position to the system log (for calibration and touch bugs). **It can reveal what you type, the lock screen's PIN too.** Off by default, asks you first, and goes off again when developer mode is turned off and when the phone restarts |
| Developer tools | installed while developer mode is on, removed while it is off (below) |
| Collect a bug report | the system's logs and state, identifiers masked, into `~/Downloads` (also with developer mode off) |

## For contributors: connecting to the phone

1. On the phone: Settings > Developer > **Developer mode** on, then **SSH**
   on. The shell asks you to confirm (no app can switch SSH on by itself).
2. **Add your computer's public key** (`~/.ssh/id_ed25519.pub`; make one with
   `ssh-keygen -t ed25519` if you have none): the + button, then paste it or
   pick the file. Confirm in the shell again.
3. Connect with the address the page shows, on the same WiFi:
   `ssh duo2admin@ADDRESS`. Or turn **USB networking** on, plug the phone in
   and use `ssh duo2admin@10.15.19.1`.

The repository's scripts that drive a phone (`scripts/lib/remote.sh`) read
where it is from `local.env` (copy `local.env.example`): `DUO2_HOST` is
`duo2admin@ADDRESS`, `DUO2_USER` the GUI user (`duo` on the image).

## For contributors: sending a bug report

Settings > Developer > **Collect a bug report**, confirm in the shell, and
`openduo-bugreport-DATE-TIME.tar.gz` appears in Downloads. Attach it to the
issue. It holds the journal of this start and the one before, the kernel's
messages, failed services and crashes, the network's state and the firewall,
memory and storage, package versions, the compositor's state (without window
titles), and the device's own items (`bugreport.d/`: the modem, the touch
and pen settings, batteries, temperatures on the Surface Duo 2).

Before anything is written, every line is masked: device numbers (IMEI,
IMSI, ICCID, EID), phone numbers, MAC and Bluetooth addresses, IP addresses
outside OpenDuo's own subnets, SSIDs, e-mail addresses, user, host and
Bluetooth device names, serial numbers and the machine id, keys and tokens,
coordinates. The rules are `scripts/check-personal-data`'s (the same that
keep personal data out of this repository) plus the log-specific ones in
`duodevmode/redact.py`. Each value becomes one placeholder for the whole
report (`[mac-1]`, `[ip-2]`...), so it can still be followed from line to
line. Masking finds the usual identifiers, not everything a log line may
say: look through the report before you post it publicly. Over ssh:
`sudo duo-devmode bugreport --out DIR`.

## Developer tools

Bring-up and debugging programs are not installed on a phone with developer
mode off. `devtools.list` names them by where they are installed (lists
beside it, `devtools-*.list`, are appended to it):

| Tool | What it is for |
|---|---|
| `duo2-tone` | a test tone on the speakers |
| `duo2-wifi-join` | joining a network by hand |
| `duo2-imsd-logger` | the VoLTE IMS data service's logger (`telephony/debug/imsd-logger`) |
| `/usr/local/lib/duo-wc/tools/{touch,key,toplevels}.py` | synthetic touches and keys, the window list (they need the compositor's admin socket) |
| `duo2-pose-check` | the pose detection checked in six positions |
| `duo-screencast-test` | the screen-cast and screenshot portals tried the way an app uses them |

Their copies are kept in `/usr/local/lib/openduo-devtools` (root's, 0700: no
other user can read or run them there). Turning developer mode on installs
them at their paths, turning it off removes them, and
`duo-devmode-sync.service` does the same at every boot: a component installed
again while developer mode is off (its installer puts its tools back) loses
them at the next start. A file found at a listed path is taken as the newest
version of that tool; a link or a directory there is left alone.

## Touch positions

duo2-touchd's contact log (`touch/README.md`) runs only while developer mode
is on **and** its switch is: `/run/duo-devmode/touch-log`, root's, on a tmpfs
(so a restart switches it off). `duo-devmode touchlog on` asks the user in the
shell first (an app could otherwise switch it on and read the positions back
from a bug report); `touchlog off`, and developer mode off, remove it. The
daemon itself checks both when the log is asked for and every 2 s while it
logs (`touch/devgate.h`). A bug report never carries the daemon's position
lines, whatever the switch says.

## How it works

`duo-devmode` (root, `/usr/local/sbin`) is the one door; Duo Settings runs
it with sudo (`/etc/sudoers.d/duo-devmode`). Any app of the GUI user could
run it too, so what opens the phone (SSH on, a key added) and the bug report
(it reads the system's logs) happen only after the user's yes to the
shell's question (duo-wc's `confirm`, which no app can answer); switching
off and removing a key ask nothing. Run by root or by the administrator, it
asks nothing. Keys come on stdin, never as a path for root to open; a key
with options (`command=`, `from=`...), an unknown type or an RSA key under
2048 bits is refused. The bug report is written by a child process that has
become the caller (never as root into the user's directory).

```
duo-devmode status               the switches, the keys, the addresses (JSON)
duo-devmode mode on|off
duo-devmode ssh on|off
duo-devmode usb on|off
duo-devmode key add              (the key on stdin)
duo-devmode key remove SHA256:...
duo-devmode touchlog on|off      (on asks the user; developer mode only)
duo-devmode devtools sync        (the developer tools follow the switch)
duo-devmode bugreport [--out DIR]
```

`/etc/duo-devmode.conf` keeps `developer_mode` and `usb_network` (sshd's
own unit being enabled is the SSH switch). An installation without the file
(from before Developer mode) counts as all on, as it was then; the image
ships it all off. `scripts/build-images.sh` with `DUO2_DEVELOPER_MODE=on`
(environment or `local.env`) makes an image for a developer's own phone
with everything on.

| File | On the phone |
|---|---|
| `duo-devmode` | `/usr/local/sbin/duo-devmode` |
| `duodevmode/` (`conf`, `keys`, `redact`, `bugreport`) | `/usr/local/lib/duo-devmode/duodevmode/` |
| `scripts/check-personal-data` | `/usr/local/lib/duo-devmode/check_personal_data.py` |
| `bugreport.d/*.conf` | `/usr/local/lib/duo-devmode/bugreport.d/` (the device's items: `NAME: command` or `NAME < /path`) |
| `devmode.conf` | `/etc/duo-devmode.conf` (only when there is none) |
| `devtools.list` | `/usr/local/lib/duo-devmode/devtools.list`; the tools' copies in `/usr/local/lib/openduo-devtools/files/` |
| `duo-devmode-sync.service` | `/etc/systemd/system`, enabled: the developer tools follow the switch at every boot |
| `install-files.sh USER` | puts them in place, the sudoers rule for USER (the image and `install.sh`), then syncs the developer tools; the image runs it after every other component, so it finds their tools |

The USB network's switch is the device's gadget script
(`rootfs/overlay/usr/local/sbin/duo2-usb-gadget apply`); while USB tethering
(hotspot/) is on, it is left as it is. The page: `shell/duo-settings/duosettings/developerpage.py`.

Tests: `python3 -m unittest discover -s tests` (keys, settings, the commands
against a stand-in system, the touch log's switch, the developer tools on a
stand-in root, masking, the bug report and its touch positions, the gadget
script on a stand-in configfs); the page: `shell/duo-settings/tests/run-gtk.sh
tests/gtk_developer_page.py`.
