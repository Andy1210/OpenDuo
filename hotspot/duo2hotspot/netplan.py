"""The network side of the hotspot (pure parts, tests/test_netplan.py): the
devices' subnets, networkd's runtime files for them, the nftables table,
the mobile data device, the policy routing.

Every device on the hotspot (WiFi: ap0, USB: tether0) gets a /24 of its own
with networkd's DHCP server, and a /64 of a private IPv6 prefix (RFC 4193,
router advertisements from networkd); the phone answers DNS on both
addresses (dnsrelay.py, to systemd-resolved). Their traffic leaves through
the MOBILE DATA device only, also while the phone's own WiFi client is the
default route: `ip rule iif ap0 lookup TABLE` and a table with nothing but
the mobile device (an unreachable default under it), masqueraded to the
phone's mobile addresses. The table `inet duo2_hotspot` lets the devices
reach the phone only for DHCP, DNS and ICMP, forwards only to and from the
mobile device, and marks what it forwards (MARK): the Waydroid table
(android/firewall.nft), which drops all forwarding but its own, lets marked
packets through.
"""

import fnmatch
import ipaddress

TABLE = 7240  # the routing table and the rules' preference
RULE_PREF = 7240
MARK = 0x10000000  # "the hotspot's table forwards this" (android/firewall.nft)
NFT_TABLE = "duo2_hotspot"
NETWORK_DIR = "/run/systemd/network"
LINK_FILE = NETWORK_DIR + "/05-duo2-hotspot.link"
POOL_OFFSET = 10
POOL_SIZE = 40


def used_networks(addrs, routes):
    """The IPv4 networks the phone already has (`ip -j addr`, `ip -j route
    show table all`): a hotspot subnet must not overlap any."""
    nets = []
    for link in addrs or []:
        for a in link.get("addr_info") or []:
            if a.get("family") == "inet" and a.get("local"):
                try:
                    nets.append(
                        ipaddress.ip_network(
                            "%s/%s" % (a["local"], a.get("prefixlen", 32)), strict=False
                        )
                    )
                except ValueError:
                    pass
    for r in routes or []:
        dst = r.get("dst")
        if not dst or dst == "default":
            continue
        try:
            net = ipaddress.ip_network(dst if "/" in dst else dst + "/32", strict=False)
        except ValueError:
            continue
        if net.version == 4:
            nets.append(net)
    return nets


def pick_subnet(candidates, used, mine=()):
    """The first candidate that overlaps nothing in use (nor `mine`, the
    other hotspot device's), or None."""
    for net in candidates:
        if not any(net.overlaps(u) for u in list(used) + list(mine)):
            return net
    return None


def downstream(iface, net4, ula48, index):
    """One hotspot device's addresses: {"iface", "net4", "addr4", "net6",
    "addr6"} (the phone is .1 / ::1; IPv6 only with a ULA /48)."""
    d = {
        "iface": iface,
        "net4": net4,
        "addr4": net4.network_address + 1,
        "net6": None,
        "addr6": None,
    }
    if ula48 is not None:
        base = int(ula48.network_address) | (index << 64)
        d["net6"] = ipaddress.IPv6Network((base, 64))
        d["addr6"] = ipaddress.IPv6Address(base + 1)
    return d


def network_file(d):
    """networkd's .network for a hotspot device (in /run: gone at reboot)."""
    lines = [
        "# duo2-hotspot (hotspot/README.md): the hotspot's %s, written at its start" % d["iface"],
        "[Match]",
        "Name=%s" % d["iface"],
        "",
        "[Link]",
        "RequiredForOnline=no",
        "",
        "[Network]",
        "Address=%s/%d" % (d["addr4"], d["net4"].prefixlen),
    ]
    if d["addr6"] is not None:
        lines.append("Address=%s/64" % d["addr6"])
    lines += [
        "DHCPServer=yes",
        "ConfigureWithoutCarrier=yes",
        "IPv6AcceptRA=no",
        "LinkLocalAddressing=%s" % ("ipv6" if d["addr6"] is not None else "no"),
        "IPv6SendRA=%s" % ("yes" if d["addr6"] is not None else "no"),
        "LLMNR=no",
        "MulticastDNS=no",
        "",
        "[DHCPServer]",
        "PoolOffset=%d" % POOL_OFFSET,
        "PoolSize=%d" % POOL_SIZE,
        "EmitRouter=yes",
        "EmitDNS=yes",
        "DNS=%s" % d["addr4"],
        "EmitNTP=no",
        "DefaultLeaseTimeSec=600",
        "MaxLeaseTimeSec=3600",
    ]
    if d["addr6"] is not None:
        lines += [
            "",
            "[IPv6SendRA]",
            "EmitDNS=yes",
            "DNS=%s" % d["addr6"],
            "EmitDomains=no",
            "",
            "[IPv6Prefix]",
            "Prefix=%s" % d["net6"],
        ]
    return "\n".join(lines) + "\n"


