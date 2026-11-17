#!/bin/bash
# The package test's inside (packaging/tests/container-test.sh runs it in a
# trixie arm64 root under qemu): install OpenDuo from its signed repository
# (stable), look at what landed, then switch to testing and upgrade.
#
#   inside.sh STAGE     STAGE: install | upgrade | all
#
# /repo is the test repository (the keyring package points at file:/repo),
# /t this directory, /manifest the packages.toml, /next the next version's
# expected changes (container-test.sh).
set -uo pipefail
export DEBIAN_FRONTEND=noninteractive
APT=(apt-get -q -y -o APT::Install-Recommends=false -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold)
fails=0
ok() { echo "ok    $*"; }
bad() { echo "FAIL  $*"; fails=$((fails + 1)); }
check() { if eval "$2"; then ok "$1"; else bad "$1"; fi; }

esp_sums() { sha256sum /boot/efi/loader/entries/duo2.conf /boot/efi/loader/entries/rescue.conf /boot/efi/duo2/*; }

manifest_checks() { # every package's check list (packages.toml)
    python3 - <<'EOF'
import tomllib
for name, spec in sorted(tomllib.load(open("/manifest", "rb"))["package"].items()):
    for c in spec.get("check", []):
        print(name, c)
EOF
}

placement() {
    local pkg what n=0
    while read -r pkg what; do
        n=$((n + 1))
        case $what in
        enabled:*)
            # a unit the owner switched off (stage_install) must stay off instead
            if grep -qx "${what#enabled:}" /tmp/owner-disabled 2>/dev/null; then continue; fi
            check "$pkg: ${what#enabled:} enabled" "systemctl is-enabled -q '${what#enabled:}' 2>/dev/null"
            ;;
        enabled-global:*) check "$pkg: user unit ${what#enabled-global:} enabled" "systemctl --global is-enabled -q '${what#enabled-global:}' 2>/dev/null" ;;
        *) check "$pkg: $what" "[ -e '$what' ]" ;;
        esac
    done < <(manifest_checks)
    [ $n -gt 50 ] || bad "only $n manifest checks"
}

common_checks() {
    local half
    half=$(dpkg-query -W -f '${db:Status-Abbrev} ${Package}\n' 'openduo-*' | grep -v '^ii ' | grep -v '^un ' || true)
    check "every OpenDuo package installed" "[ -z '$half' ]"
    [ -z "$half" ] || echo "      $half"
    check "dpkg --audit is clean" "[ -z \"\$(dpkg --audit)\" ]"
    for s in /var/lib/dpkg/info/openduo-*.postinst /var/lib/dpkg/info/openduo-*.prerm /var/lib/dpkg/info/openduo-*.postrm; do
        [ -e "$s" ] || continue
        check "sh -n $(basename "$s")" "dash -n '$s'"
    done
    placement
    check "duo-wc runs as the GUI user" "grep -qx 'User=duo' /etc/systemd/system/duo-wc.service"
    check "sudoers rules parse" "visudo -cq"
    check "/etc/os-release is OpenDuo's" "grep -qx 'NAME=\"OpenDuo\"' /etc/os-release && grep -q '^ID_LIKE=debian' /etc/os-release"
    check "/etc/duo2-release has the version" "grep -q '^openduo=' /etc/duo2-release"
    # the built programs find their libraries (the packages' Depends)
    local f missing
    for f in /usr/local/bin/duo-wc /usr/local/bin/nibfair /usr/local/bin/lpac /usr/local/bin/hexagonrpcd \
        /usr/local/libexec/iio-sensor-proxy; do
        [ -e "$f" ] || continue
        missing=$(ldd "$f" 2>&1 | grep 'not found' || true)
        check "libraries of $f" "[ -z '$missing' ]"
        [ -z "$missing" ] || echo "      $missing"
    done
    check "the hand-managed ESP entries untouched" "esp_sums | cmp -s - /tmp/esp.sums"
}

kernel_entry() { # REL: its entry is on the stand-in ESP with the known-good entry's options
    local rel=$1 e
    e=$(find /boot/efi/loader/entries -name "openduo-$rel*.conf" | head -n 1)
    check "kernel $rel has an entry" "[ -n '$e' ]"
    [ -n "$e" ] || return 0
    check "kernel $rel: Image on the ESP" "cmp -s /usr/lib/openduo/kernel/$rel/Image /boot/efi/openduo/$rel/Image"
    check "kernel $rel: options from duo2.conf + openduo.entry" "grep -qx 'options duo2.boot=root rw quiet panic=30 openduo.entry=openduo-$rel' '$e'"
    check "kernel $rel: initrd from duo2.conf" "grep -qx 'initrd  /duo2/initrd-boot.img' '$e'"
    check "kernel $rel: the DTB duo2.conf names" "grep -qx 'devicetree /openduo/$rel/sm8350-microsoft-surface-duo2-bringup-j.dtb' '$e'"
    check "kernel $rel: modules indexed" "[ -s /usr/lib/modules/$rel/modules.dep ]"
}

stage_install() {
    echo "== bootstrap: the keyring package, then apt from the signed repository"
    dpkg -i "$(find /repo/pool -name 'openduo-archive-keyring_*.deb' | sort | head -n 1)" >/dev/null
    # The base has every Debian package the packages depend on: only OpenDuo's
    # repository is read here (no Debian upgrades mixed into the test)
    mv /etc/apt/sources.list.d/debian.sources /root/ 2>/dev/null || true
    rm -f /etc/apt/sources.list
    if apt-get update -q >/tmp/update.log 2>&1; then ok "apt-get update"; else bad "apt-get update"; cat /tmp/update.log; fi
    check "apt update verifies the OpenDuo signature" "grep -q 'file:/repo stable InRelease' /tmp/update.log && ! grep -qi 'NO_PUBKEY\|not signed\|BADSIG' /tmp/update.log"
    check "our repository is pinned for the camera rebuilds" "apt-cache policy | grep -q 'o=OpenDuo'"
    esp_sums >/tmp/esp.sums
    echo "== install openduo-surface-duo2 (stable)"
    if "${APT[@]}" install openduo-surface-duo2 >/tmp/install.log 2>&1; then
        ok "apt-get install openduo-surface-duo2"
    else
        bad "apt-get install openduo-surface-duo2"
        tail -40 /tmp/install.log
    fi
    common_checks
    REL=$(dpkg-query -W -f '${Depends}' openduo-kernel-surface-duo2 | sed 's/^openduo-kernel-surface-duo2-\([^ ]*\).*/\1/')
    echo "$REL" >/tmp/rel.first
    kernel_entry "$REL"
    check "a new kernel is tried 3 times" "[ -e /boot/efi/loader/entries/openduo-$REL+3.conf ]"

    echo "== the boot entry's life (duo2-boot, the boot from it simulated)"
    export DUO2_BOOT_CMDLINE="quiet openduo.entry=openduo-$REL"
    check "status: the new kernel is being tried" "duo2-boot status --json | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d[\"kernels\"][0][\"state\"] == \"trying\"'"
    duo2-boot bless >/dev/null
    check "bless: the entry loses its counter" "[ -e /boot/efi/loader/entries/openduo-$REL.conf ] && ! ls /boot/efi/loader/entries/openduo-$REL+* 2>/dev/null"
    unset DUO2_BOOT_CMDLINE
    check "duo2-updates status answers" "duo2-updates status | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d[\"ok\"] and d[\"conf\"][\"channel\"] == \"stable\"'"

    echo "== the owner's own settings, which an upgrade must keep"
    sed -i 's/^MAX_CLIENTS=.*/MAX_CLIENTS=3/' /etc/duo2/hotspot.conf
    systemctl disable -q duo-brightness.service
    echo duo-brightness.service >/tmp/owner-disabled
    echo '# a local edit' >>/etc/duo-wc/duo-wc.conf
}

