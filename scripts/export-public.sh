#!/usr/bin/env bash
# Build the public OpenDuo tree from this (development) repository into a
# fresh directory with a single initial commit, then check it for personal
# data and vendor files. Nothing is pushed: review the tree first.
#
#   scripts/export-public.sh [--ref REF] [--out DIR] [--force] [--message TEXT]
#
#   --ref REF      the commit to export (default HEAD)
#   --out DIR      where (default out/public-preview); must not exist
#   --force        replace DIR if it exists
#   --message TEXT the commit message (default: "OpenDuo <VERSION>: ...")
#
# What goes in: the files git tracks at REF, minus EXCLUDE below. Ignored
# and untracked files (out/, src/, firmware/, dumps/, backups/, local.env,
# local.personal-data, ...) are never in it, and the script fails if one of
# them is tracked after all.
#
# The commit's author and committer: OPENDUO_AUTHOR_NAME and
# OPENDUO_AUTHOR_EMAIL, by default this repository's git user.name and
# user.email. The commit date is now.
#
# Checks (a finding fails the export; the tree stays for inspection):
#   1. no excluded or private path, no file of a firmware / archive / image
#      type (except the reviewed screenshots in docs/images, which must match
#      docs/images/SHA256SUMS);
#   2. scripts/check-personal-data --all in the new tree, with the private
#      patterns: local.personal-data, local.export-deny (more patterns that
#      only the export needs, e.g. names or a network operator: same format,
#      never committed), the phone's address and MAC from local.env, and this
#      machine's host name;
#   3. the same private patterns once more over every line, authorship lines
#      (From:, Signed-off-by:, Copyright) included, after removing the public
#      identity (the author's name and e-mail, the public repository's URL).
#   4. no exported file refers to a document that is not in the export
#      (a docs/ path, a Markdown link, or the name of an excluded document).
#
# The private files are read from the repository root, or, in a git
# worktree without them, from the main worktree's root (OPENDUO_LOCAL_DIR
# overrides; OPENDUO_EXPORT_DENY names another local.export-deny).
# OPENDUO_PUBLIC_URL is the public repository (for check 3).
set -euo pipefail
R=$(cd "$(dirname "$0")/.." && pwd)

# Not public: the development logs (Hungarian), documents tied to the
# developer's own network, and the experiments and research tools. Keep the
# documents in step with CONTRIBUTING.md.
EXCLUDE=(
    docs/BRINGUP.md
    docs/AUDIO.md
    docs/CAMERA.md
    docs/COMPOSITOR.md
    docs/LOCATION.md
    docs/RESEARCH.md
    docs/VIDEO.md
    docs/VOLTE.md
    # Experiments, research and one-off probes (the owner, 2026-11-17): kept
    # here, never exported; no exported file may name them (check 4). Tools
    # that check the hardware stay public.
    video/fetch-rpi-builds.sh
    video/browser-video-measure
    video/web/mse.html
    video/web/codecs.html
    scripts/duo2-video-browser
    compositor/duo-wc/tools/video-measure.sh
    compositor/duo-wc/tools/video/test.html
    compositor/duo-wc/tools/wake-capture.py
    compositor/duo-wc/tools/ctl-swap-repro.sh
    compositor/duo-wc/tools/teardown-repro.py
    rootfs/overlay/usr/local/sbin/duo2-wifi-mac-trial
    rootfs/overlay/usr/local/sbin/duo2-sleeptest
    devmode/devtools-experiments.list
    scripts/install-factory-addr.sh
    telephony/volte-trial-install.sh
    touch/tools/pen-ab-check.py
    touch/tools/pen-bt-trace.py
    touch/tools/pen-drop-capture.sh
    touch/tools/pen-hover-timing.py
    touch/tools/pen-keepalive-check.py
    touch/tests/test_pen_hover_timing.py
    scripts/probe-android.sh
    scripts/duo2-camera-probe
    scripts/duo2-gpu-wake-test
)
# Never public, whatever git says (they are ignored; tracked would be a mistake).
PRIVATE_RE='^(out|src|firmware|dumps|backups)/|^local\.env$|^local\.env\.(?!example$)|^local\.personal-data$|^local\.export-deny$'
# File types that are firmware, vendor binaries, images or archives.
VENDOR_RE='\.(mbn|mdt|b[0-9][0-9]|elf|so(\.[0-9]+)*|ko|apk|apex|img|simg|bin|tlv|fw|cfu|dtb|dtbo|zip|jar|tar|tgz|gz|xz|zst|bz2|7z|deb|png|jpe?g|gif|webp|bmp|mp4|webm)$'

