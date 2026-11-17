# hotspot: mobile hotspot and USB tethering

Shares the phone's mobile data (duo2-wwan's bearer, `qmapmux0.0`) with
other devices: over WiFi (an access point beside the phone's own WiFi
connection when the chip allows it) and over the USB cable (a second CDC-NCM
function in the phone's one USB gadget). Written 2026-11-09; **not run on the
phone yet** (offline: unit tests, and the forwarding / firewall / DNS part end
to end in network namespaces on the build host, below).

```
quick menu tile ──┐ (shell's grant)     Duo Settings > Hotspot ──┐ (user's yes in the shell)
                  └──> sudo duo2-hotspot ON|off|usb|set|password <┘
                          │ /run/duo2-hotspot/want.json + start / SIGHUP
                          ▼
            duo2-hotspot.service = duo2-hotspotd (only while something is shared)
     ap0 + hostapd │ tether0 (gadget) │ networkd /run files │ nft inet duo2_hotspot
     ip rule iif ap0 → table 7240 (mobile only) │ DNS relay → resolved │ sleep inhibitor
                          │ /run/duo2/hotspot.json (no password, no device address)
                          ▼
       quick menu tile, Duo Settings page, duo2-wwan smart 5G (devices = data in use)
```

## The stack (and why)

- **hostapd** (Debian's, run by our daemon with a config in `/run`; Debian's
  own units stay off). wpa_supplicant's AP mode would need no new package,
  but hostapd is the access point daemon proper: WPA2/WPA3-SAE transition
  mode with MFP, station limits, its control socket for the devices and
  events. **New package: `hostapd`** (install.sh, rootfs/Dockerfile).
- **systemd-networkd** for the hotspot's devices: runtime `.network` files in
  `/run/systemd/network/` (gone at reboot): an address, its DHCP server
  (router and DNS = the phone), router advertisements for IPv6. `networkctl
  reload` reconfigures only links whose file changed (wlan0, usb0 untouched).
- **nftables**, our own table `inet duo2_hotspot` (replaces itself in one
  transaction, deleted at the end): the devices reach the phone only for
  DHCP, DNS and ICMP (no ssh, nothing else); forwarding only between them and
  the mobile device, replies only for their own connections, MSS clamped to
  the mobile MTU; masquerade to the phone's mobile addresses (IPv4, and
  **NAT66** from a private prefix of our own for IPv6).
- **Policy routing**: `ip rule iif ap0 / tether0 lookup 7240`, the table
  holding `default dev qmapmux0.0` and an unreachable default under it. The
  devices use the MOBILE data also while the phone's own WiFi is its default
  route (WiFi first, telephony/README.md); without mobile data they get an
  error at once and never a way out over WiFi.
- **Forwarding per device, never the global switches**: IPv4
  `conf/<dev>/forwarding` on ap0 / tether0 and the mobile device (IPv4
  forwards what comes IN on a device with it on); IPv6
  `conf/<dev>/force_forwarding` (kernel 6.17+; our 7.3 has it). The global
  IPv6 switch would make the phone a router everywhere and stop the router
  advertisements on the mobile link. rp_filter: loose on the mobile device
  if it was strict. Every switch is put back at the end.
- **DNS**: a relay in the daemon on the hotspot addresses (UDP and TCP,
  bounded, no parsing) to systemd-resolved's stub, so the devices get what
  the phone gets (dnsrelay.py). LLMNR / mDNS off on the hotspot devices.
- **Waydroid's table** (android/firewall.nft) drops every forward but its
  own (Waydroid turns `ip_forward` on). The hotspot's table runs before it
  and marks what it forwards (`0x10000000`); the Waydroid table now accepts
  marked packets. A Waydroid table loaded before this change is loaded
  again from its file when the hotspot starts.
- **Sleep**: a logind `sleep` inhibitor (block) while anything is shared;
  duo2-autosleep honours it (an access point cannot serve asleep).
