#!/usr/bin/env bash
# Rebuild only the ESP (out/images/esp.img and esp.simg): systemd-boot from
# the rootfs tarball, the kernel Image + DTB from the kernel build, the
# initramfs (twice: the boot entry's and the rescue entry's copy), and the
# loader entries (scripts/lib/esp-entries.sh).
#
#   scripts/build-esp.sh
#   OPTIONS="..." scripts/build-esp.sh        other kernel options
#   KOUT=DIR scripts/build-esp.sh             another kernel build (default out/linux)
#   ESP_EXTRA_TAR=FILE scripts/build-esp.sh   more files for the ESP, a tar of
#       its tree (an image installed from the packages: the kernel package's
#       /boot/efi/openduo/<release>/ and its entry, scripts/build-images.sh)
set -euo pipefail
R=$(cd "$(dirname "$0")/.." && pwd)
O=$R/out
KOUT=$(cd "${KOUT:-$O/linux}" && pwd)
KVER=$(cat "$KOUT/include/config/kernel.release")
EXTRA=()
if [ -n "${ESP_EXTRA_TAR:-}" ]; then
    EXTRA=(-v "$(cd "$(dirname "$ESP_EXTRA_TAR")" && pwd)/$(basename "$ESP_EXTRA_TAR")":/extra.tar:ro)
fi

KOUT=$KOUT "$R/scripts/build-initrd.sh"
rm -rf "$O/esp-entries"
"$R/scripts/lib/esp-entries.sh" "$O/esp-entries" "$KVER"

echo "== ESP"
docker run --rm --platform linux/amd64 -e LANG=C.UTF-8 -e DEBIAN_FRONTEND=noninteractive \
    -v "$O":/out -v "$KOUT/arch/arm64/boot":/kboot:ro "${EXTRA[@]}" debian:trixie bash -c "
set -e
apt-get update -qq >/dev/null && apt-get install -y -qq --no-install-recommends dosfstools mtools android-sdk-libsparse-utils >/dev/null 2>&1
cd /out
tar -xf rootfs.tar -C /tmp ./usr/lib/systemd/boot/efi/systemd-bootaa64.efi 2>/dev/null || tar -xf rootfs.tar -C /tmp usr/lib/systemd/boot/efi/systemd-bootaa64.efi
rm -f images/esp.img
mkfs.fat -F 32 -S 4096 -n ESP -C images/esp.img 1048576 >/dev/null
export MTOOLS_SKIP_CHECK=1
mmd -i images/esp.img ::/EFI ::/EFI/BOOT ::/loader ::/loader/entries ::/duo2
mcopy -i images/esp.img /tmp/usr/lib/systemd/boot/efi/systemd-bootaa64.efi ::/EFI/BOOT/BOOTAA64.EFI
mcopy -i images/esp.img /out/esp-entries/loader.conf ::/loader/loader.conf
mcopy -i images/esp.img /out/esp-entries/duo2.conf /out/esp-entries/rescue.conf ::/loader/entries/
mcopy -i images/esp.img /kboot/Image /out/initrd.img ::/duo2/
mcopy -i images/esp.img /out/initrd.img ::/duo2/initrd-boot.img
mcopy -i images/esp.img /kboot/dts/qcom/sm8350-microsoft-surface-duo2.dtb /kboot/dts/qcom/sm8350-microsoft-surface-duo2-bringup.dtb /kboot/dts/qcom/sm8350-microsoft-surface-duo2-bringup-j.dtb ::/duo2/
if [ -f /extra.tar ]; then
    mkdir -p /tmp/extra && tar -xf /extra.tar -C /tmp/extra
    cd /tmp/extra
    find . -mindepth 1 -type d | sort | while read -r d; do
        mdir -i /out/images/esp.img \"::/\${d#./}\" >/dev/null 2>&1 || mmd -i /out/images/esp.img \"::/\${d#./}\"
    done
    find . -type f | sort | while read -r f; do mcopy -n -i /out/images/esp.img \"\$f\" \"::/\${f#./}\"; done
    cd /out
fi
mdir -i images/esp.img -/ :: | grep -E 'BOOTAA64|Image|initrd|dtb|conf'
img2simg images/esp.img images/esp.simg
chown -R $(id -u):$(id -g) /out/images
"
ls -la "$O/images/esp.simg"
