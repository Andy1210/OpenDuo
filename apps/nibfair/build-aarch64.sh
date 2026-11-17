#!/bin/sh
# Cross-build Nibfair for the phone (aarch64, Debian trixie) in Docker.
#
#   ./build-aarch64.sh            release build -> out/nibfair
#   ./build-aarch64.sh test       the unit tests (the pure rules; host target, same image)
#   ./build-aarch64.sh shots DIR  PNG-ready BMPs of every screen, rendered headless on the
#                                 host (SDL's offscreen driver, software renderer)
#   JOBS=4 ./build-aarch64.sh     fewer parallel jobs (default 10)
#
# The image (Dockerfile) carries the arm64 -dev packages from trixie (SDL
# 3.2.10, wayland), so the binary links against the phone's own library
# versions. The cargo registry and the target dir stay on the host, so a
# rebuild is incremental.
set -eu
here=$(cd "$(dirname "$0")" && pwd)
image=nibfair-cross:trixie
cache=${XDG_CACHE_HOME:-$HOME/.cache}/nibfair-cargo
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
    cp "$here/target/aarch64-unknown-linux-gnu/release/nibfair" "$here/out/nibfair"
    ls -l "$here/out/nibfair"
    ;;
test)
    run cargo test --locked -j "$jobs"
    ;;
shots)
    dir=${2:?usage: $0 shots DIR}
    mkdir -p "$dir"
    dir=$(cd "$dir" && pwd)
    docker run --rm --user "$(id -u):$(id -g)" \
        -e CARGO_HOME=/cargo -e HOME=/tmp -e SDL_VIDEO_DRIVER=offscreen -e SDL_RENDER_DRIVER=software \
        -v "$cache:/cargo" -v "$here:/src" -v "$dir:/shots" -w /src \
        "$image" cargo run --release --locked -j "$jobs" -- --shots /shots
    ;;
lock)
    run cargo generate-lockfile
    ;;
*)
    echo "usage: $0 [build|test|shots DIR|lock]" >&2
    exit 2
    ;;
esac
