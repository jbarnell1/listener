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
  is_self        INTEGER NOT NULL DEFAULT 0,        -- the device owner ("myself")
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
  transcribed INTEGER NOT NULL DEFAULT 0,    -- 0 pending · 1 done · -1 failed
  attempts    INTEGER NOT NULL DEFAULT 0,
  error       TEXT,
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
  summary           TEXT,                            -- durable: who they are
  emotion_trend     TEXT,                            -- TRANSIENT: recent mood/context (overwritten)
  traits_json       TEXT,                            -- durable: personality traits
  interests_json    TEXT,                            -- durable: hobbies/passions (gift-relevant)
  dislikes_json     TEXT,                            -- durable: dislikes/sensitivities
  dates_json        TEXT,                            -- durable: important dates [{label,date}]
  notable_json      TEXT,                            -- durable: facts (family/pet/job/place) — NOT tasks
  topics_json       TEXT, recurring_json TEXT, facts_json TEXT,   -- (legacy, unused)
  last_seen         TEXT,
  interaction_count INTEGER NOT NULL DEFAULT 0,
  updated_at        TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS intents (
  id           INTEGER PRIMARY KEY,
  segment_id   INTEGER REFERENCES segments(id),
  speaker_id   INTEGER REFERENCES speakers(id),
  action       TEXT, tier TEXT, due_at TEXT,        -- due_at stored UTC (ADR-017)
  kind         TEXT,                                -- event | task | followup (ADR-026)
  status       TEXT NOT NULL DEFAULT 'pending',     -- pending|scheduled|sent|dismissed
  source_quote TEXT,
  calendar_event_id TEXT, calendar_link TEXT, gtask_id TEXT, synced_at TEXT,  -- Google sync (ADR-026)
  created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS meta (
  key   TEXT PRIMARY KEY,
  value TEXT
);
CREATE TABLE IF NOT EXISTS tags (
  id         INTEGER PRIMARY KEY,
  name       TEXT NOT NULL UNIQUE COLLATE NOCASE,   -- topic label (ADR-029)
  summary    TEXT,                                  -- LLM-maintained running digest
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS transcript_tags (
  transcript_id INTEGER NOT NULL REFERENCES transcripts(id) ON DELETE CASCADE,
  tag_id        INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
  PRIMARY KEY (transcript_id, tag_id)
);
"""

TABLES = ["speakers", "embeddings", "chunks", "transcripts",
          "segments", "profiles", "intents", "meta", "tags"]


def connect(path: str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")  # concurrent readers + a writer
    return conn


def _migrate(conn):
    """Idempotent column adds for DBs created before a field existed."""
    def cols(table):
        return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
    if "is_self" not in cols("speakers"):
        conn.execute("ALTER TABLE speakers ADD COLUMN is_self INTEGER NOT NULL DEFAULT 0")
    have = cols("profiles")
    for col in ("facts_json", "traits_json", "interests_json", "dislikes_json",
                "dates_json", "notable_json", "topics_json", "recurring_json"):
        if col not in have:
            conn.execute(f"ALTER TABLE profiles ADD COLUMN {col} TEXT")
    ch = cols("chunks")
    if "attempts" not in ch:
        conn.execute("ALTER TABLE chunks ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0")
    if "error" not in ch:
        conn.execute("ALTER TABLE chunks ADD COLUMN error TEXT")
    it = cols("intents")
    for col in ("kind", "calendar_event_id", "calendar_link", "gtask_id", "synced_at"):
        if col not in it:
            conn.execute(f"ALTER TABLE intents ADD COLUMN {col} TEXT")
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


def unsynced_intents(conn):
    """Pending intents not yet pushed to Google Calendar/Tasks (ADR-026)."""
    return conn.execute(
        "SELECT i.*, COALESCE(sp.name, 'Unknown_' || sp.id) AS who FROM intents i "
        "LEFT JOIN speakers sp ON sp.id = i.speaker_id "
        "WHERE i.synced_at IS NULL AND i.status != 'dismissed' ORDER BY i.id").fetchall()


def mark_intent_synced(conn, iid, calendar_event_id=None, calendar_link=None, gtask_id=None):
    conn.execute(
        "UPDATE intents SET synced_at=datetime('now'), "
        "calendar_event_id=COALESCE(?, calendar_event_id), "
        "calendar_link=COALESCE(?, calendar_link), "
        "gtask_id=COALESCE(?, gtask_id) WHERE id=?",
        (calendar_event_id, calendar_link, gtask_id, iid))
    conn.commit()


# --- key/value meta + activity feed (ADR-028: "what's new since last check") ---

def meta_get(conn, key, default=None):
    r = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return r["value"] if r else default


def meta_set(conn, key, value):
    conn.execute("INSERT INTO meta(key, value) VALUES(?, ?) "
                 "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))
    conn.commit()


def activity_count(conn, since):
    """How many new conversations + action items since `since` (for the nav badge)."""
    return conn.execute(
        "SELECT (SELECT COUNT(*) FROM transcripts WHERE created_at > ?) + "
        "       (SELECT COUNT(*) FROM intents WHERE created_at > ?)", (since, since)).fetchone()[0]


def activity_since(conn, since):
    transcripts = conn.execute(
        "SELECT t.id, t.created_at, COUNT(s.id) AS n_segments, "
        "(SELECT GROUP_CONCAT(DISTINCT COALESCE(sp.name, 'Unknown_' || sp.id)) "
        " FROM segments s2 JOIN speakers sp ON sp.id = s2.speaker_id "
        " WHERE s2.transcript_id = t.id) AS who "
        "FROM transcripts t LEFT JOIN segments s ON s.transcript_id = t.id "
        "WHERE t.created_at > ? GROUP BY t.id ORDER BY t.id DESC", (since,)).fetchall()
    intents = conn.execute(
        "SELECT i.*, COALESCE(sp.name, 'Unknown_' || sp.id) AS who FROM intents i "
        "LEFT JOIN speakers sp ON sp.id = i.speaker_id "
        "WHERE i.created_at > ? ORDER BY i.id DESC", (since,)).fetchall()
    speakers = conn.execute(
        "SELECT id, COALESCE(name, 'Unknown_' || id) AS label, status, created_at "
        "FROM speakers WHERE created_at > ? ORDER BY id DESC", (since,)).fetchall()
    return {"transcripts": transcripts, "intents": intents, "speakers": speakers}


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

    def arr(col):
        return json.loads((r[col] if col in r.keys() else None) or "[]")
    return {"summary": r["summary"], "recent": r["emotion_trend"],
            "traits": arr("traits_json"), "interests": arr("interests_json"),
            "dislikes": arr("dislikes_json"), "dates": arr("dates_json"),
            "notable": arr("notable_json"),
            "last_seen": r["last_seen"], "interactions": r["interaction_count"],
            "updated_at": r["updated_at"]}


def upsert_profile(conn, sid, *, summary, recent, traits, interests, dislikes,
                   dates, notable, last_seen=None):
    """Insert or merge-update a speaker's profile; bumps interaction_count on update.
    Durable fields are passed pre-merged by profile.py; `recent` (mood) is transient."""
    conn.execute(
        "INSERT INTO profiles(speaker_id, summary, emotion_trend, traits_json, "
        "  interests_json, dislikes_json, dates_json, notable_json, last_seen, "
        "  interaction_count, updated_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,1,datetime('now')) "
        "ON CONFLICT(speaker_id) DO UPDATE SET "
        "  summary=excluded.summary, emotion_trend=excluded.emotion_trend, "
        "  traits_json=excluded.traits_json, interests_json=excluded.interests_json, "
        "  dislikes_json=excluded.dislikes_json, dates_json=excluded.dates_json, "
        "  notable_json=excluded.notable_json, "
        "  last_seen=COALESCE(excluded.last_seen, profiles.last_seen), "
        "  interaction_count=profiles.interaction_count+1, updated_at=datetime('now')",
        (sid, summary, recent, json.dumps(traits), json.dumps(interests),
         json.dumps(dislikes), json.dumps(dates), json.dumps(notable), last_seen))
    conn.commit()


def edit_profile(conn, sid, *, summary, traits, interests, dislikes, dates, notable):
    """User edit of the DURABLE profile fields. Leaves the transient 'recent' mood
    and interaction_count untouched (those stay automatic)."""
    conn.execute(
        "INSERT INTO profiles(speaker_id, summary, traits_json, interests_json, "
        "  dislikes_json, dates_json, notable_json, interaction_count, updated_at) "
        "VALUES(?,?,?,?,?,?,?,0,datetime('now')) "
        "ON CONFLICT(speaker_id) DO UPDATE SET summary=excluded.summary, "
        "  traits_json=excluded.traits_json, interests_json=excluded.interests_json, "
        "  dislikes_json=excluded.dislikes_json, dates_json=excluded.dates_json, "
        "  notable_json=excluded.notable_json, updated_at=datetime('now')",
        (sid, summary, json.dumps(traits), json.dumps(interests), json.dumps(dislikes),
         json.dumps(dates), json.dumps(notable)))
    conn.commit()


def set_do_not_profile(conn, sid, flag):
    conn.execute("UPDATE speakers SET do_not_profile=?, updated_at=datetime('now') WHERE id=?",
                 (1 if flag else 0, sid))
    conn.commit()


def set_relationship(conn, sid, rel):
    conn.execute("UPDATE speakers SET relationship=?, updated_at=datetime('now') WHERE id=?",
                 (rel, sid))
    conn.commit()


def set_self(conn, sid):
    """Mark one speaker as the device owner ('myself'); clears it from all others."""
    conn.execute("UPDATE speakers SET is_self=CASE WHEN id=? THEN 1 ELSE 0 END", (sid,))
    conn.commit()


def get_self(conn):
    return conn.execute("SELECT *, COALESCE(name, 'Unknown_' || id) AS label "
                        "FROM speakers WHERE is_self=1 LIMIT 1").fetchone()


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


# --- processing queue (worker.py drains ingested chunks) ---

def next_pending_chunk(conn, max_attempts=3):
    return conn.execute(
        "SELECT * FROM chunks WHERE transcribed=0 AND attempts < ? "
        "ORDER BY id LIMIT 1", (max_attempts,)).fetchone()


def mark_chunk_done(conn, cid):
    conn.execute("UPDATE chunks SET transcribed=1, error=NULL WHERE id=?", (cid,))
    conn.commit()


def mark_chunk_error(conn, cid, err, max_attempts=3):
    """Record a failed attempt; flag the chunk failed (transcribed=-1) once it
    has exhausted its retries so it stops blocking the queue."""
    conn.execute(
        "UPDATE chunks SET attempts=attempts+1, error=?, "
        "transcribed=CASE WHEN attempts+1 >= ? THEN -1 ELSE 0 END WHERE id=?",
        (str(err)[:500], max_attempts, cid))
    conn.commit()


def queue_stats(conn):
    row = conn.execute(
        "SELECT SUM(transcribed=0) AS pending, SUM(transcribed=1) AS done, "
        "SUM(transcribed=-1) AS failed FROM chunks").fetchone()
    return {"pending": row["pending"] or 0, "done": row["done"] or 0,
            "failed": row["failed"] or 0}


# --- topic tags (multi-label per transcript; LLM-assigned) — ADR-029 ---

def get_or_create_tag(conn, name):
    name = name.strip()
    r = conn.execute("SELECT id FROM tags WHERE name=? COLLATE NOCASE", (name,)).fetchone()
    if r:
        return r["id"]
    return conn.execute("INSERT INTO tags(name) VALUES(?)", (name,)).lastrowid


def set_tag_summary(conn, tag_id, summary):
    conn.execute("UPDATE tags SET summary=?, updated_at=datetime('now') WHERE id=?",
                 (summary, tag_id))


def tag_transcript(conn, tid, tag_id):
    conn.execute("INSERT OR IGNORE INTO transcript_tags(transcript_id, tag_id) VALUES(?,?)",
                 (tid, tag_id))


def untag_transcript(conn, tid, tag_id):
    conn.execute("DELETE FROM transcript_tags WHERE transcript_id=? AND tag_id=?", (tid, tag_id))
    conn.commit()


def list_tags(conn):
    return conn.execute(
        "SELECT t.id, t.name, t.summary, COUNT(tt.transcript_id) AS n, "
        "MAX(tr.created_at) AS last_at "
        "FROM tags t LEFT JOIN transcript_tags tt ON tt.tag_id = t.id "
        "LEFT JOIN transcripts tr ON tr.id = tt.transcript_id "
        "GROUP BY t.id ORDER BY (COUNT(tt.transcript_id) = 0), last_at DESC, t.name").fetchall()


def get_tag(conn, ref):
    s = str(ref).strip()
    if s.isdigit():
        return conn.execute("SELECT * FROM tags WHERE id=?", (int(s),)).fetchone()
    return conn.execute("SELECT * FROM tags WHERE name=? COLLATE NOCASE", (s,)).fetchone()


def tag_transcripts(conn, tag_id):
    return conn.execute(
        "SELECT tr.id, tr.created_at, COUNT(s.id) AS n_segments, "
        "(SELECT GROUP_CONCAT(DISTINCT COALESCE(sp.name, 'Unknown_' || sp.id)) "
        " FROM segments s2 JOIN speakers sp ON sp.id = s2.speaker_id "
        " WHERE s2.transcript_id = tr.id) AS who "
        "FROM transcript_tags tt JOIN transcripts tr ON tr.id = tt.transcript_id "
        "LEFT JOIN segments s ON s.transcript_id = tr.id "
        "WHERE tt.tag_id = ? GROUP BY tr.id ORDER BY tr.id DESC", (tag_id,)).fetchall()


def transcripts_with_all_tags(conn, tag_ids):
    """Snippets carrying ALL of `tag_ids` (multi-tag AND filter)."""
    if not tag_ids:
        return []
    ph = ",".join("?" * len(tag_ids))
    return conn.execute(
        "SELECT tr.id, tr.created_at, "
        "(SELECT COUNT(*) FROM segments s WHERE s.transcript_id=tr.id) AS n_segments, "
        "(SELECT GROUP_CONCAT(DISTINCT COALESCE(sp.name, 'Unknown_' || sp.id)) "
        " FROM segments s2 JOIN speakers sp ON sp.id = s2.speaker_id "
        " WHERE s2.transcript_id = tr.id) AS who "
        "FROM transcript_tags tt JOIN transcripts tr ON tr.id = tt.transcript_id "
        f"WHERE tt.tag_id IN ({ph}) "
        "GROUP BY tr.id HAVING COUNT(DISTINCT tt.tag_id) = ? ORDER BY tr.id DESC",
        (*tag_ids, len(tag_ids))).fetchall()


def transcript_tag_list(conn, tid):
    return conn.execute(
        "SELECT t.id, t.name FROM tags t JOIN transcript_tags tt ON tt.tag_id = t.id "
        "WHERE tt.transcript_id = ? ORDER BY t.name", (tid,)).fetchall()


def merge_tags(conn, src_id, dst_id):
    """Fold tag src into dst: move its transcript links, delete src."""
    conn.execute("INSERT OR IGNORE INTO transcript_tags(transcript_id, tag_id) "
                 "SELECT transcript_id, ? FROM transcript_tags WHERE tag_id=?", (dst_id, src_id))
    conn.execute("DELETE FROM tags WHERE id=?", (src_id,))
    conn.commit()


def rename_tag(conn, tag_id, name):
    conn.execute("UPDATE tags SET name=?, updated_at=datetime('now') WHERE id=?",
                 (name.strip(), tag_id))
    conn.commit()


if __name__ == "__main__":
    c = init_db()
    print(f"db: {DB_PATH}")
    for t in TABLES:
        n = c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        print(f"  {t:<12} {n} rows")