- **Smart 5G** (duo2-wwan): devices on the hotspot count as data in use, so
  5G NR is allowed while they are there, with the panels dark and with the
  phone itself on WiFi too, when the user's "Use 5G for the hotspot" is on
  (`use_5g` in the state file) and `SMART_5G_HOTSPOT=yes` (wwan.conf). NR
  goes 30 s after the last device left (the usual hold-down).

## WiFi: beside the phone's own connection

The chip (WCN6851, ath11k "wcn6855 hw1.1", `support_dual_stations`) reports
two interface combinations: a station and access points on **one channel**
(total 16), and on **up to two channels** (total 3, the firmware's three
vdevs: wlan0, the P2P device wpa_supplicant makes, ap0). So:

| the phone's WiFi | the hotspot |
| --- | --- |
| connected | on the network's channel and width (5 GHz 80 MHz included), whatever band was chosen: `how=shared` |
| connected on a channel an AP may not use (radar / DFS, no-IR) | another channel beside it (`OTHER_CHANNEL=yes`: the chip switches between the two, `how=second`), else it does not start |
| on, not connected | its own channel (5 GHz: 36 at 80 MHz; 2.4 GHz: 6 at 20 MHz), `how=own` |
| switched off by the user (rfkill) | the radio goes on for the hotspot, the WiFi client stays disconnected (`wpa_cli disconnect`); at the end both as they were. The quick menu's WiFi tile says "Off (hotspot on)"; a tap brings the client back beside the hotspot |

**The WiFi client is never cut without being asked.** When the hotspot
cannot run beside the connection: with `WIFI_CLIENT=keep` (the default) it
does not start; with `ask` the helper answers `needs-wifi-off` and the UI
asks ("Switch WiFi off for it? An ssh session over WiFi drops."), then
`on --wifi-off` disconnects the client for the hotspot and reconnects it at
the end. **Development mode** (`NEVER_CUT_WIFI=yes` in /etc/duo2/hotspot.conf,
the shipped default while the phone is in bring-up) refuses `--wifi-off`
whatever the user chose (`cut-not-allowed`), and Settings greys the choice.

The access point interface is `ap0` (`iw phy phy0 interface add ap0 type
__ap`), its MAC the driver's second address (locally administered). The
chip's `10-wifi.link` matches its driver and would rename a second
interface to wlan0: a runtime `/run/systemd/network/05-duo2-hotspot.link`
keeps `ap0`'s name and MAC. Channels are read from `iw phy` (the firmware's
regulatory domain decides: self-managed; a 5 GHz channel that is no-IR or a
radar channel is never chosen; the band falls back to the other one). With
today's kernel every 5 GHz channel is no-IR: next section.

Security: WPA2/WPA3 (default: `WPA-PSK SAE`, MFP optional, SAE with
H2E and hunting-and-pecking), WPA3 only (`SAE`, MFP required), WPA2 only.
No open hotspot. At most 8 devices (`MAX_CLIENTS`). Off by itself after 10
minutes without a device (`IDLE_OFF_MIN`, 0 = never).

## The devices on the hotspot

The daemon lists them from hostapd's stations (`STA-FIRST` / `STA-NEXT` on
its control socket: only authorized ones) and the DHCP server's leases
(`networkctl --json=short status ap0`: the address and the device's own
name, matched by the hardware address), and writes them to the state file
(no MAC address). It reads them again on hostapd's `AP-STA-CONNECTED` /
`AP-STA-DISCONNECTED` events, 3, 12 and 40 s after one (the lease and its
name come later), and every 30 s while the access point runs (a device that
left without an event, a name that came late; the state file is written only
when the list changes).

