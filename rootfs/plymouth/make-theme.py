#!/usr/bin/python3 -I
"""Draw the duo2-os boot splash's pictures (Plymouth, the duo2 theme) into DIR:

    make-theme.py DIR

  logo.png   the phone itself, open like a book: the Surface Duo 2's two
             halves (its real proportions: two dark glass displays in a dark
             body, wide top and bottom bezels, round outer corners, the hinge
             between them), turned a little toward the viewer, with a soft
             white rim; the device's shape only (no logo, no wordmark), made
             here from numbers (the owner's pick of six drafts, 2026-11-09)
  dot.png    one dot of the progress row under it

Standard library only (zlib, struct): anti-aliased shapes from their signed
distance, each half a plane seen in perspective (a homography from the half's
own millimetres to the picture's pixels), written as RGBA PNG. Run at install
time (telephony-style install-files.sh, the image recipe), so no picture is
kept in git.
"""

import math
import os
import struct
import sys
import zlib

INK = (230, 233, 238)  # soft white (on black: no glare at night)
ACCENT = (53, 132, 228)  # the shell's one accent (Adwaita blue)

# The Surface Duo 2 (mm). One half 92.25 x 145.2 (open: 184.5 wide); its
# display 1344 x 1892 px at 5.8", 85.3 x 120.1, so the bezels are wide at
# the top and the bottom, narrow at the sides (narrowest at the hinge, where
# the glass curves round). The outer corners are round, the hinge side's
# nearly square.
HALF_W, HALF_H = 92.25, 145.2
DISP_W, DISP_H = 85.3, 120.1
BEZEL_HINGE = 2.55
R_OUTER, R_HINGE = 7.5, 1.6  # the body's corners
DR_OUTER, DR_HINGE = 3.5, 0.8  # the display's
SPINE = 3.2  # the hinge, seen between the halves

BODY = (40, 42, 47)  # dark grey body
SPINE_COL = (22, 23, 26)
SPINE_LINE = (70, 74, 80)
GLASS = (9, 11, 15)  # the displays, off
DISP_EDGE = (90, 96, 106)

# The view: each half turned this far toward the viewer about the hinge (the
# phone open at 180 - 2 x 24 = 132 degrees), a camera this far away (mm), the
# picture's size and the phone's height in it.
TURN = math.radians(24)
CAMERA = 420.0
WIDTH, HEIGHT = 340, 250
PHONE_PX = 210.0


def png(path, width, height, pixels):
    """pixels: bytes, RGBA, row by row."""
    raw = b"".join(b"\0" + pixels[y * width * 4 : (y + 1) * width * 4] for y in range(height))

    def chunk(kind, data):
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        f.write(chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)))
        f.write(chunk(b"IDAT", zlib.compress(raw, 9)))
        f.write(chunk(b"IEND", b""))


def coverage(d):
    """Pixel coverage of an edge at signed distance d (1 px soft edge)."""
    return max(0.0, min(1.0, 0.5 - d))


def round_box(cx, cy, hw, hh, r_left, r_right):
    """Signed distance (negative inside) to a box (centre, half sizes) whose
    left corners have radius r_left and right corners r_right."""

    def sdf(u, v):
        px, py = u - cx, v - cy
        r = r_right if px > 0 else r_left
        qx = abs(px) - hw + r
        qy = abs(py) - hh + r
        return min(max(qx, qy), 0.0) + math.hypot(max(qx, 0.0), max(qy, 0.0)) - r

    return sdf


# -- planes in perspective ---------------------------------------------------


def matmul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


def inverse(m):
    """The inverse of a 3 x 3 matrix, and its determinant."""
    a, b, c = m[0]
    d, e, f = m[1]
    g, h, i = m[2]
    det = a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)
    inv = [
        [e * i - f * h, c * h - b * i, b * f - c * e],
        [f * g - d * i, a * i - c * g, c * d - a * f],
        [d * h - e * g, b * g - a * h, a * e - b * d],
    ]
    return [[x / det for x in row] for row in inv], det


def project(m, u, v):
    w = m[2][0] * u + m[2][1] * v + m[2][2]
    return ((m[0][0] * u + m[0][1] * v + m[0][2]) / w, (m[1][0] * u + m[1][1] * v + m[1][2]) / w)


