#!/usr/bin/env python3
"""Fit duo2-touchd's per-panel calibration from edge-target taps.

    journalctl -u duo2-touchd -o short-unix --since ... > raw.log
    touch-edge-fit.py PANEL TARGETS.log raw.log [WIDTH HEIGHT X_OFFSET]

PANEL is left or right; TARGETS.log comes from touch-edge-targets.py, raw.log
from duo2-touchd's contact log ("touch down ... raw=X,Y ... geom=G xoff=X").
Each tap is paired with the raw touch-down on that panel closest in time
(within 0.25 s). Prints the raw position per target (a raw value that stops
moving between the 90 % and the 98 % targets is the heat processor
saturating there, which no linear correction can undo; on the stock surface
the right panel's raw x runs 8938..17455, the left one's 0..8518), then the
least-squares affine fit in the panel's normalised coordinates, as a
/etc/duo2/touch-calibration line for the geometry the taps were made in
(split: "right", stitched: "stitched-right", one panel: "single-right"), and
the residual per target in mm. The panel's x offset is the one the daemon
logged with each contact (older logs: 8935 for the right panel); defaults
8521 x 12020 per panel (0.01 mm). Fit with the panel in its rotation 0: the
fit is in the panel's own axes.
"""

import re
import sys

panel, tlog, rlog = sys.argv[1], sys.argv[2], sys.argv[3]
W, H, XOFF = (int(v) for v in sys.argv[4:7]) if len(sys.argv) >= 7 else (8521, 12020, 8935)
xoff = XOFF if panel == "right" else 0

taps = [tuple(float(v) for v in line.split()[:3]) for line in open(tlog) if line.strip()]
downs = []
pat = re.compile(
    r"^(\d+\.\d+) .*touch down id=\d+ region=\d+ panel=(\w+) raw=(-?\d+),(-?\d+)"
    r"(?: local=-?\d+,-?\d+)?(?: geom=(\w+) xoff=(-?\d+))?"
)
for line in open(rlog):
    m = pat.match(line)
    if m and m.group(2) == panel:
        geom = m.group(5) or "split"
        off = int(m.group(6)) if m.group(6) is not None else xoff
        downs.append((float(m.group(1)), int(m.group(3)), int(m.group(4)), geom, off))

pairs = []
geoms = set()
for t, fx, fy in taps:
    best = min(downs, key=lambda d: abs(d[0] - t), default=None)
    if best and abs(best[0] - t) < 0.25:
        pairs.append((fx, fy, (best[1] - best[4]) / W, best[2] / H))
        geoms.add(best[3])
print(
    f"{len(pairs)} of {len(taps)} taps paired with a raw contact on the {panel} panel"
    f" (geometry {', '.join(sorted(geoms)) or '-'})"
)
if len(geoms) > 1:
    print("  the taps span geometries: fit each on its own")
key = {"split": "", "stitched": "stitched-", "single": "single-"}.get(
    min(geoms) if geoms else "split", ""
) + panel
for fx, fy, rx, ry in pairs:
    print(f"  target {fx:5.3f} {fy:5.3f}  raw {rx:7.4f} {ry:7.4f}  (raw local x {rx * W:7.0f})")


def lstsq3(rows, ys):
    # normal equations for y = a x + b y + c

    n = 3
    A = [[sum(r[i] * r[j] for r in rows) for j in range(n)] for i in range(n)]
    B = [sum(r[i] * y for r, y in zip(rows, ys)) for i in range(n)]
    for c in range(n):  # Gauss-Jordan
        p = max(range(c, n), key=lambda r: abs(A[r][c]))
        A[c], A[p], B[c], B[p] = A[p], A[c], B[p], B[c]
        for r in range(n):
            if r != c and A[c][c]:
                f = A[r][c] / A[c][c]
                A[r] = [a - f * b for a, b in zip(A[r], A[c])]
                B[r] -= f * B[c]
    return [B[i] / A[i][i] for i in range(n)]


if len(pairs) >= 4:
    rows = [(rx, ry, 1.0) for _, _, rx, ry in pairs]
    ax = lstsq3(rows, [fx for fx, _, _, _ in pairs])
    ay = lstsq3(rows, [fy for _, fy, _, _ in pairs])
    print(f"{key} {ax[0]:.5f} {ax[1]:.5f} {ax[2]:.5f} {ay[0]:.5f} {ay[1]:.5f} {ay[2]:.5f}")
    for fx, fy, rx, ry in pairs:
        ex = (ax[0] * rx + ax[1] * ry + ax[2] - fx) * W / 100
        ey = (ay[0] * rx + ay[1] * ry + ay[2] - fy) * H / 100
        print(f"  target {fx:5.3f} {fy:5.3f}  residual {ex:+5.2f} {ey:+5.2f} mm")
