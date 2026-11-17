#!/bin/sh
# Fetch Linux at a tag (shallow) into DIR, from kernel.org or, when it does
# not answer, from the GitHub mirror of the stable tree. Both carry the same
# tag commit, so kernel/patches applied on top give the same commits (and the
# same release string) whichever answered. With --scripts, fetch only the
# files checkpatch needs into DIR.
#   scripts/fetch-linux.sh TAG DIR
#   scripts/fetch-linux.sh --scripts TAG DIR
set -eu
KORG=https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git
MIRROR=https://github.com/gregkh/linux.git

if [ "${1:-}" = --scripts ]; then
    tag=$2 dir=$3
    mkdir -p "$dir"
    for f in checkpatch.pl spelling.txt const_structs.checkpatch; do
        curl -sSfL --retry 3 --connect-timeout 20 -o "$dir/$f" \
            "$KORG/plain/scripts/$f?h=$tag" ||
            curl -sSfL --retry 3 --connect-timeout 20 -o "$dir/$f" \
                "https://raw.githubusercontent.com/gregkh/linux/$tag/scripts/$f"
    done
    exit 0
fi

tag=$1 dir=$2
for url in "$KORG" "$MIRROR"; do
    for try in 1 2; do
        rm -rf "$dir"
        if timeout 900 git clone -q --depth 1 --branch "$tag" "$url" "$dir"; then
            echo "Linux $tag from $url"
            exit 0
        fi
        echo "fetch-linux: $url failed (try $try)" >&2
        sleep 10
    done
done
echo "fetch-linux: no source answered for $tag" >&2
exit 1
