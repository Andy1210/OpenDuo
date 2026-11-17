# Cross-build image for the camera stack's Debian packages (camera/build-debs.sh):
# x86_64 host, arm64 Debian trixie target. The build dependencies of the two
# source packages, as camera/build-debs.sh prepares them (its debian/control
# files are copied into the build context), are installed for arm64.
#
#   libcamera (Debian sid's packaging of the upstream release + our patches),
#     profiles: cross, nocheck, nodoc, pkg.libcamera.noqt (no qcam),
#     pkg.libcamera.nopython (no Python bindings); plus EGL and GLES for the
#     software ISP's GPU path (qt6-base-dev pulls them in otherwise)
#   pipewire (trixie's own source, rebuilt unchanged against that libcamera)
FROM debian:trixie

ARG DEBIAN_FRONTEND=noninteractive
RUN dpkg --add-architecture arm64 \
 && apt-get update -qq \
 && apt-get install -y -qq --no-install-recommends \
        build-essential crossbuild-essential-arm64 dpkg-dev devscripts fakeroot \
        quilt ca-certificates \
 && rm -rf /var/lib/apt/lists/*

COPY libcamera/ /tmp/deps/libcamera/
RUN apt-get update -qq \
 && apt-get build-dep -y -qq -a arm64 \
        -P cross,nocheck,nodoc,pkg.libcamera.noqt,pkg.libcamera.nopython /tmp/deps/libcamera \
 && apt-get install -y -qq --no-install-recommends libegl-dev:arm64 libgles-dev:arm64 \
 && rm -rf /var/lib/apt/lists/*

COPY pipewire/ /tmp/deps/pipewire/
RUN apt-get update -qq \
 && apt-get build-dep -y -qq -a arm64 -P cross,nocheck,nodoc /tmp/deps/pipewire \
 && rm -rf /var/lib/apt/lists/*