def network_path(iface):
    return "%s/40-duo2-hotspot-%s.network" % (NETWORK_DIR, iface)


def link_file(iface):
    """udev's .link for the access point's interface: its name kept (the WiFi
    chip's own 10-wifi.link matches the driver and would try to rename a
    second interface of it to wlan0) and its MAC address the kernel's (the
    driver gives every extra interface its own, locally administered)."""
    return (
        "\n".join(
            [
                "# duo2-hotspot (hotspot/README.md): written at its start, in /run only",
                "[Match]",
                "OriginalName=%s" % iface,
                "",
                "[Link]",
                "NamePolicy=keep",
                "Name=%s" % iface,
                "MACAddressPolicy=none",
                "",
            ]
        )
        + "\n"
    )


def nft_ruleset(downs, ups, mark=MARK, table=NFT_TABLE):
    """The table `inet duo2_hotspot`, replacing itself (one transaction).
    downs: downstream dicts; ups: the mobile data devices."""

    def names(xs):
        return ", ".join('"%s"' % x for x in xs)

    v4 = ", ".join(str(d["net4"]) for d in downs)
    v6 = ", ".join(str(d["net6"]) for d in downs if d.get("net6") is not None)
    down_elems = ("\n\t\telements = { %s }" % names(d["iface"] for d in downs)) if downs else ""
    up_elems = ("\n\t\telements = { %s }" % names(ups)) if ups else ""
    nat = []
    if v4:
        nat.append("\t\toifname @uplinks ip saddr { %s } masquerade" % v4)
    if v6:
        nat.append("\t\toifname @uplinks ip6 saddr { %s } masquerade" % v6)
    return """#!/usr/sbin/nft -f
# duo2-hotspot (hotspot/README.md), loaded at its start, removed at its end.
table inet {t}
delete table inet {t}
table inet {t} {{
	counter shared_out {{ }}
	counter shared_in {{ }}
	set downlinks {{
		type ifname{de}
	}}
	set uplinks {{
		type ifname{ue}
	}}
	chain input {{
		type filter hook input priority filter - 20; policy accept;
		iifname != @downlinks accept
		udp dport {{ 53, 67 }} accept
		tcp dport 53 accept
		meta l4proto {{ icmp, ipv6-icmp }} accept
		counter drop
	}}
	chain forward {{
		type filter hook forward priority filter - 20; policy accept;
		iifname @downlinks oifname @uplinks tcp flags syn tcp option maxseg size set rt mtu
		iifname @uplinks oifname @downlinks tcp flags syn tcp option maxseg size set rt mtu
		iifname @downlinks oifname @uplinks counter name "shared_out" meta mark set meta mark | {m} accept
		iifname @uplinks oifname @downlinks ct state established,related counter name "shared_in" meta mark set meta mark | {m} accept
		iifname @downlinks counter reject with icmpx type admin-prohibited
		oifname @downlinks counter drop
	}}
	chain postrouting {{
		type nat hook postrouting priority srcnat - 5; policy accept;
{nat}
	}}
}}
""".format(t=table, de=down_elems, ue=up_elems, m="0x%08x" % mark, nat="\n".join(nat))


def nft_teardown(table=NFT_TABLE):
    return f"table inet {table}\ndelete table inet {table}\n"


def parse_counters(nft_json):
    """`nft -j list counters table inet duo2_hotspot` -> {"out": bytes, "in": bytes}."""
    out = {"out": 0, "in": 0}
    for item in (nft_json or {}).get("nftables", []):
        c = item.get("counter")
        if isinstance(c, dict) and c.get("name") in ("shared_out", "shared_in"):
            out["out" if c["name"] == "shared_out" else "in"] = int(c.get("bytes") or 0)
    return out


