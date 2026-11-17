#!/usr/bin/env bash
# Put the sensor stack on the phone over the network (scripts/lib/remote.sh):
#
#   out/duo2-sensors-bin/  hexagonrpcd (patched), libssc, iio-sensor-proxy 3.9
#                          -> /usr/local                 (scripts/build-sensors.sh)
#   out/duo2-sensors/      the stock half: registry, json config, DSP skeletons,
#                          device facts -> /usr/share/qcom/sm8350/Microsoft/surfaceduo2
#                                                        (scripts/build-sensors-runtime.sh)
#   sensors/duo-posed/     duo-posed, duo2-ssc -> /usr/local/bin, /usr/local/lib/duo-posed
#   sensors/dist/          units, drop-ins, udev rule, polkit rule (iio-sensor-proxy
#                          claims without a logind session), /etc/duo-posed.conf(.dist)
#
#   scripts/install-sensors.sh            install, (re)start hexagonrpcd, restart
#                                         duo-posed if it runs
#   scripts/install-sensors.sh --enable   ... and enable hexagonrpcd + duo-posed at boot
#
# /etc/duo-posed.conf is written only when absent (local edits stay).
# duo-posed runs as duo-wc's user (its socket is that user's).
#
# A freshly built image already has the free half (out/duo2-sensors-bin, duo-posed,
# sensors/dist; rootfs/install-components.sh) with hexagonrpcd and duo-posed
# enabled; only the stock half is missing there, and their units skip until it
# is installed: on a fresh image this script is the post-install step for it.
set -euo pipefail
R=$(cd "$(dirname "$0")/.." && pwd)
. "$R/scripts/lib/remote.sh"
enable=0
[ "${1:-}" = --enable ] && enable=1

for d in "$R/out/duo2-sensors-bin/usr/local/bin" "$R/out/duo2-sensors/sensors/registry"; do
    [ -d "$d" ] || { echo "missing $d: run scripts/build-sensors.sh and scripts/build-sensors-runtime.sh" >&2; exit 1; }
done

stage=$(mktemp -d)
trap 'rm -rf "$stage"' EXIT
mkdir -p "$stage/bin" "$stage/qcom" "$stage/posed" "$stage/dist"
cp -a "$R/out/duo2-sensors-bin/." "$stage/bin/"
cp -a "$R/out/duo2-sensors/." "$stage/qcom/"
cp -a "$R/sensors/duo-posed/duo-posed" "$R/sensors/duo-posed/duo2-ssc" \
      "$R/sensors/duo-posed/duo2_ssc.py" "$R/sensors/duo-posed/posepolicy.py" "$stage/posed/"
cp -a "$R/sensors/dist/." "$stage/dist/"

read -r -d '' REMOTE <<'EOF' || true
set -euo pipefail
t=$(mktemp -d); trap 'rm -rf "$t"' EXIT
tar -C "$t" -xzf -
Q=/usr/share/qcom/sm8350/Microsoft/surfaceduo2
getent passwd duo-posed >/dev/null ||
    sudo useradd --system --user-group --no-create-home --home-dir /nonexistent \
        --shell /usr/sbin/nologin --comment "duo-posed (sensors drive duo-wc)" duo-posed

# libssc's runtime, and polkitd for iio-sensor-proxy's claims (a bus reload
# makes the new polkit D-Bus policy count).
if ! dpkg -s libprotobuf-c1 polkitd >/dev/null 2>&1; then
    sudo apt-get install -y -qq --no-install-recommends libprotobuf-c1 polkitd >/dev/null
    sudo systemctl reload dbus
fi
sudo tar -C "$t/bin" -cf - . | sudo tar -C / --no-same-owner --keep-directory-symlink -xf -
sudo ldconfig
sudo rm -rf "$Q.new"; sudo mkdir -p "$(dirname "$Q")"
sudo cp -r "$t/qcom" "$Q.new"; sudo chown -R root:root "$Q.new"
sudo rm -rf "$Q.old"; [ -e "$Q" ] && sudo mv "$Q" "$Q.old"; sudo mv "$Q.new" "$Q"; sudo rm -rf "$Q.old"

sudo install -d /usr/local/lib/duo-posed
sudo install -m 0644 "$t/posed/duo2_ssc.py" "$t/posed/posepolicy.py" /usr/local/lib/duo-posed/
sudo install -m 0755 "$t/posed/duo-posed" "$t/posed/duo2-ssc" /usr/local/bin/
sudo install -m 0644 "$t/dist/duo-posed.conf" /etc/duo-posed.conf.dist
[ -e /etc/duo-posed.conf ] || sudo install -m 0644 "$t/dist/duo-posed.conf" /etc/duo-posed.conf
sudo install -D -m 0644 "$t/dist/hexagonrpcd-duo2.conf" /etc/systemd/system/hexagonrpcd.service.d/duo2.conf
sudo install -D -m 0644 "$t/dist/iio-sensor-proxy-duo2.conf" /etc/systemd/system/iio-sensor-proxy.service.d/duo2.conf
sudo install -m 0644 "$t/dist/90-duo2-sensors.rules" /etc/udev/rules.d/90-duo2-sensors.rules
sudo install -D -m 0644 "$t/dist/50-duo2-sensors.rules" /etc/polkit-1/rules.d/50-duo2-sensors.rules
sudo install -m 0644 "$t/dist/duo-posed.service" /etc/systemd/system/duo-posed.service
sudo systemctl daemon-reload
sudo udevadm control --reload
if [ "$ENABLE" = 1 ]; then
    sudo systemctl enable hexagonrpcd.service duo-posed.service
fi
sudo systemctl restart hexagonrpcd.service
systemctl is-active -q duo-posed.service && sudo systemctl restart duo-posed.service
echo "installed: hexagonrpcd $(systemctl is-active hexagonrpcd), duo-posed $(systemctl is-active duo-posed)" \
     "(enabled: $(systemctl is-enabled hexagonrpcd 2>/dev/null)/$(systemctl is-enabled duo-posed 2>/dev/null)), user duo-posed"
EOF
tar -C "$stage" -czf - . | duo2_ssh "ENABLE=$enable bash -c $(printf '%q' "$REMOTE")"