**2026-11-17, on the phone:** the hotspot worked (another phone joined and
had internet), but Settings > Hotspot never listed it. The event socket has a
timeout for its requests, and with a timeout Python's `recv` waits for it even
with `MSG_DONTWAIT` and raises `TimeoutError`: at the first event the
daemon's event watch ended with that exception, before it read the stations,
so the list stayed empty for the rest of the run. Fixed (`ctrl.recv_event`
polls without waiting; a broken socket drops only the event watch; the 30 s
read). systemd 257 gives a lease's hardware address as the DHCP message's
whole 16-byte field with its length beside it; that is now read too, so a
device whose DHCP client id is not its MAC still gets its name and address.
The fix is unit-tested (a real datagram socket in place of hostapd's, the
daemon's event path); **not tried on the phone yet**.

## Regulatory domain (the WiFi country)

Investigated 2026-11-12 (read-only on the phone, the stock firmware files
decoded on the build host). **Symptom:** the access point cannot start on
5 GHz: every 5 GHz channel is no-IR, so beside the home network on 5 GHz
channel 40 the hotspot refuses; 2.4 GHz channels 1-11 are free.

**What the phone runs** (kernel 7.3.3-duo2-g9d07e7d6b, firmware
WLAN.HSP.1.1.c3-00267, the stock `regdb.bin` and `bdwlan.elf`):

- `iw reg get`: `phy#0 (self-managed)` **country DE: DFS-ETSI**, yet every
  5 GHz rule `PASSIVE-SCAN` (no-IR), a `2457-2482 PASSIVE-SCAN` rule
  (channels 12-13 no-IR), every power at most 20 dBm, the 5.8 GHz band cut at
  5835 MHz. `global` is `country 00`: cfg80211 refused its own database at
  boot ("loaded regulatory.db is malformed or signature is missing/invalid").
- The cached scan results (`iw dev wlan0 scan dump`) carry only Country DE.
- Interface combinations: one channel (total 16) or **two channels (total 3)**:
  wlan0 + the P2P device + ap0 fit the second one, so `how=second` (the AP on
  2.4 GHz beside channel 40) is allowed by cfg80211; if it failed on the
  phone, that is the firmware's multi-channel side, a separate question.

**Where it comes from** (all four steps checked in the source or the files):

1. *The firmware's default.* The firmware loads its own `regdb.bin` (stock,
   from the modem partition; Qualcomm's format: a 197-country table, regdomain
   pairs, 2.4 / 5 / 6 GHz domains and rules) and the board file, whose
   regulatory header holds no country (zeros). It boots in a **world roaming
   domain**: decoded from its regdb, the WORLD_6x domains are 2402-2472
   active, 2457-2482 no-IR, 5170-5330 / 5490-5730 / 5735-5835 no-IR, all
   20 dBm. It reports that domain under a country code (seen as "PR"), not
   "00". This is the "PR": no beacon, no 11d.
2. *ath11k's intersection.* The first channel list after WMI init becomes
   ath11k's `default_regd`, and every later country is **intersected** with
   it (`ath11k_regd_intersect`: ranges cut, powers min'ed, flags OR'ed)
   unless the default's alpha2 is `00` or `na`. With "PR" it is intersected.
3. *11d.* The station's first scan runs the firmware's 11d scan (WMI
   11D_SCAN_START, stopped at association); it found DE, ath11k sent
   `WMI_SET_CURRENT_COUNTRY` DE, the firmware answered with its own DE rules,
   decoded: ETSI13, 5170-5250 indoor 23 dBm, 5250-5330 indoor + DFS,
   5490-5730 DFS 30 dBm, 5735-5875 14 dBm, **no no-IR anywhere**. DE ∩ world
   is exactly what the phone shows, rule for rule (the 20 dBm caps, the 5835
   end and the 2457-2482 rule are the world default's). The no-IR is
   ath11k's, not the firmware's and not Germany's. **Correction (measured
   2026-11-12 evening, below):** on a fresh boot 11d finds nothing. The
   association stops the 11d scan ~4 s after it starts, before the firmware
   reports a country, and the phy stays "PR" (both kernels). The DE of the
   long-running boot came from some earlier 11d run (a longer scan without a
   network) and was kept by the re-send at every wake. 11d is luck; the
   country setter is what makes it reliable.
4. *Every wake.* The chip is powered off in sleep; after a resume the firmware
   starts in the default again ("PR" for a moment) and ath11k re-sends the
   last country (`ath11k_core_resume`), DE again.