REF=HEAD
OUT=$R/out/public-preview
FORCE=0
MESSAGE=
while [ $# -gt 0 ]; do
    case $1 in
        --ref) REF=$2; shift 2 ;;
        --out) OUT=$2; shift 2 ;;
        --force) FORCE=1; shift ;;
        --message) MESSAGE=$2; shift 2 ;;
        -h | --help) sed -n '2,/^set -euo/p' "$0" | sed '$d; s/^# \{0,1\}//'; exit 0 ;;
        *) echo "export-public: unknown argument $1" >&2; exit 2 ;;
    esac
done
case $OUT in /*) ;; *) OUT=$PWD/$OUT ;; esac

g() { git -C "$R" "$@"; }
COMMIT=$(g rev-parse --verify "$REF^{commit}")
VERSION=$(g show "$COMMIT:VERSION" | tr -d ' \t\r\n')
NAME=${OPENDUO_AUTHOR_NAME:-$(g config user.name || true)}
EMAIL=${OPENDUO_AUTHOR_EMAIL:-$(g config user.email || true)}
PUBLIC_URL=${OPENDUO_PUBLIC_URL:-https://github.com/$NAME/OpenDuo}
[ -n "$NAME" ] && [ -n "$EMAIL" ] || { echo "export-public: no author (set OPENDUO_AUTHOR_NAME / _EMAIL)" >&2; exit 2; }

LOCAL_DIR=${OPENDUO_LOCAL_DIR:-}
if [ -z "$LOCAL_DIR" ]; then
    LOCAL_DIR=$R
    if [ ! -f "$R/local.personal-data" ]; then
        common=$(g rev-parse --path-format=absolute --git-common-dir)
        [ "$(basename "$common")" = .git ] && LOCAL_DIR=$(dirname "$common")
    fi
fi

if [ -e "$OUT" ]; then
    [ "$FORCE" = 1 ] || { echo "export-public: $OUT exists (--force replaces it)" >&2; exit 2; }
    rm -rf "$OUT"
fi
mkdir -p "$OUT"
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

echo "== export $(g rev-parse --short "$COMMIT") ($REF), OpenDuo $VERSION -> $OUT"
g archive --format=tar "$COMMIT" | tar -x -C "$OUT"
removed=()
for p in "${EXCLUDE[@]}"; do
    if [ -e "$OUT/$p" ]; then
        rm -rf "${OUT:?}/$p"
        removed+=("$p")
    fi
done
chmod -R go-w "$OUT"

echo "== commit"
[ -n "$MESSAGE" ] || MESSAGE="OpenDuo $VERSION: the source tree

The first public import of OpenDuo, a Linux system for the Surface Duo 2.
OpenDuo is developed with an AI coding assistant (Claude, in Claude Code).

Assisted-by: Claude:claude-opus-5-5"
git init -q -b main "$OUT"
git -C "$OUT" add -A
GIT_AUTHOR_NAME=$NAME GIT_AUTHOR_EMAIL=$EMAIL GIT_COMMITTER_NAME=$NAME GIT_COMMITTER_EMAIL=$EMAIL \
    git -C "$OUT" -c commit.gpgsign=false -c core.hooksPath=/dev/null commit -q -m "$MESSAGE"
git -C "$OUT" ls-files >"$WORK/files"

# The private patterns, one Python regular expression per line ("!" = allow).
{
    for f in "$LOCAL_DIR/local.personal-data" "${OPENDUO_EXPORT_DENY:-$LOCAL_DIR/local.export-deny}"; do
        if [ -f "$f" ]; then cat "$f"; echo; else echo "export-public: no $f" >&2; fi
    done
    python3 -I - "$LOCAL_DIR/local.env" "$(hostname -s 2>/dev/null || true)" <<'EOF'
import os
import re
import sys

env, host = sys.argv[1], sys.argv[2]
if os.path.exists(env):
    for raw in open(env, encoding="utf-8"):
        m = re.match(r"\s*(DUO2_HOST|DUO2_MAC)=[\"']?([^\"'\s#]+)", raw)
        if not m:
            continue
        value = m.group(2).rsplit("@", 1)[-1]
        if not value or value.startswith("$") or value in ("192.0.2.50", "00:11:22:33:44:55"):
            continue
        if m.group(1) == "DUO2_MAC":
            print("(?i)" + re.escape(value))
        else:
            print(r"(?<![0-9A-Za-z.])" + re.escape(value) + r"(?![0-9A-Za-z])")
if host and host not in ("localhost", "debian", "ubuntu", "runner"):
    print(r"(?i)\b" + re.escape(host) + r"\b")
EOF
} >"$WORK/patterns"
# The public identity is allowed where check-personal-data looks.
{
    cat "$WORK/patterns"
    python3 -I -c 'import re, sys; print("!(?i)" + re.escape(sys.argv[1].split("://", 1)[-1]))' "$PUBLIC_URL"
} >"$WORK/patterns-check"

fail=0
echo "== 1. paths and file types"
if grep -P "$PRIVATE_RE" "$WORK/files"; then
    echo "   FAIL: private paths are tracked (above)"; fail=1
fi
for p in "${EXCLUDE[@]}"; do
    if grep -qx -- "$p" "$WORK/files"; then echo "   FAIL: excluded $p is in the tree"; fail=1; fi
done
grep -iP "$VENDOR_RE" "$WORK/files" | grep -v '^docs/images/[^/]*\.png$' >"$WORK/vendor" || true
if [ -s "$WORK/vendor" ]; then
    sed 's/^/   FAIL: file type: /' "$WORK/vendor"; fail=1
fi
images_ok=0
if [ -f "$OUT/docs/images/SHA256SUMS" ]; then
    if (cd "$OUT/docs/images" && sha256sum --quiet -c SHA256SUMS); then
        images_ok=1
        while IFS= read -r f; do
            [ "$f" = SHA256SUMS ] && continue
            grep -qF "  $f" "$OUT/docs/images/SHA256SUMS" || { echo "   FAIL: not reviewed: docs/images/$f"; fail=1; images_ok=0; }
        done < <(sed -n 's|^docs/images/||p' "$WORK/files")
    else
        echo "   FAIL: docs/images does not match its SHA256SUMS"; fail=1
    fi
fi
[ "$fail" = 0 ] && echo "   ok"

echo "== 2. scripts/check-personal-data --all (with the private patterns)"
set +e
DUO2_PERSONAL_DATA=$WORK/patterns-check python3 -I "$OUT/scripts/check-personal-data" --all >"$WORK/check" 2>"$WORK/check.err"
rc=$?
set -e
[ "$rc" -le 1 ] || { cat "$WORK/check.err" >&2; echo "   FAIL: check-personal-data could not run"; fail=1; }
# The reviewed screenshots are binary files by design.
if [ "$images_ok" = 1 ]; then
    grep -vE '^docs/images/[^/:]+\.png: blob: ' "$WORK/check" >"$WORK/check.left" || true
else
    cp "$WORK/check" "$WORK/check.left"
fi
if [ -s "$WORK/check.left" ]; then
    sed 's/^/   /' "$WORK/check.left"
    echo "   FAIL: $(wc -l <"$WORK/check.left") finding(s)"; fail=1
else
    echo "   ok"
fi

echo "== 3. private patterns on every line, authorship included"
set +e
python3 -I - "$OUT" "$WORK/files" "$WORK/patterns" "$NAME" "$EMAIL" "$PUBLIC_URL" <<'EOF'
import os
import re
import sys

out, files, patterns, name, email, url = sys.argv[1:7]
deny = []
for raw in open(patterns, encoding="utf-8"):
    line = raw.rstrip("\n")
    if line.strip() and not line.lstrip().startswith("#") and not line.startswith("!"):
        deny.append(re.compile(line))
public = [
    re.compile(re.escape(name) + r"\s*<" + re.escape(email) + ">", re.I),
    re.compile(re.escape(url.split("://", 1)[-1]), re.I),
    re.compile(r"Copyright \(c\) \d{4} " + re.escape(name) + r"\b", re.I),
]


def mask(text):
    text = text.strip()
    return text[:1] + "…" if len(text) <= 6 else text[:2] + "…" + text[-2:]


found = 0
for path in open(files, encoding="utf-8").read().split("\n"):
    full = os.path.join(out, path)
    if not path or not os.path.isfile(full) or os.path.islink(full):
        continue
    data = open(full, "rb").read()
    if b"\0" in data[:8192]:
        continue
    for n, text in enumerate(data.decode("utf-8", "replace").splitlines(), 1):
        for rx in public:
            text = rx.sub("", text)
        for rx in deny:
            for m in rx.finditer(text):
                print("   %s:%d: %s" % (path, n, mask(m.group(0))))
                found += 1
sys.exit(1 if found else 0)
EOF
rc=$?
set -e
if [ "$rc" = 0 ]; then echo "   ok"; else echo "   FAIL: private patterns found (above)"; fail=1; fi

echo "== 4. links to documents that are not exported"
set +e
python3 -I - "$OUT" "$WORK/files" "${EXCLUDE[@]}" <<'EOF'
import os
import re
import sys

out, files = sys.argv[1], sys.argv[2]
excluded = sys.argv[3:]
exported = set(open(files, encoding="utf-8").read().split("\n")) - {""}
names = {os.path.basename(p) for p in excluded}
doc_ref = re.compile(r"docs/[A-Za-z0-9_./-]+\.md")
md_link = re.compile(r"\]\(([^)#\s]+\.md)(?:#[^)]*)?\)")
self_files = {"scripts/export-public.sh", "scripts/tests/test_check_personal_data.py"}
found = 0
for path in sorted(exported):
    if path in self_files:
        continue
    full = os.path.join(out, path)
    if os.path.islink(full) or not os.path.isfile(full):
        continue
    data = open(full, "rb").read()
    if b"\0" in data[:8192]:
        continue
    for n, text in enumerate(data.decode("utf-8", "replace").splitlines(), 1):
        for m in doc_ref.finditer(text):
            if m.group(0) not in exported:
                print("   %s:%d: %s is not in the export" % (path, n, m.group(0)))
                found += 1
        for m in md_link.finditer(text):
            if m.group(1).startswith(("http:", "https:")):
                continue
            target = os.path.normpath(os.path.join(os.path.dirname(path), m.group(1)))
            if target not in exported:
                print("   %s:%d: the link to %s is not in the export" % (path, n, target))
                found += 1
        for name in names:
            if re.search(r"(?<![A-Za-z0-9_-])" + re.escape(name) + r"\b", text):
                print("   %s:%d: mentions the excluded %s" % (path, n, name))
                found += 1
sys.exit(1 if found else 0)
EOF
rc=$?
set -e
if [ "$rc" = 0 ]; then echo "   ok"; else echo "   FAIL: dangling document links (above)"; fail=1; fi

echo "== summary"
echo "   tracked at $REF: $(g ls-tree -r --name-only "$COMMIT" | wc -l) files; exported: $(wc -l <"$WORK/files") files, $(du -sh --exclude=.git "$OUT" | cut -f1)"
echo "   excluded: ${removed[*]:-(none)}"
echo "   commit: $(git -C "$OUT" log -1 --format='%h %an <%ae> %s')"
echo "   private patterns from: $LOCAL_DIR ($(grep -cv '^\s*\(#\|$\)' "$WORK/patterns") patterns)"
if [ "$fail" = 0 ]; then
    echo "   result: CLEAN. Nothing was pushed; review $OUT first."
else
    echo "   result: FINDINGS (above). Nothing was pushed; $OUT is kept for inspection."
fi
exit "$fail"
