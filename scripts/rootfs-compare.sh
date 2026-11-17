#!/usr/bin/env bash
# Does the recipe (rootfs/Dockerfile + install-components.sh) give what is
# on the phone now? Builds the root file system's Docker image only
# (ROOTFS_ONLY=1 scripts/build-images.sh, tag duo2-rootfs-check: no kernel,
# no images, nothing flashed) and compares, into out/rootfs-compare/:
#   packages.diff  dpkg's package list (name, version), image vs phone
#   files.diff     the files of the system parts that are ours or configured
#                  (/etc, /usr/local, /opt, /var/lib/duo2*, and the packages'
#                  directories' listing), image vs phone, with sizes
# HEAVY (arm64 under qemu, tens of minutes): run it only when no kernel build
# or other heavy job runs (`pgrep -c cc1`, `pgrep -af "docker|qemu"`).
#
#   scripts/rootfs-compare.sh [--no-build]
set -euo pipefail
. "$(dirname "$0")/lib/remote.sh"
O=$R/out/rootfs-compare
TAG=duo2-rootfs-check
mkdir -p "$O"
if [ "${1:-}" != "--no-build" ]; then
    ROOTFS_ONLY=1 ROOTFS_TAG=$TAG "$R/scripts/build-images.sh"
fi
# What changes with use and differs between any two phones is left out.
LIST='set -e
pk() { dpkg-query -W -f "\${Package} \${Version}\n" | sort; }
fl() { find /etc /usr/local /opt /var/lib/duo2-lock /var/lib/duo2 -xdev \
         \( -path /etc/ssh/ssh_host_\* -o -path /etc/machine-id -o -path /etc/hostname \
            -o -path /etc/hosts -o -path /etc/resolv.conf -o -path /etc/adjtime \
            -o -path /etc/wpa_supplicant/\* -o -path /etc/NetworkManager/system-connections/\* \
            -o -name "*.pyc" -o -path "*/__pycache__*" -o -path /etc/.pwd.lock \
            -o -path /etc/shadow\* -o -path /etc/gshadow\* -o -path /etc/subuid\* -o -path /etc/subgid\* \
            -o -path /etc/duo2/factory-addresses -o -path /etc/ld.so.cache \
            -o -path "/etc/systemd/system/*.wants/*" -o -path /etc/duo2/esim-fake \) -prune \
         -o \( -type f -o -type l \) -printf "%p %s %M %u:%g\n" 2>/dev/null | sort; }
'
docker run --rm --platform linux/arm64 --entrypoint /bin/sh "$TAG" -c "$LIST pk" >"$O/packages.image"
docker run --rm --platform linux/arm64 --entrypoint /bin/sh "$TAG" -c "$LIST fl" >"$O/files.image"
duo2_ssh "sudo sh -c '$LIST pk'" >"$O/packages.phone"
duo2_ssh "sudo sh -c '$LIST fl'" >"$O/files.phone"
diff "$O/packages.image" "$O/packages.phone" >"$O/packages.diff" || true
diff "$O/files.image" "$O/files.phone" >"$O/files.diff" || true
echo "packages: image $(wc -l <"$O/packages.image"), phone $(wc -l <"$O/packages.phone"):" \
     "$(grep -c '^<' "$O/packages.diff" || true) only / other version in the image," \
     "$(grep -c '^>' "$O/packages.diff" || true) on the phone"
echo "files: image $(wc -l <"$O/files.image"), phone $(wc -l <"$O/files.phone"):" \
     "$(grep -c '^<' "$O/files.diff" || true) differ in the image, $(grep -c '^>' "$O/files.diff" || true) on the phone"
echo "details: $O/packages.diff, $O/files.diff"