**`iw reg set DE`** does nothing on the chip today: cfg80211 passes a user
hint to a self-managed wiphy (`notify_self_managed_wiphys`), but ath11k's
notifier drops it unless the kernel has `CONFIG_ATH_REG_DYNAMIC_USER_REG_HINTS`
(which needs `CONFIG_CFG80211_CERTIFICATION_ONUS`); ours has neither. And even
with it, DE ∩ world is what already runs. cfg80211's own database (CRDA,
wireless-regdb) is only the `global` domain, which a self-managed phy ignores;
it is refused anyway because Debian's alternative is signed with Debian's key
and the kernel trusts the upstream ones (`net/wireless/certs`: sforshee,
wens), so a hint there ends in a CRDA timeout. **There is no runtime-only way
to lift the no-IR: it needs the kernel change below.**

**Android** (the stock dump): the country comes from telephony
(`gsm.operator.iso-country` = `de`, the serving network's MCC) through the WiFi
HAL's `setCountryCode` into qcacld, which gives the user's country priority
over 11d (`gCountryCodePriority=1`, `g11dSupportEnabled=1` in the
QCA6490's `WCNSS_qcom_cfg.ini`) and takes the firmware's channel list as it
is: no intersection, so Android never had this.

### The fix

- **Kernel**, branch `duo2-regd` (worktree `src/linux-regd`, from `duo2`),
  compile-checked (`W=1`, no warnings), tried on the phone 2026-11-12 (below):
  1. `wifi: ath11k: Don't intersect a country with a world roaming default`:
     the default counts as world roaming when its alpha2 is a world one, when
     its domain code is one of the firmware's WORLD pairs (0x60-0x6f), or when
     none of its 5 GHz rules allows initiating radiation (the last one surely
     holds here: every 5 GHz rule of the measured intersection is no-IR). A
     REG debug line names the default (alpha2, country id, domain code), so the
     trial shows which test matched.
  2. `wifi: ath11k: Keep a user's country that 11d had already set`: a hint
     equal to the current country used to be dropped silently (11d stayed on);
     now it stops 11d and is the country re-sent after a resume. No WMI
     command: the firmware already runs it.
  3. `kernel/config/regd.config` (EXTRA_CONFIG for the trial, into
     `duo2.config` at promotion): `CFG80211_CERTIFICATION_ONUS=y`,
     `ATH_REG_DYNAMIC_USER_REG_HINTS=y` (the other ONUS options stay off).
  With 1 alone, 11d's DE becomes Germany's own rules: 36-48 and 149-165 free
  for an access point (36-64 indoor-only, `NO-OUTDOOR`: cfg80211 does not
  stop an access point there), 52-144 radar (the hotspot never picks them):
  `how=shared` on channel 40 works. Without any country (no network seen
  yet), the world default stays: 5 GHz no-IR, the hotspot on 2.4 GHz.
- **duo2-wifi-country** (this directory): the country from the mobile
  network's MCC (`mmcli -m any -J`, the 3GPP operator code; never the SIM's
  home country), `iw reg set XX` once per country and boot, read back from
  `iw reg get`. Not while an access point runs (deferred: a new country could
  take its channel away, cfg80211 would stop it), not twice within a minute,
  only a country the firmware's regdb knows (otherwise 11d decides). Setting
  `COUNTRY=auto|XX|off` in `/etc/duo2/wifi-country.conf`. State
  `/run/duo2/wifi-country.json` (countries, the MCC, times; no operator, no
  address). A timer (2 min after boot, every 10 min) runs `apply`; **the
  installer puts the files in place but does not enable the timer.** On
  today's kernel it is harmless: `result: ignored`. Checked on the phone
  read-only from /tmp: `status` → network 262 / DE, phy DE, `no_ir_5ghz:
  all`; `apply --dry-run` → `set DE (network, same as 11d)`.
- **cfg80211's database**: `install-files.sh` points the `regulatory.db`
  alternative at `regulatory.db-upstream` (takes effect at the next boot; only
  the global domain, so no crda timeouts after a hint).

### Risks

- The trial kernel: only the regdomain computation changes, after a country
  is set (11d at the first scan, or a hint); the station's channel stays
  enabled (cfg80211 drops a station only on a disabled channel, and ch 40 is
  enabled in both). Power caps rise to the country's (the firmware enforces
  its board limits anyway). `duo2-try`: one boot, netwatch reboots into the
  known-good kernel without WiFi in 5 minutes, a hang panics into it.