def find_uplink(links, addrs, routes, patterns):
    """The mobile data device: a link whose name matches one of `patterns`
    (UPLINKS), not down, with a global address; one with a default route in
    the main table first. links: `ip -j link`, addrs: `ip -j addr`, routes:
    `ip -j route show default` + `ip -j -6 route show default`."""
    pats = (patterns or "").split()
    have_addr = set()
    for link in addrs or []:
        for a in link.get("addr_info") or []:
            if a.get("scope") == "global":
                have_addr.add(link.get("ifname"))
    with_default = {r.get("dev") for r in routes or [] if r.get("dev")}
    found = []
    for link in links or []:
        name = link.get("ifname") or ""
        if not any(fnmatch.fnmatchcase(name, p) for p in pats):
            continue
        if "UP" not in (link.get("flags") or []) or link.get("operstate") == "DOWN":
            continue
        if name not in have_addr:
            continue
        found.append(name)
    found.sort(key=lambda n: (n not in with_default, n))
    return found[0] if found else None


def has_v6(addrs, iface):
    for link in addrs or []:
        if link.get("ifname") == iface:
            return any(
                a.get("family") == "inet6" and a.get("scope") == "global"
                for a in link.get("addr_info") or []
            )
    return False


def rules_present(rules, iface, pref=RULE_PREF, table=TABLE):
    """Whether `ip -j rule show` has our rule for this device."""
    for r in rules or []:
        if (
            r.get("priority") == pref
            and r.get("iif") == iface
            and str(r.get("table")) == str(table)
        ):
            return True
    return False


def rule_cmds(iface, add=True, pref=RULE_PREF, table=TABLE):
    verb = "add" if add else "del"
    return [
        ["ip", fam, "rule", verb, "pref", str(pref), "iif", iface, "lookup", str(table)]
        for fam in ("-4", "-6")
    ]


def table_cmds(uplink, v6, table=TABLE):
    """The policy table: the mobile device as the default, an unreachable
    default under it (no mobile data: the devices get an error at once
    instead of a way out through WiFi)."""
    cmds = [
        [
            "ip",
            "-4",
            "route",
            "replace",
            "unreachable",
            "default",
            "metric",
            "4294967295",
            "table",
            str(table),
        ],
        [
            "ip",
            "-6",
            "route",
            "replace",
            "unreachable",
            "default",
            "metric",
            "4294967295",
            "table",
            str(table),
        ],
    ]
    if uplink:
        cmds.append(
            [
                "ip",
                "-4",
                "route",
                "replace",
                "default",
                "dev",
                uplink,
                "metric",
                "1",
                "table",
                str(table),
            ]
        )
        if v6:
            cmds.append(
                [
                    "ip",
                    "-6",
                    "route",
                    "replace",
                    "default",
                    "dev",
                    uplink,
                    "metric",
                    "1",
                    "table",
                    str(table),
                ]
            )
    return cmds


def table_flush_cmds(table=TABLE):
    return [
        ["ip", "-4", "route", "flush", "table", str(table)],
        ["ip", "-6", "route", "flush", "table", str(table)],
    ]


def sysctls(downs, uplink):
    """The kernel switches the sharing needs: (path, value). IPv4 forwards
    what comes in on a device whose `forwarding` is on (both ways: the
    devices', the mobile one's); IPv6 forwards per device with
    force_forwarding (kernel 6.17+), never the global switch (that would turn
    the router mode on everywhere: no more router advertisements accepted on
    the mobile link). rp_filter: loose on the mobile device (its replies come
    back there although WiFi is the default route)."""
    out = []
    for iface in [d["iface"] for d in downs] + ([uplink] if uplink else []):
        out.append(("/proc/sys/net/ipv4/conf/%s/forwarding" % iface, "1"))
        out.append(("/proc/sys/net/ipv6/conf/%s/force_forwarding" % iface, "1"))
    return out


def rp_filter_fix(all_value, dev_value):
    """The mobile device's rp_filter to write so that the effective one
    (the larger of all/dev) is loose (2), or None when it already is."""
    try:
        eff = max(int(all_value), int(dev_value))
    except (TypeError, ValueError):
        return None
    return None if eff in (0, 2) else "2"
