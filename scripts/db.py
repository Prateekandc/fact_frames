"""
SQLite schema + helper functions for the detection pipeline.

Design note: this schema is deliberately shaped like a graph-in-waiting.
cameras / chunks / detections are the "nodes" and foreign keys are the
"edges" you'll later lift into a proper knowledge graph (Person, Machine,
Camera, Timestamp, Location/Activity) without changing how data is written.
"""

import sqlite3
import os
from contextlib import contextmanager

DB_PATH = os.environ.get(
    "PIPELINE_DB_PATH",
    os.path.join(os.path.dirname(__file__), "..", "db", "detections.db"),
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS cameras (
    camera_id   TEXT PRIMARY KEY,
    name        TEXT,
    rtsp_url    TEXT,
    enabled     INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS chunks (
    chunk_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    camera_id       TEXT NOT NULL,
    file_path       TEXT NOT NULL UNIQUE,
    start_time_utc  TEXT NOT NULL,     -- ISO8601, derived from filename/strftime
    duration_sec    REAL,
    status          TEXT DEFAULT 'pending',  -- pending | processing | done | failed
    processed_at    TEXT,
    error_message   TEXT,
    FOREIGN KEY (camera_id) REFERENCES cameras(camera_id)
);
CREATE INDEX IF NOT EXISTS idx_chunks_status ON chunks(status);
CREATE INDEX IF NOT EXISTS idx_chunks_camera_time ON chunks(camera_id, start_time_utc);

CREATE TABLE IF NOT EXISTS detections (
    detection_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    camera_id       TEXT NOT NULL,
    chunk_id        INTEGER NOT NULL,
    timestamp_utc   TEXT NOT NULL,     -- absolute time of this detection
    frame_offset_sec REAL,             -- offset within the chunk, for scrubbing to evidence
    person_track_id TEXT,              -- temporary ID, stable only within one chunk unless you add cross-chunk re-id later
    machine_id      TEXT,              -- NULL until machine detection model exists
    bbox_x1         REAL, bbox_y1 REAL, bbox_x2 REAL, bbox_y2 REAL,
    confidence      REAL,
    class_name      TEXT DEFAULT 'person',
    FOREIGN KEY (camera_id) REFERENCES cameras(camera_id),
    FOREIGN KEY (chunk_id) REFERENCES chunks(chunk_id)
);
CREATE INDEX IF NOT EXISTS idx_det_camera_time ON detections(camera_id, timestamp_utc);
CREATE INDEX IF NOT EXISTS idx_det_chunk ON detections(chunk_id);
CREATE INDEX IF NOT EXISTS idx_det_person ON detections(person_track_id);
"""


@contextmanager
def get_conn():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL;")  # allows ingestion + processing to write concurrently
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.executescript(SCHEMA)


def upsert_camera(camera_id, name, rtsp_url, enabled=1):
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO cameras (camera_id, name, rtsp_url, enabled)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(camera_id) DO UPDATE SET
                 name=excluded.name, rtsp_url=excluded.rtsp_url, enabled=excluded.enabled""",
            (camera_id, name, rtsp_url, enabled),
        )


def register_chunk(camera_id, file_path, start_time_utc, duration_sec):
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT OR IGNORE INTO chunks (camera_id, file_path, start_time_utc, duration_sec)
               VALUES (?, ?, ?, ?)""",
            (camera_id, file_path, start_time_utc, duration_sec),
        )
        return cur.lastrowid


def claim_next_pending_chunk(worker_tag="worker"):
    """Atomically claim one pending chunk so multiple GPU workers don't double-process it."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM chunks WHERE status='pending' ORDER BY start_time_utc ASC LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        conn.execute(
            "UPDATE chunks SET status='processing' WHERE chunk_id=?", (row["chunk_id"],)
        )
        return dict(row)


def mark_chunk_done(chunk_id):
    with get_conn() as conn:
        conn.execute(
            "UPDATE chunks SET status='done', processed_at=datetime('now') WHERE chunk_id=?",
            (chunk_id,),
        )


def mark_chunk_failed(chunk_id, error_message):
    with get_conn() as conn:
        conn.execute(
            "UPDATE chunks SET status='failed', processed_at=datetime('now'), error_message=? WHERE chunk_id=?",
            (str(error_message)[:500], chunk_id),
        )


def insert_detections(rows):
    """rows: list of dicts matching the detections table columns (minus detection_id)."""
    if not rows:
        return
    with get_conn() as conn:
        conn.executemany(
            """INSERT INTO detections
               (camera_id, chunk_id, timestamp_utc, frame_offset_sec, person_track_id,
                machine_id, bbox_x1, bbox_y1, bbox_x2, bbox_y2, confidence, class_name)
               VALUES (:camera_id, :chunk_id, :timestamp_utc, :frame_offset_sec, :person_track_id,
                       :machine_id, :bbox_x1, :bbox_y1, :bbox_x2, :bbox_y2, :confidence, :class_name)""",
            rows,
        )
