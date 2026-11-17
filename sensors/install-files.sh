#!/bin/sh
# Put the FREE half of the sensor stack in place, as root: in the image
# (rootfs/install-components.sh under qemu) and on the phone from its package
# (packaging/, docs/UPDATES.md): one list for both.
#
#   install-files.sh          (run from the staged directory:
#                             scripts/lib/stage-components.sh sensors)
#
#   bin/ -> /                 hexagonrpcd (patched), libssc, iio-sensor-proxy 3.9
#                             (out/duo2-sensors-bin, scripts/build-sensors.sh)
#   duo-posed                 the posture daemon (its own system user), the
#                             drop-ins, the udev and polkit rules
#   duo-brightness            automatic brightness from the light sensor
#
# The stock half (the DSP's own libraries and sensor registry, from the
# owner's phone) is a post-install step: scripts/install-sensors.sh. The
# config files are kept when present; the shipped ones are always .dist.
set -eu
here=$(cd "$(dirname "$0")" && pwd)
cd "$here"
tar -C bin -cf - . | tar -C / --no-same-owner --keep-directory-symlink -xf -
ldconfig
install -d /usr/local/lib/duo-posed
install -m 0644 duo2_ssc.py posepolicy.py /usr/local/lib/duo-posed/
install -m 0755 duo-posed duo2-ssc /usr/local/bin/
install -m 0644 duo-posed.conf /etc/duo-posed.conf.dist
[ -e /etc/duo-posed.conf ] || install -m 0644 duo-posed.conf /etc/duo-posed.conf
install -D -m 0644 hexagonrpcd-duo2.conf /etc/systemd/system/hexagonrpcd.service.d/duo2.conf
install -D -m 0644 iio-sensor-proxy-duo2.conf /etc/systemd/system/iio-sensor-proxy.service.d/duo2.conf
install -D -m 0644 90-duo2-sensors.rules /etc/udev/rules.d/90-duo2-sensors.rules
install -D -m 0644 50-duo2-sensors.rules /etc/polkit-1/rules.d/50-duo2-sensors.rules
# duo-posed runs as its own system user: duo-wc answers it on the admin
# socket (sensor_users), not the GUI user (docs/hardware/security.md).
getent passwd duo-posed >/dev/null ||
    useradd --system --user-group --no-create-home --home-dir /nonexistent \
        --shell /usr/sbin/nologin --comment "duo-posed (sensors drive duo-wc)" duo-posed
install -m 0644 duo-posed.service /etc/systemd/system/duo-posed.service
# duo-brightness: automatic brightness from the light sensor (root: it writes
# the backlight; acts only while /etc/duo2/brightness.conf says auto = on).
install -d /usr/local/lib/duo-brightness
install -m 0644 brightnesspolicy.py /usr/local/lib/duo-brightness/
install -m 0755 duo-brightness /usr/local/bin/
[ -e /etc/duo2/brightness.conf ] || install -D -m 0644 brightness.conf /etc/duo2/brightness.conf
install -m 0644 duo-brightness.service /etc/systemd/system/duo-brightness.service
