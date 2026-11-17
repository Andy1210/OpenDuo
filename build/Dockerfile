# Build environment for the Duo 2 kernel, DTBs and firmware tools.
FROM ubuntu:24.04
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        build-essential gcc-aarch64-linux-gnu binutils-aarch64-linux-gnu bison flex bc \
        libssl-dev libelf-dev device-tree-compiler python3 python3-dev python3-pip python3-venv swig cpio kmod \
        git ca-certificates xz-utils zstd lz4 rsync file \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /work