- A country **change** during an association (MCC says AT while 11d said
  DE): `WMI_SET_CURRENT_COUNTRY` + a new channel list. Not seen on this
  firmware yet (ath11k does it for every 11d result, but before the
  association; Android does it whenever the MCC changes). Tested first with a
  dead-man reboot armed (below). The setter's first hint (DE = 11d's DE) sends
  no WMI command at all with patch 2.
- 11d alone can be fooled by an access point announcing a wrong country; the
  setter's network country wins once it runs (patch 2 stops 11d).

### On the phone (each step on the coordinator's GO; WiFi is the only link)

0. Logging only, no risk: the REG debug bit for one sleep and wake (the
   firmware sends its channel lists again at every resume). Shows the
   default's alpha2 and rules, and the DE list's raw flags (`FLAGS` without
   128 = `NL80211_RRF_NO_IR`):

   ```sh
   echo 0x200 | sudo tee /sys/module/ath11k/parameters/debug_mask
   # ... one sleep and wake ...
   sudo dmesg | grep -E 'ath11k.*(Country|FLAGS|Regulatory)'
   echo 0 | sudo tee /sys/module/ath11k/parameters/debug_mask
   ```

1. The trial kernel (one boot; no WiFi in 5 minutes: netwatch reboots into
   the known-good one):

   ```sh
   SRC=src/linux-regd O=out/linux-regd EXTRA_CONFIG=kernel/config/regd.config \
       scripts/build-kernel.sh
   DUO2_KSRC=src/linux-regd DUO2_KOUT=out/linux-regd \
       scripts/duo2-try --append ath11k.debug_mask=0x200
   ```

   Check: ssh over WiFi as before; `iw reg get`: phy#0 DE without
   `PASSIVE-SCAN` on 5170-5250 and 5735-5875 (`duo2-wifi-country status`:
   `no_ir_5ghz: none`); dmesg `default regd .. world roaming`; `sudo
   duo2-hotspot on` → `how: shared`, channel 40, then `off`; one sleep and
   wake: DE comes back. Good → `duo2-promote`, merge `duo2-regd` into `duo2`,
   fold `regd.config` into `duo2.config`, re-export `kernel/patches`.

2. The setter, after the promotion (`hotspot/install.sh`), with a dead-man
   reboot armed for each step:

   ```sh
   sudo systemd-run --on-active=5min --unit=duo2-deadman systemctl reboot
   sudo duo2-wifi-country apply          # DE = 11d's DE: no WMI command, pins it
   sudo systemctl stop duo2-deadman.timer
   # a real change during the association (ETSI too, channel 40 stays):
   sudo systemd-run --on-active=5min --unit=duo2-deadman systemctl reboot
   sudo iw reg set AT; iw dev wlan0 link | grep freq; ping -c5 <gateway>
   sudo iw reg set DE
   sudo systemctl stop duo2-deadman.timer
   sudo systemctl enable --now duo2-wifi-country.timer
   ```

   A dropped link comes back through wpa_supplicant in seconds; if it does
   not, the dead-man reboots (all of it is runtime: the boot's default and
   11d return).

### Measured on the phone (2026-11-12 evening)

Panels dark throughout, autosleep held off, then back on.

