# duo2-os sensor stack, built for the phone (Debian trixie arm64, under qemu):
#   hexagonrpcd  (linux-msm/hexagonrpc v0.3.2 = Debian's version, + patches/hexagonrpc)
#                serves the SLPI's FastRPC file calls (registry, json config)
#   libssc       (Dylan Van Assche, v0.4.4 + patches/libssc: a light sensor may be
#                lightfusion / ambient_light_oc) the SSC client library (not in Debian)
#   iio-sensor-proxy 3.9 with SSC support (Debian has 3.7, which predates it)
# Everything installs under /usr/local into /staging; scripts/build-sensors.sh
# exports it to out/duo2-sensors-bin/. All three are free software (GPL-3+);
# no stock (Qualcomm / Microsoft) file is involved here.
FROM --platform=linux/arm64 debian:trixie
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates git build-essential meson ninja-build pkg-config \
        libglib2.0-dev libqmi-glib-dev libqrtr-glib-dev libprotobuf-c-dev \
        protobuf-c-compiler protobuf-compiler python3-dev \
        libgudev-1.0-dev libpolkit-gobject-1-dev systemd-dev udev \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

ARG HEXAGONRPC_REV=v0.3.2
ARG LIBSSC_REV=v0.4.4
ARG IIO_SENSOR_PROXY_REV=3.9
# true logs every FastRPC file call (for debugging what the DSP reads)
ARG HEXAGONRPCD_VERBOSE=false
WORKDIR /build

COPY patches/libssc/ /patches/libssc/
RUN git clone -q https://codeberg.org/DylanVanAssche/libssc.git \
    && cd libssc && git checkout -q "$LIBSSC_REV" \
    && git -c user.name=duo2 -c user.email=duo2@localhost am -q /patches/libssc/*.patch \
    && meson setup _b --prefix=/usr/local --buildtype=release \
    && meson compile -C _b \
    && meson install -q -C _b \
    && DESTDIR=/staging meson install -q -C _b \
    && ldconfig

RUN git clone -q https://gitlab.freedesktop.org/hadess/iio-sensor-proxy.git \
    && cd iio-sensor-proxy && git checkout -q "$IIO_SENSOR_PROXY_REV" \
    && PKG_CONFIG_PATH=/usr/local/lib/aarch64-linux-gnu/pkgconfig \
       meson setup _b --prefix=/usr/local --buildtype=release -Dssc-support=enabled \
           -Dudevrulesdir=/usr/local/lib/udev/rules.d \
           -Dsystemdsystemunitdir=/usr/local/lib/systemd/system \
    && meson compile -C _b \
    && DESTDIR=/staging meson install -q -C _b

# hexagonrpcd last: its patches change most often.
COPY patches/ /patches/
RUN git clone -q https://github.com/linux-msm/hexagonrpc.git \
    && cd hexagonrpc && git checkout -q "$HEXAGONRPC_REV" \
    && git -c user.name=duo2 -c user.email=duo2@localhost am -q /patches/hexagonrpc/*.patch \
    && meson setup _b --prefix=/usr/local --buildtype=release -Dhexagonrpcd_verbose=$HEXAGONRPCD_VERBOSE \
    && meson compile -C _b \
    && DESTDIR=/staging meson install -q -C _b

# Only what runs on the phone: no headers, static libs, pkg-config, tests, docs.
# Upstream v0.3.2's libhexagonrpc has no soversion (libhexagonrpc.so; Debian's
# package adds .so.0.3), so ours ships beside Debian's without a clash. The
# iio-sensor-proxy unit, udev rule, D-Bus and polkit files are Debian's (the
# image's package); rootfs/overlay points its unit at the binary built here.
# Runtime: libprotobuf-c1, libqmi-glib5, libqrtr-glib0, libgudev-1.0-0.
RUN cd /staging/usr/local && rm -rf include share/doc share/gtk-doc share/man \
        lib/systemd lib/udev share/dbus-1 share/polkit-1 etc \
        lib/aarch64-linux-gnu/pkgconfig lib/aarch64-linux-gnu/*.a libexec/installed-tests \
        share/installed-tests lib/python3* \
    && rm -rf /staging/usr/share \
    && find /staging -name '*.a' -delete \
    && find /staging -type f | sort
