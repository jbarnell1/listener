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


if __name__ == "__main__":
    c = init_db()
    print(f"db: {DB_PATH}")
    for t in TABLES:
        n = c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        print(f"  {t:<12} {n} rows")