class Picture:
    """An RGBA picture drawn in layers (premultiplied, painter's order)."""

    def __init__(self, w, h):
        self.w, self.h = w, h
        self.px = [[0.0, 0.0, 0.0, 0.0] for _ in range(w * h)]

    def layer(self, plane, sdf, colour, box, alpha=1.0, stroke=None):
        """Draw a shape given in a plane's own coordinates: `plane` maps them
        to pixels (a homography), `sdf` is its signed distance there,
        `colour(u, v)` its colour, `box` (u0, u1, v0, v1) its extent; filled,
        or an outline `stroke` px wide."""
        inv, det = inverse(plane)
        corners = [project(plane, u, v) for u in box[:2] for v in box[2:]]
        x0 = max(0, int(min(p[0] for p in corners)) - 3)
        x1 = min(self.w, int(max(p[0] for p in corners)) + 4)
        y0 = max(0, int(min(p[1] for p in corners)) - 3)
        y1 = min(self.h, int(max(p[1] for p in corners)) + 4)
        for y in range(y0, y1):
            for x in range(x0, x1):
                sx, sy = x + 0.5, y + 0.5
                w = inv[2][0] * sx + inv[2][1] * sy + inv[2][2]
                u = (inv[0][0] * sx + inv[0][1] * sy + inv[0][2]) / w
                v = (inv[1][0] * sx + inv[1][1] * sy + inv[1][2]) / w
                # pixels per plane unit here: the homography's local scale
                wf = plane[2][0] * u + plane[2][1] * v + plane[2][2]
                d = sdf(u, v) * math.sqrt(abs(det) / abs(wf) ** 3)
                a = coverage(d) if stroke is None else coverage(abs(d) - stroke / 2)
                a *= alpha
                if a <= 0.0:
                    continue
                r, g, b = colour(u, v)
                p = self.px[y * self.w + x]
                k = 1.0 - a
                p[0] = r * a + p[0] * k
                p[1] = g * a + p[1] * k
                p[2] = b * a + p[2] * k
                p[3] = a + p[3] * k

    def save(self, path):
        out = bytearray(self.w * self.h * 4)
        for i, (r, g, b, a) in enumerate(self.px):
            if a > 0:
                out[i * 4 : i * 4 + 4] = bytes(
                    (
                        min(255, round(r / a)),
                        min(255, round(g / a)),
                        min(255, round(b / a)),
                        round(a * 255),
                    )
                )
        png(path, self.w, self.h, bytes(out))


def flat(c):
    return lambda u, v: c


def glass(side):
    """The dark glass of one half's display, with one faint diagonal
    reflection running across both (u: mm from the hinge outwards)."""

    def colour(u, v):
        t = (side * (u + SPINE / 2) + 20.0) * 0.55 - v * 0.83
        k = 0.10 * math.exp(-(((t - 18.0) / 22.0) ** 2)) + 0.035 * math.exp(
            -(((t + 30.0) / 9.0) ** 2)
        )
        return tuple(GLASS[i] + (255 - GLASS[i]) * k for i in range(3))

    return colour


def half_plane(side, f, cx, cy):
    """One half (side 1: the right, -1: the left) turned TURN about the
    hinge, its outer edge toward the camera: its own (u mm from the hinge
    outwards, v mm from the middle down) to pixels. In 3D x = side * (SPINE/2
    + u cos), z = u sin (toward the camera), y = v; seen as f*x/(CAMERA - z)."""
    c, s = math.cos(TURN), math.sin(TURN)
    return [
        [side * f * c + cx * -s, 0, side * f * SPINE / 2 + cx * CAMERA],
        [cy * -s, f, cy * CAMERA],
        [-s, 0, CAMERA],
    ]


def logo(path):
    pic = Picture(WIDTH, HEIGHT)
    f = PHONE_PX / HALF_H * CAMERA * 0.93
    cx, cy = WIDTH / 2, HEIGHT / 2
    box = (0.0, HALF_W, -HALF_H / 2, HALF_H / 2)
    body = round_box(HALF_W / 2, 0.0, HALF_W / 2, HALF_H / 2, R_HINGE, R_OUTER)
    disp = round_box(BEZEL_HINGE + DISP_W / 2, 0.0, DISP_W / 2, DISP_H / 2, DR_HINGE, DR_OUTER)
    # the hinge between the halves, at the far end of the fold (z = 0)
    spine = [[f / CAMERA, 0, cx], [0, f / CAMERA, cy], [0, 0, 1]]
    spine_box = (-SPINE, SPINE, -HALF_H / 2, HALF_H / 2)
    pic.layer(
        spine,
        round_box(0, 0, SPINE / 2 + 0.6, HALF_H / 2 - 2.0, 0.8, 0.8),
        flat(SPINE_COL),
        spine_box,
    )
    pic.layer(
        spine,
        round_box(0, 0, 0.35, HALF_H / 2 - 3.0, 0.3, 0.3),
        flat(SPINE_LINE),
        spine_box,
        alpha=0.8,
    )
    for side in (-1, 1):
        plane = half_plane(side, f, cx, cy)
        pic.layer(plane, body, flat(BODY), box)
        pic.layer(plane, body, flat(INK), box, alpha=0.45, stroke=1.3)  # the rim
        pic.layer(plane, disp, glass(side), box)
        pic.layer(plane, disp, flat(DISP_EDGE), box, alpha=0.35, stroke=0.9)  # the display's edge
    pic.save(path)


def dot(path, size=16):
    px = bytearray(size * size * 4)
    c = size / 2
    for y in range(size):
        for x in range(size):
            d = math.hypot(x + 0.5 - c, y + 0.5 - c) - (size / 2 - 1.5)
            i = (y * size + x) * 4
            px[i : i + 4] = bytes((INK[0], INK[1], INK[2], int(round(coverage(d) * 255))))
    png(path, size, size, bytes(px))


def main(argv):
    if len(argv) != 1:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    os.makedirs(argv[0], exist_ok=True)
    logo(os.path.join(argv[0], "logo.png"))
    dot(os.path.join(argv[0], "dot.png"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
