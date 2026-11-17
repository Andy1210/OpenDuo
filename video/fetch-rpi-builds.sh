#!/usr/bin/env bash
# Fetch, verify and unpack (here, on the build machine) the two Raspberry Pi
# OS builds the browser video path can use on the phone. Nothing is installed:
# scripts/duo2-video-test install-rpi copies the result to the phone.
#
#   video/fetch-rpi-builds.sh            -> out/video-rpi/
#
# Why these (docs/hardware/video.md):
#   - FFmpeg 7.1.5+rpt (libavcodec61 / libavutil59 / libswresample5): Debian's
#     FFmpeg build of the same release, plus Raspberry Pi's V4L2 work, whose
#     *_v4l2m2m decoders hand out DRM-PRIME frames (dmabufs). Firefox's V4L2
#     path accepts nothing else (upstream FFmpeg, Debian's included, only
#     gives copies), and mpv can then show them without copying. Used only
#     through LD_LIBRARY_PATH by the programs that ask for it; the system
#     FFmpeg stays Debian's.
#   - Chromium 156+rpt (chromium, chromium-common): Debian's Chromium package
#     rebuilt with Raspberry Pi's patches: VA-API off and V4L2 on as the only
#     hardware decoder, the GPU sandbox lets the decoder node through
#     (Debian's build refuses /dev/videoN there, so it needs
#     --disable-gpu-sandbox), and fixes to the V4L2 stateful decoder (polling
#     after POLLERR, instance counting). Unpacked to run from its own
#     directory beside Debian's chromium package, which stays installed.
#
# Trust: the archive's InRelease must carry a good signature from the
# Raspberry Pi archive key with the fingerprint below (the key itself comes
# from the archive's raspberrypi-archive-keyring package and is checked
# against the fingerprint), the package index must match the hash in it, and
# every .deb the hash in the index.
set -euo pipefail
R=$(cd "$(dirname "$0")/.." && pwd)
O=${O:-$R/out/video-rpi}
ARCHIVE=https://archive.raspberrypi.com/debian
SUITE=trixie
KEY_FPR=CF8A1AF502A2AA2D763BAE7E82B129927FA3303E
KEYRING_DEB=pool/main/r/raspberrypi-archive-keyring/raspberrypi-archive-keyring_2025.1+rpt1_all.deb
FFMPEG_PKGS="libavcodec61 libavutil59 libswresample5"
CHROMIUM_PKGS="chromium chromium-common"

mkdir -p "$O/debs" "$O/meta"
cd "$O/meta"
get() { curl -fsSL --retry 3 -m 600 -o "$2" "$ARCHIVE/$1"; }

echo "== archive key"
get "$KEYRING_DEB" keyring.deb
rm -rf keyring && mkdir keyring && dpkg-deb -x keyring.deb keyring
KEY=keyring/usr/share/keyrings/raspberrypi-archive-keyring.pgp
[ -f "$KEY" ] || KEY=keyring/usr/share/keyrings/raspberrypi-archive-keyring.gpg
fpr=$(gpg --show-keys --with-colons "$KEY" 2>/dev/null | awk -F: '$1 == "fpr" { print $10; exit }')
[ "$fpr" = "$KEY_FPR" ] || { echo "archive key fingerprint $fpr, expected $KEY_FPR" >&2; exit 1; }

echo "== index"
get "dists/$SUITE/InRelease" InRelease
gpgv --keyring "$KEY" --output Release InRelease 2>gpgv.log || { cat gpgv.log >&2; exit 1; }
grep -q "$KEY_FPR" gpgv.log
want=$(awk '/^SHA256:/ { s = 1; next } /^[A-Za-z]/ { s = 0 } s && $3 == "main/binary-arm64/Packages.gz" { print $1 }' Release)
[ -n "$want" ] || { echo "no main/binary-arm64/Packages.gz in Release" >&2; exit 1; }
get "dists/$SUITE/main/binary-arm64/Packages.gz" Packages.gz
echo "$want  Packages.gz" | sha256sum -c --quiet
gzip -dc Packages.gz > Packages

# field PKG NAME: a field of the newest stanza of PKG (Architecture arm64 or all)
field() {
    awk -v p="$1" -v f="$2" -v RS= -F'\n' '{
        pkg = ""; val = ""
        for (i = 1; i <= NF; i++) {
            if ($i ~ "^Package: ") pkg = substr($i, 10)
            if (index($i, f ": ") == 1) val = substr($i, length(f) + 3)
        }
        if (pkg == p) last = val
    } END { print last }' Packages
}

echo "== packages"
: > versions.txt
for p in $FFMPEG_PKGS $CHROMIUM_PKGS; do
    file=$(field "$p" Filename); sum=$(field "$p" SHA256); ver=$(field "$p" Version)
    [ -n "$file" ] && [ -n "$sum" ] || { echo "$p: not in the index" >&2; exit 1; }
    deb=$O/debs/$(basename "$file")
    if ! echo "$sum  $deb" | sha256sum -c --quiet >/dev/null 2>&1; then
        get "$file" "$deb"
        echo "$sum  $deb" | sha256sum -c --quiet
    fi
    printf '%s %s %s\n' "$p" "$ver" "$sum" | tee -a versions.txt
done

echo "== unpack"
rm -rf "$O/ffmpeg-v4l2" "$O/chromium" && mkdir -p "$O/ffmpeg-v4l2/lib" "$O/chromium"
tmp=$(mktemp -d)
for p in $FFMPEG_PKGS; do
    dpkg-deb -x "$O/debs/$(basename "$(field "$p" Filename)")" "$tmp"
done
cp -a "$tmp"/usr/lib/aarch64-linux-gnu/lib{avcodec.so.61,avutil.so.59,swresample.so.5}* "$O/ffmpeg-v4l2/lib/"
cp -a "$tmp"/usr/share/doc/libavcodec61/copyright "$O/ffmpeg-v4l2/copyright"
rm -rf "$tmp"
for p in $CHROMIUM_PKGS; do
    dpkg-deb -x "$O/debs/$(basename "$(field "$p" Filename)")" "$O/chromium"
done
cp "$O/meta/versions.txt" "$O/versions.txt"
ls -la "$O/ffmpeg-v4l2/lib"
du -sh "$O/chromium"
echo "ok: $O (versions.txt)"
