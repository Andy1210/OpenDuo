# 81voltd (the IMS Data service for VoLTE, gitlab.postmarketos.org/modem/81voltd)
# for the phone: aarch64, Debian trixie (whose 81voltd is 1.0.0). Built by
# ./build.sh; nothing of it goes into git but this, the patches and the unit.
FROM debian:trixie
ARG DEBIAN_FRONTEND=noninteractive
RUN dpkg --add-architecture arm64 \
 && apt-get update -qq \
 && apt-get install -y -qq --no-install-recommends \
        ca-certificates git meson ninja-build pkg-config crossbuild-essential-arm64 \
        libmm-glib-dev:arm64 libqmi-glib-dev:arm64 libqrtr-dev:arm64 libglib2.0-dev:arm64 \
 && rm -rf /var/lib/apt/lists/*
