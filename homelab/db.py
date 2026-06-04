#!/usr/bin/env python3
"""SQLite infrastructure for the Listener homelab pipeline.

One local DB (listener.db — gitignored; holds transcripts + voiceprints, so it's
sensitive). Schema mirrors docs/homelab/PIPELINE.md. Pure stdlib so any venv/worker
can import it. Run `python db.py` to init + print a summary.
"""
import os
import sqlite3

HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(HERE, "listener.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS speakers (
  id             INTEGER PRIMARY KEY,
  name           TEXT,                              -- null until labeled
  relationship   TEXT,
  status         TEXT NOT NULL DEFAULT 'unknown',   -- enrolled | unknown
  do_not_profile INTEGER NOT NULL DEFAULT 0,        -- Q-S5 opt-out
  created_at     TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at     TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS embeddings (
  id          INTEGER PRIMARY KEY,
  speaker_id  INTEGER NOT NULL REFERENCES speakers(id) ON DELETE CASCADE,
  vec         BLOB NOT NULL,                        -- float32 (192-d ECAPA)
  dim         INTEGER NOT NULL,
  is_centroid INTEGER NOT NULL DEFAULT 0,
  n_samples   INTEGER NOT NULL DEFAULT 1,           -- for running-mean centroid
  source      TEXT,
  created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS chunks (
  id          INTEGER PRIMARY KEY,
  device      TEXT, seq INTEGER, ts_start TEXT, codec TEXT,
  bytes       INTEGER, path TEXT,
  acked       INTEGER NOT NULL DEFAULT 0,
  transcribed INTEGER NOT NULL DEFAULT 0,
  created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS transcripts (
  id         INTEGER PRIMARY KEY,
  chunk_id   INTEGER REFERENCES chunks(id) ON DELETE CASCADE,
  audio_path TEXT, lang TEXT, words_json TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS segments (
  id            INTEGER PRIMARY KEY,
  transcript_id INTEGER REFERENCES transcripts(id) ON DELETE CASCADE,
  speaker_id    INTEGER REFERENCES speakers(id),
  t_start       REAL, t_end REAL, text TEXT
);
CREATE TABLE IF NOT EXISTS profiles (
  speaker_id        INTEGER PRIMARY KEY REFERENCES speakers(id) ON DELETE CASCADE,
  summary           TEXT, emotion_trend TEXT, topics_json TEXT, recurring_json TEXT,
  last_seen         TEXT,
  interaction_count INTEGER NOT NULL DEFAULT 0,
  updated_at        TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS intents (
  id           INTEGER PRIMARY KEY,
  segment_id   INTEGER REFERENCES segments(id),
  speaker_id   INTEGER REFERENCES speakers(id),
  action       TEXT, tier TEXT, due_at TEXT,        -- due_at stored UTC (ADR-017)
  status       TEXT NOT NULL DEFAULT 'pending',     -- pending|scheduled|sent|dismissed
  source_quote TEXT,
  created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

TABLES = ["speakers", "embeddings", "chunks", "transcripts",
          "segments", "profiles", "intents"]


def connect(path: str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")  # concurrent readers + a writer
    return conn


def init_db(path: str = DB_PATH) -> sqlite3.Connection:
    conn = connect(path)
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


# --- dashboard read/write helpers (pure SQL; usable from the light web venv) ---

def counts(conn):
    return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in TABLES}


def recent_transcripts(conn, limit=25):
    return conn.execute(
        "SELECT t.id, t.audio_path, t.created_at, COUNT(s.id) AS n_segments "
        "FROM transcripts t LEFT JOIN segments s ON s.transcript_id = t.id "
        "GROUP BY t.id ORDER BY t.id DESC LIMIT ?", (limit,)).fetchall()


def transcript(conn, tid):
    return conn.execute("SELECT * FROM transcripts WHERE id=?", (tid,)).fetchone()


def transcript_segments(conn, tid):
    return conn.execute(
        "SELECT s.*, COALESCE(sp.name, 'Unknown_' || sp.id, '?') AS who "
        "FROM segments s LEFT JOIN speakers sp ON sp.id = s.speaker_id "
        "WHERE s.transcript_id = ? ORDER BY s.t_start", (tid,)).fetchall()


def list_speakers(conn):
    return conn.execute(
        "SELECT sp.id, sp.name, sp.status, sp.relationship, "
        "COALESCE(sp.name, 'Unknown_' || sp.id) AS label, "
        "(SELECT COUNT(*) FROM segments s WHERE s.speaker_id = sp.id) AS n_segments "
        "FROM speakers sp ORDER BY (sp.status = 'unknown'), sp.id").fetchall()


def get_speaker(conn, sid):
    return conn.execute(
        "SELECT *, COALESCE(name, 'Unknown_' || id) AS label FROM speakers WHERE id=?",
        (sid,)).fetchone()


def speaker_segments(conn, sid, limit=50):
    return conn.execute(
        "SELECT s.*, t.audio_path FROM segments s JOIN transcripts t ON t.id = s.transcript_id "
        "WHERE s.speaker_id = ? ORDER BY s.id DESC LIMIT ?", (sid, limit)).fetchall()


def get_segment(conn, seg_id):
    return conn.execute(
        "SELECT s.*, t.audio_path FROM segments s JOIN transcripts t ON t.id = s.transcript_id "
        "WHERE s.id = ?", (seg_id,)).fetchone()


def unknown_speakers(conn):
    return conn.execute(
        "SELECT sp.id, COALESCE(sp.name, 'Unknown_' || sp.id) AS label, "
        "(SELECT COUNT(*) FROM segments s WHERE s.speaker_id = sp.id) AS n_segments "
        "FROM speakers sp WHERE sp.status = 'unknown' ORDER BY sp.id").fetchall()


def enrolled_speakers(conn):
    return conn.execute(
        "SELECT id, name FROM speakers WHERE status='enrolled' AND name IS NOT NULL "
        "ORDER BY name").fetchall()


def rename_speaker(conn, sid, name):
    conn.execute("UPDATE speakers SET name=?, status='enrolled', updated_at=datetime('now') "
                 "WHERE id=?", (name, sid))
    conn.commit()


def merge_speakers(conn, src_id, dst_id):
    """Merge src speaker into dst: weighted-average their centroids (so dst's
    voiceprint improves), reassign src's segments to dst, delete src. Pure stdlib."""
    import array
    cur = conn.cursor()
    s = cur.execute("SELECT vec, n_samples FROM embeddings WHERE speaker_id=? AND is_centroid=1",
                    (src_id,)).fetchone()
    d = cur.execute("SELECT id, vec, n_samples FROM embeddings WHERE speaker_id=? AND is_centroid=1",
                    (dst_id,)).fetchone()
    if s and d:
        sv, dv = array.array("f", s["vec"]), array.array("f", d["vec"])
        ns, nd = s["n_samples"], d["n_samples"]
        merged = array.array("f", [(dv[i] * nd + sv[i] * ns) / (nd + ns) for i in range(len(dv))])
        cur.execute("UPDATE embeddings SET vec=?, n_samples=? WHERE id=?",
                    (merged.tobytes(), nd + ns, d["id"]))
    cur.execute("UPDATE segments SET speaker_id=? WHERE speaker_id=?", (dst_id, src_id))
    cur.execute("DELETE FROM speakers WHERE id=?", (src_id,))  # cascades its embeddings
    conn.commit()


if __name__ == "__main__":
    c = init_db()
    print(f"db: {DB_PATH}")
    for t in TABLES:
        n = c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        print(f"  {t:<12} {n} rows")
