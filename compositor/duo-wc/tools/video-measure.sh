#!/bin/sh
# Measure a fullscreen video in Chromium on the phone (run there as the
# administrator, duo2admin: duoctl reaches duo-wc's admin socket, Chromium
# starts as the GUI user in the session; with duo-wc running):
#
#   tools/video-measure.sh FILE [SECONDS] [span|DSI-1|DSI-2] [extra chromium flags...]
#
# FILE is a video in /tmp/vt next to test.html (tools/video/test.html);
# VT_QUERY=raf=0 leaves out the page's own animation-frame loop. A
# throwaway Chromium profile (/tmp/vt/profile) is started fullscreen with the
# page, placed as asked, and after a 6 s settle the script samples for
# SECONDS (default 15): each process's CPU (from /proc, % of one core),
# duo-wc's page flips per panel per second, and the page's own report
# (frames shown / decoded / dropped per second, its rAF rate), then stops
# that Chromium. Only the Chromium it started is stopped (by PID).
set -eu
file=$1; secs=${2:-15}; where=${3:-DSI-1}
shift; [ $# -gt 0 ] && shift; [ $# -gt 0 ] && shift
gui=$(systemctl show -p User --value duo-wc)
dir=/tmp/vt
[ -f "$dir/test.html" ] || { echo "no $dir/test.html"; exit 2; }
duoctl set_power all on >/dev/null
case "$where" in span) duoctl expect_window DSI-1 >/dev/null ;; *) duoctl expect_window "$where" >/dev/null ;; esac
# shellcheck disable=SC2024 # the redirect is meant for the calling user
sudo -u "$gui" sh -c '. /run/duo-wc/session.env; exec "$@"' sh chromium --user-data-dir="$dir/profile" --no-first-run --start-fullscreen \
    --autoplay-policy=no-user-gesture-required "$@" \
    "file://$dir/test.html?v=$file${VT_QUERY:+&$VT_QUERY}" >"$dir/chromium.log" 2>&1 &
pid=$!
sleep 6
win=$(duoctl get_state | python3 -c 'import json,sys
for w in json.load(sys.stdin)["ok"]["windows"]:
    if (w.get("title") or "").startswith("vt "): print(w["id"])' | tail -1)
[ "$where" = span ] && [ -n "$win" ] && duoctl place_window span "$win" >/dev/null && sleep 2
python3 - "$secs" "$pid" <<'PY'
import json, os, socket, sys, time
secs, root = float(sys.argv[1]), int(sys.argv[2])
hz = os.sysconf("SC_CLK_TCK")
def wc(req):
    s = socket.socket(socket.AF_UNIX); s.connect(os.environ.get("DUO_WC_ADMIN_SOCKET", "/run/duo-wc/admin.sock"))
    s.sendall((json.dumps({"id": 1, "request": req}) + "\n").encode()); b = b""
    while not b.endswith(b"\n"): b += s.recv(65536)
    s.close(); return json.loads(b)["ok"]
def procs():
    out = {}
    for p in os.listdir("/proc"):
        if not p.isdigit(): continue
        try:
            st = open("/proc/%s/stat" % p).read(); cmd = open("/proc/%s/cmdline" % p).read().split("\0")
        except OSError: continue
        name = st[st.index("(") + 1:st.rindex(")")]; f = st[st.rindex(")") + 2:].split()
        if name in ("duo-wc", "squeekboard") or "chromium" in cmd[0] or "duo-shell" in " ".join(cmd[:2]):
            # Chromium's children rewrite their command line into one string.
            whole = " ".join(cmd)
            typ = whole.split("--type=", 1)[1].split()[0] if "--type=" in whole else "browser"
            label = name if "chromium" not in cmd[0] else "chromium/" + typ
            out[p] = (label, int(f[11]) + int(f[12]))
    return out
def flips():
    return {o["name"]: o["frames_presented"] for o in wc("get_outputs")["outputs"]}
GPU = "/sys/class/devfreq/3d00000.gpu/trans_stat"
def gpu():
    """ms spent at each GPU frequency (MHz), from devfreq."""
    try:
        lines = open(GPU).read().splitlines()
    except OSError:
        return {}
    out = {}
    for line in lines[2:]:
        if ":" not in line: continue
        head, rest = line.split(":", 1)
        f = head.replace("*", "").strip()
        if not f.isdigit(): continue
        out[int(f) // 1000000] = int(rest.split()[-1])
    return out
p0, f0, g0, t0 = procs(), flips(), gpu(), time.time()
titles = []
for _ in range(int(secs)):
    time.sleep(1)
    for w in wc("get_state")["windows"]:
        if (w.get("title") or "").startswith("vt "): titles.append(w["title"])
p1, f1, g1, t1 = procs(), flips(), gpu(), time.time()
dt = t1 - t0
agg = {}
for pid, (label, ticks) in p1.items():
    if pid in p0:
        agg[label] = agg.get(label, 0.0) + (ticks - p0[pid][1]) / hz / dt * 100
print("cpu %% of one core over %.1f s:" % dt)
for label, pct in sorted(agg.items(), key=lambda kv: -kv[1]):
    print("  %-28s %6.1f" % (label, pct))
print("page flips per second: " + ", ".join("%s %.1f" % (o, (f1[o] - f0[o]) / dt) for o in f1))
if g1:
    spent = {f: g1[f] - g0.get(f, 0) for f in g1}
    total = sum(spent.values()) or 1
    print("gpu MHz residency: " + ", ".join("%d %d%%" % (f, 100 * ms / total) for f, ms in sorted(spent.items()) if ms))
for t in titles[-4:]:
    print("page: " + t)
la = open("/proc/loadavg").read().split()[:3]
print("load", " ".join(la))
PY
# /usr/bin/chromium is a wrapper script: stop the browser process it started
# (the one with this profile and no --type), by PID.
for p in $(pgrep -f -- "--user-data-dir=$dir/profile"); do
    [ -r /proc/"$p"/cmdline ] || continue
    tr '\0' ' ' </proc/"$p"/cmdline 2>/dev/null | grep -q -- "--type=" || sudo kill "$p" 2>/dev/null || true
done
wait "$pid" 2>/dev/null || true
