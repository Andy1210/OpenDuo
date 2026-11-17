#!/usr/bin/env bash
# Assemble the firmware tree the root image ships in /lib/firmware
# (firmware/linux) from the phone's OWN stock partitions.
#
# None of these files may be redistributed: they are Qualcomm / Microsoft
# firmware signed for this device. The repository carries only this recipe;
# every user builds the tree from a dump of their own phone.
#
#   scripts/collect-firmware.sh [STOCK] [OUT]
#
#   STOCK (default firmware/stock) holds the extracted stock partitions:
#     modem/image/   NON-HLOS (FAT, modem_a): DSP .mdt/.bNN, WLAN firmware
#     bt/image/      BTFM (FAT, bluetooth_a): Bluetooth patch + NVM files
#     vendor/firmware/  /vendor/firmware of the vendor image (super or OTA)
#   OUT (default firmware/linux) is replaced.
#
# Layout produced (what the mainline drivers request):
#   qcom/a660_sqe.fw, qcom/a660_gmu.bin          Adreno 660 microcode
#   qcom/sm8350/microsoft/a660_zap.mbn           GPU zap shader (DT firmware-name)
#   qcom/sm8350/microsoft/{adsp,cdsp,slpi,modem}.mbn
#                                                remoteprocs, .mdt + .bNN joined
#   qcom/sm8350/microsoft/vpu20_4v.mbn           video codec (iris, DT firmware-name):
#       VPU 2.0 with four pipes, Gen1 HFI (video-firmware.1.0), .mdt + .bNN joined
#   qcom/sm8350/microsoft/modem_pr/            modem configuration (MCFG), via tqftpserv
#   qca/hpbtfw10.tlv, qca/hpnv10.*  (+ wcn* names)  WCN6855 Bluetooth (hci_uart qca)
#   ath11k/WCN6855/hw1.1/{amss,m3,board,regdb}.bin  WLAN: the chip is a WCN6851
#       (QCA6490 v1, SOC id 1.0x10), which needs the ath11k "WCN6855 hw1.1"
#       support. Only the stock mobile firmware boots on it, and its OWN regdb
#       must be used: linux-firmware's regdb.bin crashes this firmware.
set -euo pipefail
R=$(cd "$(dirname "$0")/.." && pwd)
STOCK=${1:-$R/firmware/stock}
OUT=${2:-$R/firmware/linux}

need() { [ -e "$1" ] || { echo "missing: $1" >&2; exit 1; }; }
for f in modem/image/amss.bin modem/image/m3.bin modem/image/bdwlan.elf modem/image/regdb.bin \
         bt/image/hpbtfw10.tlv vendor/firmware/a660_sqe.fw vendor/firmware/a660_gmu.bin \
         vendor/firmware/a660_zap.elf vendor/firmware/ipa_fws.mdt vendor/firmware/vpu20_4v.mdt; do
    need "$STOCK/$f"
done
for p in adsp cdsp slpi modem; do need "$STOCK/modem/image/$p.mdt"; done

tmp=$(mktemp -d "$OUT.new.XXXXXX")
trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/qcom/sm8350/microsoft" "$tmp/qca" "$tmp/ath11k/WCN6855/hw1.1"

# GPU
cp "$STOCK/vendor/firmware/a660_sqe.fw" "$STOCK/vendor/firmware/a660_gmu.bin" "$tmp/qcom/"
cp "$STOCK/vendor/firmware/a660_zap.elf" "$tmp/qcom/sm8350/microsoft/a660_zap.mbn"

# Remoteprocs: the kernel loads one .mbn per DSP.
for p in adsp cdsp slpi modem; do
    python3 -I "$R/scripts/mdt2mbn.py" "$STOCK/modem/image/$p.mdt" "$tmp/qcom/sm8350/microsoft/$p.mbn"
done
# IPA GSI firmware: loaded by Linux (qcom,gsi-loader = "self")
python3 -I "$R/scripts/mdt2mbn.py" "$STOCK/vendor/firmware/ipa_fws.mdt" "$tmp/qcom/sm8350/microsoft/ipa_fws.mbn"
# Video codec (iris): the signed image, not vpu20_4v_unsigned; the other
# vpu20_{1,2}v are for SoCs with fewer pipes. Joined, it is byte-identical to
# the stock vpu20_4v.mbn.
python3 -I "$R/scripts/mdt2mbn.py" "$STOCK/vendor/firmware/vpu20_4v.mdt" "$tmp/qcom/sm8350/microsoft/vpu20_4v.mbn"

# Modem configuration (MCFG and friends): the modem fetches them over QRTR
# TFTP as /readonly/firmware/image/modem_pr/..., which tqftpserv resolves in
# the modem's firmware directory.
cp -r "$STOCK/modem/image/modem_pr" "$tmp/qcom/sm8350/microsoft/modem_pr"

# Bluetooth: btqca asks for qca/hpbtfw10.tlv + qca/hpnv10.bXX (by board id)
# and, for the WCN6855 compatible, the same files with a "wcn" prefix.
cp "$STOCK"/bt/image/hpbtfw10.tlv "$STOCK"/bt/image/hpnv10.* "$tmp/qca/"
(cd "$tmp/qca" && for f in hpbtfw10.tlv hpnv10.*; do ln -s "$f" "wcn$f"; done)

# WLAN
w=$tmp/ath11k/WCN6855/hw1.1
cp "$STOCK/modem/image/amss.bin" "$STOCK/modem/image/m3.bin" "$STOCK/modem/image/regdb.bin" "$w/"
cp "$STOCK/modem/image/bdwlan.elf" "$w/board.bin"

chmod -R u=rwX,go=rX "$tmp"
rm -rf "$OUT"
mv "$tmp" "$OUT"
trap - EXIT
echo "firmware tree: $OUT ($(find "$OUT" -type f | wc -l) files, $(du -sh "$OUT" | cut -f1))"
