# lpac (the eSIM LPA, github.com/estkme-group/lpac) for the phone: aarch64,
# Debian trixie, with the QMI-over-QRTR APDU backend (the built-in eUICC sits
# behind the modem's UIM service) and curl for SM-DP+. Debian does not
# package lpac. Built by ./build.sh; nothing of it goes into git but this.
FROM debian:trixie
ARG DEBIAN_FRONTEND=noninteractive
RUN dpkg --add-architecture arm64 \
 && apt-get update -qq \
 && apt-get install -y -qq --no-install-recommends \
        ca-certificates git cmake make pkg-config crossbuild-essential-arm64 \
        libqmi-glib-dev:arm64 libqrtr-glib-dev:arm64 libglib2.0-dev:arm64 \
        libcurl4-openssl-dev:arm64 \
 && rm -rf /var/lib/apt/lists/*
