"""
SQLite persistence layer.

Schema, in plain terms:
  machines   — a station/line the admin adds (e.g. "Bracket Press #2")
  cameras    — a physical camera the admin adds and assigns to a machine.
               Each camera holds its own reference (the "golden sample"
               photo + its measured pixel dimensions), because two cameras
               even on the same machine will sit at different distances/
               angles and so see different pixel sizes for the same part.
  inspections — every photo a camera sends, with its measurement compared
               against that camera's reference.
"""

import sqlite3
import time
from contextlib import contextmanager

from config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS machines (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    name                TEXT NOT NULL,
    location            TEXT,
    max_error_percent   REAL NOT NULL DEFAULT 3.0,
    created_at          REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS cameras (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    name                    TEXT NOT NULL,
    machine_id              INTEGER,
    api_key                 TEXT UNIQUE NOT NULL,
    last_seen               REAL DEFAULT 0,
    reference_image_path    TEXT,
    reference_width_px      REAL,
    reference_height_px     REAL,
    reference_set_at        REAL,
    created_at              REAL NOT NULL,
    FOREIGN KEY (machine_id) REFERENCES machines (id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS inspections (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    camera_id           INTEGER NOT NULL,
    machine_id          INTEGER,
    ts                  REAL NOT NULL,
    width_px            REAL,
    height_px           REAL,
    width_error_pct     REAL,
    height_error_pct    REAL,
    error_pct           REAL,
    result              TEXT NOT NULL,          -- PASS / FAIL / ERROR
    diagnosis           TEXT,
    recommendation      TEXT,
    severity            TEXT,
    image_path          TEXT,
    FOREIGN KEY (camera_id) REFERENCES cameras (id)
);

CREATE INDEX IF NOT EXISTS idx_inspections_camera_ts ON inspections (camera_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_inspections_machine_ts ON inspections (machine_id, ts DESC);
"""


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


NEW_CAMERA_COLUMNS = {
    "auto_enabled":       "INTEGER NOT NULL DEFAULT 0",
    "auto_interval_sec":  "INTEGER NOT NULL DEFAULT 10",
    "pending_command":    "TEXT",
    "preview_image_path": "TEXT",
    "preview_at":         "REAL",
}


def init_db():
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        # light migration so older hub.db files keep working
        have = {r["name"] for r in conn.execute("PRAGMA table_info(cameras)")}
        for col, decl in NEW_CAMERA_COLUMNS.items():
            if col not in have:
                conn.execute(f"ALTER TABLE cameras ADD COLUMN {col} {decl}")


# --------------------------------------------------------------- machines
def create_machine(name, location, max_error_percent):
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO machines (name, location, max_error_percent, created_at) VALUES (?, ?, ?, ?)",
            (name, location, max_error_percent, time.time()),
        )
        return cur.lastrowid


def update_machine(machine_id, name, location, max_error_percent):
    with get_conn() as conn:
        conn.execute(
            "UPDATE machines SET name=?, location=?, max_error_percent=? WHERE id=?",
            (name, location, max_error_percent, machine_id),
        )


def delete_machine(machine_id):
    with get_conn() as conn:
        conn.execute("UPDATE cameras SET machine_id = NULL WHERE machine_id = ?", (machine_id,))
        conn.execute("DELETE FROM machines WHERE id = ?", (machine_id,))


def get_machine(machine_id):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM machines WHERE id = ?", (machine_id,)).fetchone()
        return dict(row) if row else None


def list_machines():
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM machines ORDER BY id").fetchall()
        return [dict(r) for r in rows]


# --------------------------------------------------------------- cameras
def create_camera(name, machine_id, api_key):
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO cameras (name, machine_id, api_key, created_at, last_seen) VALUES (?, ?, ?, ?, 0)",
            (name, machine_id, api_key, time.time()),
        )
        return cur.lastrowid


def update_camera(camera_id, name, machine_id):
    with get_conn() as conn:
        conn.execute("UPDATE cameras SET name=?, machine_id=? WHERE id=?", (name, machine_id, camera_id))


def delete_camera(camera_id):
    with get_conn() as conn:
        conn.execute("DELETE FROM inspections WHERE camera_id = ?", (camera_id,))
        conn.execute("DELETE FROM cameras WHERE id = ?", (camera_id,))


def set_camera_reference(camera_id, image_path, width_px, height_px):
    with get_conn() as conn:
        conn.execute(
            "UPDATE cameras SET reference_image_path=?, reference_width_px=?, reference_height_px=?, reference_set_at=? WHERE id=?",
            (image_path, width_px, height_px, time.time(), camera_id),
        )


def clear_camera_reference(camera_id):
    with get_conn() as conn:
        conn.execute(
            "UPDATE cameras SET reference_image_path=NULL, reference_width_px=NULL, "
            "reference_height_px=NULL, reference_set_at=NULL WHERE id=?",
            (camera_id,),
        )


def set_pending_command(camera_id, command):
    with get_conn() as conn:
        conn.execute("UPDATE cameras SET pending_command=? WHERE id=?", (command, camera_id))


def set_auto(camera_id, enabled, interval_sec):
    with get_conn() as conn:
        conn.execute("UPDATE cameras SET auto_enabled=?, auto_interval_sec=? WHERE id=?",
                     (1 if enabled else 0, int(interval_sec), camera_id))


def set_preview(camera_id, image_path):
    with get_conn() as conn:
        conn.execute("UPDATE cameras SET preview_image_path=?, preview_at=? WHERE id=?",
                     (image_path, time.time(), camera_id))


def clear_preview(camera_id):
    with get_conn() as conn:
        conn.execute("UPDATE cameras SET preview_image_path=NULL, preview_at=NULL WHERE id=?", (camera_id,))


def touch_camera(camera_id):
    with get_conn() as conn:
        conn.execute("UPDATE cameras SET last_seen = ? WHERE id = ?", (time.time(), camera_id))


def get_camera(camera_id):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM cameras WHERE id = ?", (camera_id,)).fetchone()
        return dict(row) if row else None


def get_camera_by_api_key(api_key):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM cameras WHERE api_key = ?", (api_key,)).fetchone()
        return dict(row) if row else None


def list_cameras(machine_id=None):
    with get_conn() as conn:
        if machine_id is not None:
            rows = conn.execute("SELECT * FROM cameras WHERE machine_id = ? ORDER BY id", (machine_id,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM cameras ORDER BY id").fetchall()
        return [dict(r) for r in rows]


# --------------------------------------------------------------- inspections
def insert_inspection(camera_id, machine_id, width_px, height_px, width_error_pct, height_error_pct,
                       error_pct, result, diagnosis, recommendation, severity, image_path):
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO inspections (camera_id, machine_id, ts, width_px, height_px, width_error_pct, "
            "height_error_pct, error_pct, result, diagnosis, recommendation, severity, image_path) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (camera_id, machine_id, time.time(), width_px, height_px, width_error_pct, height_error_pct,
             error_pct, result, diagnosis, recommendation, severity, image_path),
        )
        return cur.lastrowid


def recent_inspections(camera_id, limit=25):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM inspections WHERE camera_id = ? ORDER BY ts DESC LIMIT ?", (camera_id, limit)
        ).fetchall()
        return [dict(r) for r in rows][::-1]


def latest_inspection(camera_id):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM inspections WHERE camera_id = ? ORDER BY ts DESC LIMIT 1", (camera_id,)
        ).fetchone()
        return dict(row) if row else None


def all_recent_inspections(limit=60):
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM inspections ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]


def camera_stats(camera_id, window=200):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT result FROM inspections WHERE camera_id = ? ORDER BY ts DESC LIMIT ?", (camera_id, window)
        ).fetchall()
    total = len(rows)
    passes = sum(1 for r in rows if r["result"] == "PASS")
    return {
        "total": total,
        "passes": passes,
        "fails": total - passes,
        "pass_rate": round(100 * passes / total, 1) if total else None,
    }
