"""Touch calibration from compositor/duo-wc/tools/touch-calib.py's log. Pure
(tests/test_touchcal.py).

touch-calib.py shows five crosshairs on one panel, two taps each, and logs
`target_x target_y tap_x tap_y width height` (logical px). The taps were
already mapped by the panel's current libinput calibration matrix M (a 2x3
affine map on normalised coordinates, udev's LIBINPUT_CALIBRATION_MATRIX), so
the raw touch is M^-1(tap); the new matrix is a least-squares affine fit from
raw to target, row by row. A row is changed only when the fit really helps
(its error drops by a quarter and by at least 2 px): with ten taps a fitted
row otherwise follows the finger's own scatter (as found when the panels were
first calibrated by hand, 2026-11-07).
"""

IDENTITY = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0)
MIN_GAIN = 0.25
MIN_GAIN_PX = 2.0


def parse_log(text):
    rows = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) != 6:
            continue
        try:
            tx, ty, px, py, w, h = (float(p) for p in parts)
        except ValueError:
            continue
        if w > 0 and h > 0:
            rows.append((tx, ty, px, py, w, h))
    return rows


def parse_matrix(text):
    try:
        vals = tuple(float(v) for v in (text or "").split())
    except ValueError:
        return IDENTITY
    return vals[:6] if len(vals) >= 6 else IDENTITY


def format_matrix(m):
    return " ".join(_fmt(v) for v in m)


def _fmt(v):
    if v == int(v):
        return str(int(v))
    return ("%.5f" % v).rstrip("0")


def invert(m):
    a, b, c, d, e, f = m
    det = a * e - b * d
    if abs(det) < 1e-9:
        raise ValueError("singular matrix")
    ia, ib = e / det, -b / det
    id_, ie = -d / det, a / det
    ic = -(ia * c + ib * f)
    if_ = -(id_ * c + ie * f)
    return (ia, ib, ic, id_, ie, if_)


def apply(m, x, y):
    a, b, c, d, e, f = m
    return a * x + b * y + c, d * x + e * y + f


def _solve3(A, v):
    """Solve a 3x3 system (Gaussian elimination with pivoting)."""
    M = [row[:] + [v[i]] for i, row in enumerate(A)]
    for col in range(3):
        piv = max(range(col, 3), key=lambda r: abs(M[r][col]))
        if abs(M[piv][col]) < 1e-12:
            raise ValueError("degenerate taps")
        M[col], M[piv] = M[piv], M[col]
        for r in range(3):
            if r != col:
                k = M[r][col] / M[col][col]
                M[r] = [M[r][i] - k * M[col][i] for i in range(4)]
    return [M[i][3] / M[i][i] for i in range(3)]


def _fit_row(raw, goal):
    """Least squares goal ~ p*x + q*y + r over raw points."""
    sxx = sum(x * x for x, _ in raw)
    sxy = sum(x * y for x, y in raw)
    syy = sum(y * y for _, y in raw)
    sx = sum(x for x, _ in raw)
    sy = sum(y for _, y in raw)
    n = float(len(raw))
    A = [[sxx, sxy, sx], [sxy, syy, sy], [sx, sy, n]]
    v = [
        sum(x * g for (x, _), g in zip(raw, goal)),
        sum(y * g for (_, y), g in zip(raw, goal)),
        sum(goal),
    ]
    return _solve3(A, v)


def _rms(values):
    return (sum(v * v for v in values) / len(values)) ** 0.5 if values else 0.0


def fit(rows, current):
    """-> (new matrix, {'x': (rms_before, rms_after, changed), 'y': ...}) in
    logical px. `rows` from parse_log, `current` the matrix in force."""
    if len(rows) < 4:
        raise ValueError("too few taps")
    inv = invert(current)
    raw, tgt, size = [], [], []
    for tx, ty, px, py, w, h in rows:
        raw.append(apply(inv, px / w, py / h))
        tgt.append((tx / w, ty / h))
        size.append((w, h))
    new = list(current)
    report = {}
    for axis, (lo, dim) in (("x", (0, 0)), ("y", (3, 1))):
        goal = [t[dim] for t in tgt]
        cur_err = [(apply(current, *r)[dim] - g) * s[dim] for r, g, s in zip(raw, goal, size)]
        p, q, r_ = _fit_row(raw, goal)
        fit_err = [((p * x + q * y + r_) - g) * s[dim] for (x, y), g, s in zip(raw, goal, size)]
        before, after = _rms(cur_err), _rms(fit_err)
        changed = after < before * (1 - MIN_GAIN) and before - after >= MIN_GAIN_PX
        if changed:
            new[lo : lo + 3] = [p, q, r_]
        report[axis] = (before, after if changed else before, changed)
    return tuple(new), report
