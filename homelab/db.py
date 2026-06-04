#!/usr/bin/env python3
"""SQLite infrastructure for the Listener homelab pipeline.

One local DB (listener.db — gitignored; holds transcripts + voiceprints, so it's
sensitive). Schema mirrors docs/homelab/PIPELINE.md. Pure stdlib so any venv/worker
can import it. Run `python db.py` to init + print a summary.
"""
import json
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
  facts_json        TEXT,                            -- durable learned facts (LLM-merged)
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


def _migrate(conn):
    """Idempotent column adds for DBs created before a field existed."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(profiles)")}
    if "facts_json" not in cols:
        conn.execute("ALTER TABLE profiles ADD COLUMN facts_json TEXT")
    conn.commit()


def init_db(path: str = DB_PATH) -> sqlite3.Connection:
    conn = connect(path)
    conn.executescript(SCHEMA)
    _migrate(conn)
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


def list_intents(conn, tier=None):
    base = ("SELECT i.*, COALESCE(sp.name, 'Unknown_' || sp.id, '—') AS who "
            "FROM intents i LEFT JOIN speakers sp ON sp.id = i.speaker_id "
            "WHERE i.status != 'dismissed' ")
    if tier:
        return conn.execute(base + "AND i.tier=? ORDER BY i.due_at IS NULL, i.due_at",
                            (tier,)).fetchall()
    return conn.execute(base + "ORDER BY i.due_at IS NULL, i.due_at").fetchall()


def speaker_intents(conn, sid):
    return conn.execute(
        "SELECT i.*, COALESCE(sp.name, 'Unknown_' || sp.id) AS who FROM intents i "
        "LEFT JOIN speakers sp ON sp.id = i.speaker_id "
        "WHERE i.speaker_id=? AND i.status!='dismissed' "
        "ORDER BY i.due_at IS NULL, i.due_at", (sid,)).fetchall()


def dismiss_intent(conn, iid):
    conn.execute("UPDATE intents SET status='dismissed' WHERE id=?", (iid,))
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
    cur.execute("UPDATE intents SET speaker_id=? WHERE speaker_id=?", (dst_id, src_id))
    cur.execute("DELETE FROM speakers WHERE id=?", (src_id,))  # cascades its embeddings/profile
    conn.commit()


# --- speaker profiles (continuously enriched by the local LLM; profile.py) ---

def get_profile(conn, sid):
    r = conn.execute("SELECT * FROM profiles WHERE speaker_id=?", (sid,)).fetchone()
    if not r:
        return None
    return {"summary": r["summary"], "emotion_trend": r["emotion_trend"],
            "topics": json.loads(r["topics_json"] or "[]"),
            "recurring": json.loads(r["recurring_json"] or "[]"),
            "facts": json.loads(r["facts_json"] or "[]"),
            "last_seen": r["last_seen"], "interactions": r["interaction_count"],
            "updated_at": r["updated_at"]}


def upsert_profile(conn, sid, *, summary, emotion_trend, topics, recurring, facts, last_seen=None):
    """Insert or merge-update a speaker's profile; bumps interaction_count on update."""
    conn.execute(
        "INSERT INTO profiles(speaker_id, summary, emotion_trend, topics_json, "
        "  recurring_json, facts_json, last_seen, interaction_count, updated_at) "
        "VALUES(?,?,?,?,?,?,?,1,datetime('now')) "
        "ON CONFLICT(speaker_id) DO UPDATE SET "
        "  summary=excluded.summary, emotion_trend=excluded.emotion_trend, "
        "  topics_json=excluded.topics_json, recurring_json=excluded.recurring_json, "
        "  facts_json=excluded.facts_json, "
        "  last_seen=COALESCE(excluded.last_seen, profiles.last_seen), "
        "  interaction_count=profiles.interaction_count+1, updated_at=datetime('now')",
        (sid, summary, emotion_trend, json.dumps(topics), json.dumps(recurring),
         json.dumps(facts), last_seen))
    conn.commit()


def set_do_not_profile(conn, sid, flag):
    conn.execute("UPDATE speakers SET do_not_profile=?, updated_at=datetime('now') WHERE id=?",
                 (1 if flag else 0, sid))
    conn.commit()


def speaker_transcript_ids(conn, sid):
    return [r[0] for r in conn.execute(
        "SELECT DISTINCT transcript_id FROM segments WHERE speaker_id=? "
        "ORDER BY transcript_id", (sid,)).fetchall()]


def delete_speaker(conn, sid):
    """Smart cascade (privacy delete): remove this speaker's tasks, profile,
    voiceprints, and THEIR lines in every transcript. A transcript left with no
    segments is deleted (and its audio file unlinked); shared transcripts keep
    the other speakers' lines. Returns a summary of what was removed."""
    cur = conn.cursor()
    tids = [r[0] for r in cur.execute(
        "SELECT DISTINCT transcript_id FROM segments WHERE speaker_id=?", (sid,)).fetchall()]
    n_tasks = cur.execute("SELECT COUNT(*) FROM intents WHERE speaker_id=?", (sid,)).fetchone()[0]
    n_segs = cur.execute("SELECT COUNT(*) FROM segments WHERE speaker_id=?", (sid,)).fetchone()[0]
    cur.execute("DELETE FROM intents WHERE speaker_id=?", (sid,))
    cur.execute("DELETE FROM segments WHERE speaker_id=?", (sid,))
    removed_tx = 0
    for tid in tids:
        if cur.execute("SELECT COUNT(*) FROM segments WHERE transcript_id=?",
                       (tid,)).fetchone()[0] == 0:
            row = cur.execute("SELECT audio_path FROM transcripts WHERE id=?", (tid,)).fetchone()
            cur.execute("DELETE FROM transcripts WHERE id=?", (tid,))
            removed_tx += 1
            ap = row["audio_path"] if row else None
            if ap and os.path.exists(ap):
                try:
                    os.remove(ap)
                except OSError:
                    pass
    cur.execute("DELETE FROM speakers WHERE id=?", (sid,))   # cascades embeddings + profile
    conn.commit()
    return {"speaker_id": sid, "tasks": n_tasks, "segments": n_segs,
            "transcripts_removed": removed_tx}


if __name__ == "__main__":
    c = init_db()
    print(f"db: {DB_PATH}")
    for t in TABLES:
        n = c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        print(f"  {t:<12} {n} rows")
