#!/bin/sh
# duo2-os's browser video pieces on the phone, as root (install.sh runs it
# there; docs/hardware/video.md). NOT installed by default (the owner, 2026-11-10: no
# browser launcher that runs Chromium with --disable-gpu-sandbox; YouTube
# goes through Pipeline, the Flatpak, which decodes on the video core
# without it). Without an argument this REMOVES what an earlier install put
# there; --launcher installs it:
#
#   duo2-youtube                    /usr/local/bin: YouTube (or a given page) in
#                                   Chromium on the video core, its own profile
#   duo2-youtube.desktop            /usr/local/share/applications: the app grid entry
#   chromium-codecs, -h264          /usr/local/share/duo2-os/: the extension that
#                                   hides AV1 (the -h264 variant VP9 and VP8 too)
#                                   from YouTube's player
#
# The launcher's profile (~/.config/duo2-youtube of the GUI user) is the
# user's: install.sh removes it with the launcher (--remove-profile USER).
#
# The Raspberry Pi OS builds that duo2-youtube prefers when present
# (/opt/duo2-chromium-rpi, /opt/duo2-ffmpeg-v4l2) are not installed here:
# an experiment, kept in the development repository.
#
#   install-files.sh [--launcher]
set -eu
here=$(cd "$(dirname "$0")" && pwd)
cd "$here"
if [ "${1:-}" != --launcher ]; then
    rm -f /usr/local/bin/duo2-youtube /usr/local/share/applications/duo2-youtube.desktop
    rm -rf /usr/local/share/duo2-os/chromium-codecs /usr/local/share/duo2-os/chromium-codecs-h264
    rmdir /usr/local/share/duo2-os 2>/dev/null || true
    exit 0
fi
install -m 0755 duo2-youtube /usr/local/bin/duo2-youtube
install -d -m 0755 /usr/local/share/applications /usr/local/share/duo2-os
install -m 0644 duo2-youtube.desktop /usr/local/share/applications/duo2-youtube.desktop
for v in "" -h264; do
    d=/usr/local/share/duo2-os/chromium-codecs$v
    install -d -m 0755 "$d"
    install -m 0644 chromium-codecs/manifest.json "$d/manifest.json"
    install -m 0644 chromium-codecs/codecs.js "$d/codecs.js"
done
sed -i "s/^    const HIDDEN = \['av01'\];/    const HIDDEN = ['av01', 'vp09', 'vp9', 'vp08', 'vp8'];/" \
    /usr/local/share/duo2-os/chromium-codecs-h264/codecs.js
grep -q "'vp09'" /usr/local/share/duo2-os/chromium-codecs-h264/codecs.js
