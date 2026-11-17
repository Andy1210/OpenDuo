"""The local store: /var/lib/duo2-battery/usage.db (SQLite, root:duo2-battery
0640 in a 0750 directory: the GUI user reads it through the duo2-battery
group; nothing leaves the phone). Bounded: rows older than KEEP_DAYS go at
every flush. The calibration's statistics (no personal data) are kept in
`meta` and survive "Clear usage history"."""

import json
import os
import sqlite3
import time

DB_PATH = "/var/lib/duo2-battery/usage.db"
KEEP_DAYS = 7
BUCKET_S = 600
SCHEMA = 1

TABLES = """
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS samples (
    t REAL PRIMARY KEY, level INTEGER, status TEXT, charger INTEGER,
    current_ma REAL, voltage_v REAL, charge_mah REAL, usb_mw REAL, lit INTEGER);
CREATE TABLE IF NOT EXISTS periods (t0 REAL, t1 REAL, what TEXT);
CREATE INDEX IF NOT EXISTS periods_t1 ON periods (t1);
CREATE TABLE IF NOT EXISTS usage (
    bucket REAL, kind TEXT, key TEXT, mah REAL, cpu_mah REAL, gpu_mah REAL, extra TEXT,
    PRIMARY KEY (bucket, kind, key));
CREATE TABLE IF NOT EXISTS groups (
    t0 REAL, t1 REAL, kind TEXT, measured REAL, prior REAL, model REAL, scale REAL);
CREATE INDEX IF NOT EXISTS groups_t1 ON groups (t1);
"""


def connect(path=DB_PATH, readonly=False):
    if readonly:
        db = sqlite3.connect("file:%s?mode=ro" % path, uri=True, timeout=3)
    else:
        db = sqlite3.connect(path, timeout=5)
        db.executescript(TABLES)
        db.execute("INSERT OR IGNORE INTO meta VALUES ('schema', ?)", (str(SCHEMA),))
        db.commit()
    return db


def get_meta(db, key, default=None):
    row = db.execute("SELECT v FROM meta WHERE k = ?", (key,)).fetchone()
    if row is None:
        return default
    try:
        return json.loads(row[0])
    except ValueError:
        return default


def set_meta(db, key, value):
    db.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (key, json.dumps(value)))


def bucket_of(t):
    return float(int(t // BUCKET_S) * BUCKET_S)


def merge_extra(a, b):
    out = dict(a or {})
    for k, v in (b or {}).items():
        out[k] = out.get(k, 0.0) + v
    return out


def write(db, samples=(), periods=(), usage=None, groups=()):
    """One transaction: new samples, finished periods, usage per (bucket,
    kind, key) added to what is stored, calibration groups."""
    with db:
        db.executemany("INSERT OR REPLACE INTO samples VALUES (?,?,?,?,?,?,?,?,?)", samples)
        db.executemany("INSERT INTO periods VALUES (?,?,?)", periods)
        for (bucket, kind, key), u in (usage or {}).items():
            row = db.execute(
                "SELECT mah, cpu_mah, gpu_mah, extra FROM usage WHERE bucket=? AND kind=? AND key=?",
                (bucket, kind, key),
            ).fetchone()
            mah, cpu, gpu, extra = (
                u["mah"],
                u.get("cpu_mah", 0.0),
                u.get("gpu_mah", 0.0),
                u.get("extra") or {},
            )
            if row:
                mah += row[0] or 0.0
                cpu += row[1] or 0.0
                gpu += row[2] or 0.0
                extra = merge_extra(json.loads(row[3]) if row[3] else {}, extra)
            db.execute(
                "INSERT OR REPLACE INTO usage VALUES (?,?,?,?,?,?,?)",
                (bucket, kind, key, mah, cpu, gpu, json.dumps(extra, separators=(",", ":"))),
            )
        db.executemany("INSERT INTO groups VALUES (?,?,?,?,?,?,?)", groups)


def prune(db, now=None, keep_days=KEEP_DAYS):
    """Drop everything older than keep_days; returns the rows dropped."""
    cut = (now if now is not None else time.time()) - keep_days * 86400
    n = 0
    with db:
        for sql in (
            "DELETE FROM samples WHERE t < ?",
            "DELETE FROM periods WHERE t1 < ?",
            "DELETE FROM usage WHERE bucket + %d < ?" % BUCKET_S,
            "DELETE FROM groups WHERE t1 < ?",
        ):
            n += db.execute(sql, (cut,)).rowcount
    return n


def clear(db):
    """ "Clear usage history": every sample, period, usage row and group;
    the calibration (meta) stays."""
    with db:
        for table in ("samples", "periods", "usage", "groups"):
            db.execute("DELETE FROM %s" % table)
    db.execute("VACUUM")


def ensure_dir(path=DB_PATH):
    os.makedirs(os.path.dirname(path), exist_ok=True)
