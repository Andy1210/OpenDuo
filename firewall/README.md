# firewall: nothing reaches the phone unasked

OpenDuo's firewall is one nftables table, `inet duo_firewall`, loaded by
`duo-firewall.service` before the network comes up. Its input chain drops
everything (policy drop) except:

| What | Where from |
|---|---|
| replies to the phone's own connections (`ct state established,related`), loopback | everywhere |
| ICMP errors, ping (10/s), IPv6 neighbour discovery and router advertisements (hop limit 255), multicast listener queries from link-local | everywhere |
| DHCP client answers (udp 67 -> 68), DHCPv6 client answers from link-local | everywhere |
| ssh (tcp 22): only while Developer mode switches sshd on (`devmode/`) | `wlan0` (the WiFi), `usb0` (the USB development network) |
| the USB network's DHCP server (udp 67) | `usb0` |
| DNS and DHCP for the hotspot's devices (hotspot/) | `ap0`, `tether*` |
| DNS and DHCP for Android (Waydroid's dnsmasq) | `waydroid0` |

Nothing else: no service listens on the mobile data links (`qmapmux*`,
`rmnet*`; the IMS link has no address on the phone, VoLTE runs in the
modem), and ssh is never reachable over mobile data, from the hotspot's
devices or from Android. mDNS is not let in: nothing on the phone uses it.
To let something else in, add a rule to `/etc/duo-firewall/firewall.nft`
and `systemctl reload duo-firewall`.

The other tables on the same hook keep working: Waydroid's
(`inet duo2_waydroid`, android/firewall.nft) and the hotspot's
(`inet duo2_hotspot`) run before this one and drop the rest of their
interfaces' traffic themselves; a packet passes only if every table lets it,
so what they let in is listed here too. Forwarding stays theirs (this table
has no forward chain).

`resolved-no-llmnr.conf` (`/etc/systemd/resolved.conf.d/90-duo-no-llmnr.conf`)
turns systemd-resolved's LLMNR (port 5355 on every interface) and multicast
DNS off.

| File | On the phone |
|---|---|
| `firewall.nft` | `/etc/duo-firewall/firewall.nft` |
| `duo-firewall.service` | `/etc/systemd/system`, enabled (sysinit.target) |
| `resolved-no-llmnr.conf` | `/etc/systemd/resolved.conf.d/90-duo-no-llmnr.conf` |
| `install-files.sh` | puts them in place (the image and `install.sh`) |
| `install.sh [--start]` | over the network; `--start` loads it now under a dead man's switch: a transient timer stops it again after 10 minutes unless a new ssh connection gets in |

Tests (no root): `unshare -rn python3 tests/netns_check.py` loads this table,
Android's and the hotspot's in network namespaces and sends real packets
from the WiFi, the USB network, a hotspot device, Android and the mobile
network.

`systemctl stop duo-firewall` removes the table; `nft list table inet
duo_firewall` shows it with the counter of what was dropped.