- **Step 0** (promoted `7.3.3-duo2-g9d07e7d6b`, REG debug for one 60 s
  RTC-bounded sleep). The firmware's default after the wake: `Country PR,
  CFG Regdomain UNSET FW Regdomain 0`. Its rules: 2402-2472 FLAGS 0,
  2457-2482 FLAGS 128 (no-IR), and 5170-5330, 5490-5730, 5735-5835 FLAGS
  2176 (AUTO-BW + NO-IR). Then ath11k re-sent DE: `Country DE, CFG
  Regdomain ETSI FW Regdomain 2`, 2402-2482 FLAGS 0, 5170-5250 23 dBm 2056
  (NO-OUTDOOR), 5250-5330 2072 (NO-OUTDOOR + DFS), 5490-5730 30 dBm 2064
  (DFS), 5735-5875 14 dBm 2048. **No NO-IR anywhere in DE's rules**:
  decoding and measurement agree.
- **Trial kernel** `7.3.3-duo2-g904ec5d3f` (branch `duo2-cand` in
  `src/linux-cand`: `duo2` + iris 0001-0010 + the two regd patches,
  `regd.config` as EXTRA_CONFIG; `duo2-try --append ath11k.debug_mask=0x200`):
  - `default regd PR country 630 domain 0x6a world roaming`. The domain-code
    test matched: PR is not a world alpha2, and 0x6a is a WORLD_6x pair.
  - The station came up as before (5200 MHz, 80 MHz HE, 960 Mbit/s).
  - **11d set no country**: the scan started and was stopped at
    association after ~4.4 s without a result (`mac stop soc 11d scan`, no
    `event 11d new cc`). This happened at boot and again after a wake, and
    the promoted kernel does the same on a fresh boot (`country PR` after
    the reboot back). So the phy stayed `country PR: DFS-UNSET`, with the
    default's own rules (5 GHz all PASSIVE-SCAN, as expected without a
    country).
  - **`iw reg set DE`** (dead-man armed, then cancelled) **took effect at
    once.** ath11k sent DE (CONFIG_ATH_REG_DYNAMIC_USER_REG_HINTS), and
    without the intersection the phy became `country DE: DFS-ETSI` with
    2402-2482 (N/A, 20), 5170-5250 (N/A, 23) NO-OUTDOOR, 5250-5330 (N/A, 23)
    NO-OUTDOOR DFS, 5490-5590 / 5590-5650 / 5650-5730 (N/A, 30) DFS, and
    5735-5875 (N/A, 14). **No PASSIVE-SCAN on any rule** (before: every 5 GHz
    rule PASSIVE-SCAN, every power capped at 20 dBm, 5.8 GHz cut at 5835,
    and 2457-2482 PASSIVE-SCAN). The link stayed on channel 40 and ping
    worked. No CRDA trouble showed (the global domain stayed `00`).
  - **After a 60 s RTC-bounded sleep**: PR for a moment, then DE re-applied
    twice, the same rules without no-IR. 11d stayed off (a user's country).
  - **`duo2-hotspot on`** (with a grant from the admin socket; no second
    device): `ok`, `how: shared`, **channel 40**, 5 GHz, 80 MHz, the client
    `kept`; ap0 is an AP on 5200 MHz beside wlan0. `off` right after: ap0
    gone, the service inactive, wlan0 still on channel 40.
  - Kernel log: the same five known WARNs as the promoted kernel (clk x4, irq
    x1), nothing new from ath11k. No failed units.
  - **The setter is required.** With patch 1 and no country, a fresh boot
    keeps the world default (5 GHz no-IR, the hotspot on 2.4 GHz). Only a
    country set by the user or by duo2-wifi-country (or a lucky 11d) frees
    5 GHz.

## USB tethering

`functions/ncm.tether` (device `tether0`, its own stable MACs from the
machine id) is linked into the gadget's configuration AFTER the console
(ACM) and the development network (NCM, usb0), so their interface numbers,
addresses and the host's names for them stay. Adding a function needs the
gadget unbound: it is unbound and bound again at once (the host sees the
phone re-plugged for about a second, as at every suspend; usb0 keeps its
address; an ssh over it may or may not survive). **`usb on` refuses while an
ssh session runs over usb0** (`usb-ssh`) unless `--force` (Settings asks
first). CDC-NCM works with Linux, macOS and Windows 11 (Windows 10 2004+);
RNDIS (older Windows) is not offered. ⚠ A host that is plugged in gets a
default route and DNS through the phone: do not switch it on while the
development Pi is the USB host (its own traffic could go out over mobile
data, depending on its route metrics).

## Files

| file | installed as | what |
| --- | --- | --- |
| `duo2-hotspot` | `/usr/local/sbin` | the one door: status / on / off / usb / wifi-client / set / password (one JSON line each); sudoers rule for the GUI user; on, usb on, a new password need the shell's grant or the user's yes (duo-wc's `confirm`) |
| `duo2-hotspotd` + `duo2-hotspot.service` | `/usr/local/sbin`, `/etc/systemd/system` | the daemon; started by the helper, never at boot, stops itself when nothing is shared; KillMode=mixed, hardening |
| `duo2hotspot/` | `/usr/local/lib/duo2-hotspot/duo2hotspot` | conf (settings, password), wifi (iw parsing, the channel plan, hostapd's config, its control messages), netplan (subnets, networkd files, nft, policy routing, switches), gadget, dnsrelay, ctrl (hostapd's socket), state |
| `hotspot.conf` | `/etc/duo2/hotspot.conf(.dist)` | the administrator's switches |
| — | `/var/lib/duo2/hotspot/{settings,password,ula}` | the user's choices, the password (0600, generated: `xxxx-xxxx-xxxx`, 60 bits), the private IPv6 prefix's seed; made on first use |
| — | `/run/duo2/hotspot.json` | the state (0644): on/off, band, channel, how, the client's hold, mobile data, devices (name, address, since), `use_5g`, the last request's result |
| `duo2-wifi-country` + `.service` + `.timer` | `/usr/local/sbin`, `/etc/systemd/system` | the WiFi country from the mobile network (status / apply [--dry-run]); the timer is not enabled by the installer ("Regulatory domain") |
| `duo2hotspot/regdom.py` | with the rest of `duo2hotspot/` | the MCC table, the setting, `iw reg get`, the firmware regdb's country table, the decision |
| `wifi-country.conf` | `/etc/duo2/wifi-country.conf(.dist)` | `COUNTRY=auto` / `XX` / `off` |
| — | `/run/duo2/wifi-country.json` | the last hint (country, time, why), its result (applied / ignored / failed), the last check |
| `install.sh`, `install-files.sh` | | hostapd + the files on the phone / in the image; the `regulatory.db` alternative to the upstream-signed file |
| `tests/` | | `python3 -m unittest discover -s tests` (84); `unshare -rn python3 tests/netns_check.py` (19 checks, no root) |

Elsewhere: `shell/duo-shell/duoshell/hotspot.py`, `hotspotmodel.py` (the
quick menu tile), `shell/duo-settings/duosettings/hotspot.py`,
`hotspotpage.py` (Settings > Hotspot), `telephony/duo2-wwan` (smart 5G),
`android/firewall.nft` (the mark).

## Secrets and identifiers

The password lives only in `/var/lib/duo2/hotspot/password` (root, 0600)
and in hostapd's runtime config (`/run/duo2-hotspot/hostapd.conf`, 0600,
removed at the end); `duo2-hotspot password` prints it (Settings: Show /
Copy, forgotten after 60 s or when the page closes). Any app of the user can
run the helper and read it, as any app could see it on the screen; a new
password needs the shell's grant or the user's yes. Nothing logs it, the
SSID, a device's address or name: hostapd's output goes through the daemon,
which replaces every MAC address by a short name valid for that run; the
state file has no MAC address.

## Tests

- `python3 -m unittest discover -s tests` (here): settings, iw output
  (fixtures with made-up names), channel plans (shared / second / own, DFS,
  no-IR, widths), hostapd's config per security, the network plan, the
  gadget on a directory standing in for configfs (the development
  functions kept, the bind always restored), the DNS relay against a
  stand-in resolver, the helper's words and grants, and the daemon's whole
  start / stop with a stand-in system: the commands, what it writes, that the
  WiFi client is never touched unless allowed, that everything goes back.
  The WiFi country (`test_regdom.py`): the MCC table, the setting, `iw reg
  get` (the phone's measured intersection and the expected rules after the
  patch), a regdb-like table (and the stock one when it is in the tree), the
  decision, and `duo2-wifi-country apply` with stand-in commands (once per
  country, never under an access point, `ignored` read back, no operator
  code in the state file).
- `unshare -rn python3 tests/netns_check.py`: the real `apply_shared()` in
  network namespaces (a veth for ap0, a veth "home WiFi" as the default
  route, a raw-IP TUN pair as the mobile link, Waydroid's table loaded): the
  device reaches the internet over MOBILE data, masqueraded (IPv4, TCP,
  NAT66); not the home LAN, not the phone's ssh; nothing reaches it unasked;
  DNS answers on both hotspot addresses; no mobile data = no way out over
  WiFi; Waydroid's old table drops it, the new one passes it; everything
  removed at the end. (The build host's kernel 6.8 has no IPv6
  force_forwarding: the namespace's global switch stands in.)
- duo-shell `tests/test_hotspotmodel.py`, duo-settings `tests/test_hotspot.py`
  (pure), `tests/run-gtk.sh tests/gtk_hotspot_page.py` (the page under GTK 4 +
  libadwaita, pictures), telephony `tests/test_wwan.py` (smart 5G).

## On the phone (tomorrow, with the owner)

Install (needs the coordinator's GO: a package and files): `hotspot/install.sh`
(hostapd), then `shell/duo-shell/install.sh`, `shell/duo-settings/install.sh`
and `telephony/install.sh` (duo2-wwan restarts: the bearer stays up),
`android/install.sh` only for the new firewall file. Read-only first:

```sh
iw phy phy0 info | sed -n '/interface combinations/,/^$/p'   # the two combinations
iw reg get                                                    # the firmware's country
iw phy phy0 info | grep -E 'MHz \[(36|40|44|48|149)\]'        # 5 GHz: no IR? radar?
iw dev wlan0 info | grep channel                              # the home network's channel
cat /proc/sys/net/ipv6/conf/wlan0/force_forwarding            # exists (kernel 6.17+)
sysctl net.ipv4.conf.all.rp_filter net.ipv4.ip_forward
```

Then (development mode stays on: the WiFi client is never cut):

1. `sudo duo2-hotspot status` (makes the name and password), `sudo
   duo2-hotspot on` (or the quick menu tile). Expect `"ok": true`, `how:
   shared`, channel = the home network's. ssh over WiFi must stay up.
   (On a 5 GHz home network this needs the `duo2-regd` kernel: until then
   every 5 GHz channel is no-IR, "Regulatory domain".)
   `journalctl -u duo2-hotspot -b`.
2. The owner's other device joins (Settings > Hotspot: name, Show password).
   It gets 10.15.43.x and fd..:1::x; check on it: a web page; its public IP
   is the mobile one (an IP lookup site, Telekom), not the home line's;
   a ping to the home router fails. The tile shows "1 device".
3. On the phone: `sudo nft list table inet duo2_hotspot` (counters), `ip rule`,
   `ip route show table 7240`, `sudo duo2-hotspot status --bytes`; panels
   dark for a minute: `journalctl -u duo2-wwan | grep -i 'smart 5G'` says NR
   allowed (devices on the hotspot). Leave the device: NR off 30 s later.
4. Off (tile / `sudo duo2-hotspot off`): ap0 gone (`ip link`), the table
   gone, `ip rule` back, WiFi client still connected.
5. 2.4 GHz: `sudo duo2-hotspot set band 2.4` (with the home WiFi connected
   the channel stays the home network's: expected).
6. USB tethering (a laptop, NOT the Pi as the host): unplug from the Pi,
   plug the laptop, `sudo duo2-hotspot usb on` (over WiFi ssh), the laptop
   gets 10.15.44.x and browses over mobile data; `usb off`; plug the Pi
   back and check `ssh -J <pi> duo2admin@10.15.19.1` (`<pi>`: the Pi's ssh host).

If anything misbehaves: `sudo duo2-hotspot off; sudo systemctl stop
duo2-hotspot` (everything it made goes with it); a reboot clears all of it
anyway (all runtime).
