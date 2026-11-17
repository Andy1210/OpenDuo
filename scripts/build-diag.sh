#!/usr/bin/env bash
# Build Bjorn Andersson's DIAG router (github.com/andersson/diag, BSD-3) for
# the phone, with sensors/patches/diag, into out/duo2-diag-bin/diag-router.
#
#   scripts/build-diag.sh
#
# It is a debugging tool: the SLPI (and every other DSP) sends its debug
# messages (F3 / "MSG") over DIAG, which on SM8350 runs over QRTR (service
# 4097; the DSP connects to the DIAG control server the AP publishes). With
# the router running, sensors/tools/duo2-diag-log sets the message masks and
# prints what the sensor DSP says. Nothing here is installed by default.
#
# Built under qemu in the duo2-sensors image (scripts/build-sensors.sh makes
# it), against Debian's libqrtr; libudev (the rpmsg peripherals) is left out.
set -euo pipefail
R=$(cd "$(dirname "$0")/.." && pwd)
REV=23c12c167e93215853af4e59c021551767f6fec8
OUT=$R/out/duo2-diag-bin
mkdir -p "$OUT"
docker run --rm --platform linux/arm64 -u 0 \
    -v "$R/sensors/patches/diag:/patches:ro" -v "$OUT:/out" duo2-sensors bash -ec "
    apt-get update -qq >/dev/null && apt-get install -y -qq --no-install-recommends libqrtr-dev >/dev/null
    cd /tmp && git clone -q https://github.com/andersson/diag.git && cd diag && git checkout -q $REV
    git -c user.name=duo2 -c user.email=duo2@localhost am -q /patches/*.patch
    make -s HAVE_LIBUDEV=0 diag-router
    install -m 755 diag-router /out/diag-router
    chown $(id -u):$(id -g) /out/diag-router"
ls -la "$OUT"