stage_upgrade() {
    echo "== the testing channel (developer mode), then the upgrade"
    sed -i 's/^developer_mode = .*/developer_mode = on/' /etc/duo-devmode.conf
    check "duo2-updates set channel testing" "duo2-updates set channel testing | grep -q '\"ok\": true'"
    apt-get update -q >/tmp/update2.log 2>&1
    check "testing is read" "grep -q 'file:/repo testing InRelease' /tmp/update2.log"
    local before
    before=$(dpkg-query -W -f '${Version}' openduo-maint)
    esp_sums >/tmp/esp.sums
    if "${APT[@]}" dist-upgrade >/tmp/upgrade.log 2>&1; then ok "apt-get dist-upgrade"; else bad "apt-get dist-upgrade"; tail -40 /tmp/upgrade.log; fi
    check "openduo-maint upgraded" "dpkg --compare-versions \"\$(dpkg-query -W -f '\${Version}' openduo-maint)\" gt '$before'"
    common_checks
    check "the new version's file landed" "grep -q 'container-test: the next version' /etc/duo-firewall/firewall.nft"
    check "the owner's hotspot setting kept" "grep -qx 'MAX_CLIENTS=3' /etc/duo2/hotspot.conf"
    check "the owner's duo-wc.conf edit kept" "grep -q '# a local edit' /etc/duo-wc/duo-wc.conf"
    check "a unit the owner disabled stays disabled" "! systemctl is-enabled -q duo-brightness.service"
    local first next
    first=$(cat /tmp/rel.first)
    next=$(dpkg-query -W -f '${Depends}' openduo-kernel-surface-duo2 | sed 's/^openduo-kernel-surface-duo2-\([^ ]*\).*/\1/')
    check "the kernel moved to a new build" "[ '$next' != '$first' ]"
    kernel_entry "$next"
    check "the new kernel is tried 3 times" "[ -e /boot/efi/loader/entries/openduo-$next+3.conf ]"
    check "the previous (good) kernel stays" "[ -e /boot/efi/loader/entries/openduo-$first.conf ] && [ -e /boot/efi/openduo/$first/Image ]"
    check "the boot order: new, previous, duo2.conf, rescue" "python3 -I - <<'EOF'
import os, sys
sys.path.insert(0, '/usr/local/lib/duo2-maint')
from duo2maint import bootentry as be
E = '/boot/efi/loader/entries'
fs = sorted(os.listdir(E))
order = [f for f, _ in be.boot_order([(f, be.parse_entry(open(os.path.join(E, f)).read())) for f in fs])]
assert order[0] == 'openduo-$next+3.conf' and order[1] == 'openduo-$first.conf', order
assert order[-2:] == ['duo2.conf', 'rescue.conf'], order
EOF"
    echo "== the new kernel fails its checks: marked bad, the previous one boots first"
    DUO2_BOOT_CMDLINE="openduo.entry=openduo-$next" duo2-boot bad >/dev/null
    check "bad: no tries left" "[ -e /boot/efi/loader/entries/openduo-$next+0-3.conf ]"
    check "the previous kernel boots next" "duo2-boot status --json | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d[\"boots_next\"] == \"openduo-$first.conf\", d[\"boots_next\"]'"
    echo "== an old kernel package goes, its entry and ESP files with it"
    DUO2_BOOT_CMDLINE="openduo.entry=openduo-$first" duo2-boot obsolete >/tmp/obsolete
    check "nothing obsolete while both are kept" "[ ! -s /tmp/obsolete ]"
    if "${APT[@]}" purge "openduo-kernel-surface-duo2-$next" openduo-kernel-surface-duo2 openduo-surface-duo2 >/tmp/purge.log 2>&1; then
        ok "purge the bad kernel"
    else
        bad "purge the bad kernel"; tail -20 /tmp/purge.log
    fi
    check "its entry is gone" "! ls /boot/efi/loader/entries/openduo-$next* 2>/dev/null"
    check "its ESP files are gone" "[ ! -e /boot/efi/openduo/$next ]"
    check "its modules are gone" "[ ! -e /usr/lib/modules/$next ]"
    check "the hand-managed ESP entries untouched" "esp_sums | cmp -s - /tmp/esp.sums"
}

case ${1:-all} in
install) stage_install ;;
upgrade) stage_upgrade ;;
all) stage_install; stage_upgrade ;;
esac
echo "== $fails failure(s)"
[ $fails = 0 ]
