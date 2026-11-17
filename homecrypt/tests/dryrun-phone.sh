#!/bin/bash
# tests/dryrun.py ON THE PHONE (scripts/lib/remote.sh), with the phone's own
# kernel (its fscrypt, HCTR2, IV_INO_LBLK_64 on ext4), on a throwaway 3 GB
# loop image in /var/tmp and a throwaway user `duo`: never the owner's home,
# never the real lock store.
#
#   homecrypt/tests/dryrun-phone.sh
#
# It runs as root in a private mount namespace (MemoryMax 1.5G) where /etc and
# /var/lib are overlays on a tmpfs (useradd, the dconf profile: gone with the
# namespace), /var/lib/duo2-lock and /run/duo2-home are empty tmpfs, and
# systemctl and mmcli are no-op stand-ins: settle would otherwise stop the
# running duo-wc and the user's session. The image, the loop device and the
# staged copy are removed at the end; what it prints afterwards shows that
# nothing outside changed.
set -eu
here=$(cd "$(dirname "$0")" && pwd)
. "$here/../../scripts/lib/remote.sh"

stage=$(duo2_stage homecrypt-dryrun)
work=/var/tmp/openduo-dryrun
duo2_ssh "rm -rf $stage && mkdir -p $stage/homecrypt"
tar -C "$here/.." --exclude=__pycache__ --mode=go-w -cf - . | duo2_ssh "tar -C $stage/homecrypt --no-same-owner -xf -"
cat <<'EOF' | duo2_ssh "cat > $stage/inner.sh"
set -eu
top=$1 work=$2
mkdir -p "$work/ns"
mount -t tmpfs -o mode=0700,size=64m tmpfs "$work/ns"
mkdir -p "$work/ns/etc-up" "$work/ns/etc-wk" "$work/ns/lib-up" "$work/ns/lib-wk" "$work/mnt"
mount -t overlay overlay -o "lowerdir=/etc,upperdir=$work/ns/etc-up,workdir=$work/ns/etc-wk" /etc
mount -t overlay overlay -o "lowerdir=/var/lib,upperdir=$work/ns/lib-up,workdir=$work/ns/lib-wk" /var/lib
mkdir -p /run/duo2-home
mount -t tmpfs -o mode=0755,size=1m tmpfs /run/duo2-home
mount -t tmpfs -o mode=0700,size=1m tmpfs /var/lib/duo2-lock
printf '#!/bin/sh\nexit 0\n' > "$work/ns/stub"
chmod 755 "$work/ns/stub"
mount --bind "$work/ns/stub" /usr/bin/systemctl
mount --bind "$work/ns/stub" /usr/bin/mmcli
DRYRUN_TOP=$top DRYRUN_IMG=$work/fs.img DRYRUN_MNT=$work/mnt exec python3 -B "$top/tests/dryrun.py"
EOF
duo2_ssh "sudo mkdir -p $work && sudo modprobe overlay
sudo systemd-run --scope --quiet -p MemoryMax=1500M -p IOWeight=20 nice -n 10 \
    unshare --mount --propagation private sh $stage/inner.sh $stage/homecrypt $work
rc=\$?
echo \"== dry run rc \$rc\"
echo \"loop devices still on the image: \$(sudo losetup -a | grep -c $work)\"
sudo rm -rf $work
rm -rf $stage
getent passwd duo >/dev/null && echo 'a user duo exists outside!' || echo 'no user duo outside'
grep -s -q openduo /etc/dconf/profile/user && echo 'a dconf profile outside!' || echo 'no dconf profile outside'
echo \"the real lock store: \$(sudo ls /var/lib/duo2-lock | tr '\n' ' ')\"
exit \$rc"
