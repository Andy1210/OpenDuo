# Cross-build image for duo-wc: x86_64 host, aarch64 Debian trixie target.
#
# The -dev packages are the target's (:arm64), so the binary links against
# exactly the library versions the phone runs (trixie, same as the rootfs).
# The same set for the host (amd64) is there only so the unit tests can run
# natively in the container.
FROM debian:trixie

ARG DEBIAN_FRONTEND=noninteractive
RUN dpkg --add-architecture arm64 \
 && apt-get update -qq \
 && apt-get install -y -qq --no-install-recommends \
        ca-certificates curl git pkg-config clang libclang-dev \
        crossbuild-essential-arm64 \
        libudev-dev:arm64 libgbm-dev:arm64 libxkbcommon-dev:arm64 \
        libegl-dev:arm64 libgles-dev:arm64 libwayland-dev:arm64 \
        libinput-dev:arm64 libseat-dev:arm64 libdisplay-info-dev:arm64 \
        libudev-dev libgbm-dev libxkbcommon-dev libegl-dev libgles-dev \
        libwayland-dev libinput-dev libseat-dev libdisplay-info-dev \
 && rm -rf /var/lib/apt/lists/*

# The toolchain lives outside CARGO_HOME, so the registry cache can be a
# volume owned by the calling user while the toolchain stays read-only.
ENV RUSTUP_HOME=/opt/rustup \
    PATH=/opt/cargo/bin:$PATH
RUN curl -sSf https://sh.rustup.rs | CARGO_HOME=/opt/cargo sh -s -- -y -q \
        --profile minimal --default-toolchain stable --component clippy,rustfmt \
        --target aarch64-unknown-linux-gnu \
 && chmod -R a+rX /opt/rustup /opt/cargo

ENV CARGO_TARGET_AARCH64_UNKNOWN_LINUX_GNU_LINKER=aarch64-linux-gnu-gcc \
    CC_aarch64_unknown_linux_gnu=aarch64-linux-gnu-gcc \
    PKG_CONFIG_ALLOW_CROSS=1 \
    PKG_CONFIG_LIBDIR_aarch64_unknown_linux_gnu=/usr/lib/aarch64-linux-gnu/pkgconfig:/usr/share/pkgconfig \
    BINDGEN_EXTRA_CLANG_ARGS="--target=aarch64-linux-gnu -I/usr/include/aarch64-linux-gnu"
