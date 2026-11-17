#!/bin/sh
# Cross-build duo-wc for the phone (aarch64, Debian trixie) in Docker.
#
#   ./build-aarch64.sh            release build -> out/duo-wc
#   ./build-aarch64.sh test       run the unit tests (host target, same image)
#   ./build-aarch64.sh check      rustfmt --check and clippy (no warnings)
#   JOBS=4 ./build-aarch64.sh     fewer parallel jobs (default 10)
#
# The image (Dockerfile) carries the arm64 -dev packages from trixie, so the
# binary links against the phone's own library versions. The cargo registry
# and the target dir are kept on the host, so a rebuild is incremental.
set -eu
here=$(cd "$(dirname "$0")" && pwd)
image=duo-wc-cross:trixie
cache=${XDG_CACHE_HOME:-$HOME/.cache}/duo-wc-cargo
jobs=${JOBS:-10}
mkdir -p "$cache" "$here/out"

if ! docker image inspect "$image" >/dev/null 2>&1 || [ "${REBUILD_IMAGE:-}" = 1 ]; then
    docker build -t "$image" "$here"
fi

run() {
    docker run --rm --user "$(id -u):$(id -g)" \
        -e CARGO_HOME=/cargo -e HOME=/tmp \
        -v "$cache:/cargo" -v "$here:/src" -w /src \
        "$image" "$@"
}

case "${1:-build}" in
build)
    run cargo build --release --locked -j "$jobs" --target aarch64-unknown-linux-gnu
    cp "$here/target/aarch64-unknown-linux-gnu/release/duo-wc" "$here/out/duo-wc"
    ls -l "$here/out/duo-wc"
    ;;
test)
    # Natively on the host (the image carries the amd64 -dev set too): the
    # tests cover the pure parts (layout, config, cli), no emulator needed.
    run cargo test --locked -j "$jobs"
    ;;
check)
    # What every commit keeps clean. An image built before clippy and
    # rustfmt were in the Dockerfile needs REBUILD_IMAGE=1 once.
    run cargo fmt --check
    run cargo clippy --locked -j "$jobs" --all-targets -- -D warnings
    ;;
lock)
    run cargo generate-lockfile
    ;;
*)
    echo "usage: $0 [build|test|check|lock]" >&2
    exit 2
    ;;
esac
