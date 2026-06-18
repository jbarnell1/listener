#!/usr/bin/env python3
"""SQLite infrastructure for the Listener homelab pipeline.

One local DB (listener.db — gitignored; holds transcripts + voiceprints, so it's
sensitive). Schema mirrors docs/homelab/PIPELINE.md. Pure stdlib so any venv/worker
can import it. Run `python db.py` to init + print a summary.
"""
import json
import os
import secrets
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
  profile_dirty  INTEGER NOT NULL DEFAULT 0,        -- needs a (debounced) profile refresh, ADR-038
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
  marked      INTEGER NOT NULL DEFAULT 0,    -- device REC/"remember" press → deliberate capture (ADR-038)
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
  transcript_id INTEGER REFERENCES transcripts(id),   -- source conversation (backlink, ADR-036)
  speaker_id   INTEGER REFERENCES speakers(id),
  action       TEXT, tier TEXT, due_at TEXT,        -- due_at stored UTC (ADR-017)
  kind         TEXT,                                -- event | task | followup (ADR-026)
  owner        TEXT,                                -- who is responsible (name or "me"); ADR-038
  recurrence   TEXT,                                -- none|daily|weekly:TU|monthly… (ADR-038)
  status       TEXT NOT NULL DEFAULT 'pending',     -- pending|suggested|dismissed
  source_quote TEXT,
  confidence   REAL,                                -- extraction confidence (triage, ADR-033)
  importance   INTEGER,                             -- significance 1-5 (ADR-043)
  close_suggested INTEGER NOT NULL DEFAULT 0,       -- reconciler thinks it's resolved (ADR-032)
  close_kind   TEXT, close_note TEXT, closed_at TEXT,  -- completed|cancelled · evidence · when
  calendar_event_id TEXT, calendar_link TEXT, gtask_id TEXT, synced_at TEXT,  -- Google sync (ADR-026)
  created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS meta (
  key   TEXT PRIMARY KEY,
  value TEXT
);
CREATE TABLE IF NOT EXISTS decisions (        -- Review-queue supervision signal (ADR-041)
  id            INTEGER PRIMARY KEY,
  action        TEXT NOT NULL,                -- approve | dismiss | confirm_close | keep | undo
  intent_kind   TEXT, confidence REAL,
  was_suggested INTEGER NOT NULL DEFAULT 0,
  created_at    TEXT NOT NULL DEFAULT (datetime('now'))
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
CREATE TABLE IF NOT EXISTS device_status (        -- periodic device telemetry (ADR-031)
  device_id  TEXT PRIMARY KEY,
  battery_mv INTEGER, rssi INTEGER, ssid TEXT, ip TEXT,
  uptime_s   INTEGER, free_heap INTEGER, fw TEXT, seq INTEGER,
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS devices (              -- per-device HMAC keys + revocation (ADR-042)
  device_id  TEXT PRIMARY KEY,
  key        TEXT NOT NULL,
  label      TEXT,
  active     INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  revoked_at TEXT
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
    if "profile_dirty" not in cols("speakers"):
        conn.execute("ALTER TABLE speakers ADD COLUMN profile_dirty INTEGER NOT NULL DEFAULT 0")
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
    if "marked" not in ch:
        conn.execute("ALTER TABLE chunks ADD COLUMN marked INTEGER NOT NULL DEFAULT 0")
    it = cols("intents")
    for col in ("kind", "calendar_event_id", "calendar_link", "gtask_id", "synced_at",
                "close_kind", "close_note", "closed_at", "owner", "recurrence"):
        if col not in it:
            conn.execute(f"ALTER TABLE intents ADD COLUMN {col} TEXT")
    if "confidence" not in it:
        conn.execute("ALTER TABLE intents ADD COLUMN confidence REAL")
    if "close_suggested" not in it:
        conn.execute("ALTER TABLE intents ADD COLUMN close_suggested INTEGER NOT NULL DEFAULT 0")
    if "transcript_id" not in it:
        conn.execute("ALTER TABLE intents ADD COLUMN transcript_id INTEGER")
    if "importance" not in it:
        conn.execute("ALTER TABLE intents ADD COLUMN importance INTEGER")
    conn.commit()


def _init_fts(conn):
    """Best-effort FTS5 index over segment text for fast search at scale (ADR-042).
    No-ops if the SQLite build lacks FTS5 — search then falls back to LIKE."""
    try:
        conn.executescript("""
        CREATE VIRTUAL TABLE IF NOT EXISTS segments_fts USING fts5(
          text, content='segments', content_rowid='id');
        CREATE TRIGGER IF NOT EXISTS segments_ai AFTER INSERT ON segments BEGIN
          INSERT INTO segments_fts(rowid, text) VALUES (new.id, new.text); END;
        CREATE TRIGGER IF NOT EXISTS segments_ad AFTER DELETE ON segments BEGIN
          INSERT INTO segments_fts(segments_fts, rowid, text) VALUES('delete', old.id, old.text); END;
        CREATE TRIGGER IF NOT EXISTS segments_au AFTER UPDATE ON segments BEGIN
          INSERT INTO segments_fts(segments_fts, rowid, text) VALUES('delete', old.id, old.text);
          INSERT INTO segments_fts(rowid, text) VALUES (new.id, new.text); END;
        """)
        if (conn.execute("SELECT COUNT(*) FROM segments").fetchone()[0]
                != conn.execute("SELECT COUNT(*) FROM segments_fts").fetchone()[0]):
            conn.execute("INSERT INTO segments_fts(segments_fts) VALUES('rebuild')")
        conn.commit()
    except sqlite3.OperationalError:
        pass


def init_db(path: str = DB_PATH) -> sqlite3.Connection:
    conn = connect(path)
    conn.executescript(SCHEMA)
    _migrate(conn)
    _init_fts(conn)
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


def search_transcripts(conn, q, limit=40):
    """Keyword search across all spoken text; groups matches by conversation. Uses FTS5
    (prefix-AND) when available, else a LIKE scan (ADR-042)."""
    q = (q or "").strip()
    if not q:
        return []
    rows = None
    try:
        terms = " ".join(f'"{t}"*' for t in q.split() if t)
        if terms:
            rows = conn.execute(
                "SELECT s.transcript_id, s.text, t.created_at, "
                "COALESCE(sp.name, 'Unknown_' || sp.id, '?') AS who "
                "FROM segments_fts f JOIN segments s ON s.id = f.rowid "
                "JOIN transcripts t ON t.id = s.transcript_id "
                "LEFT JOIN speakers sp ON sp.id = s.speaker_id "
                "WHERE segments_fts MATCH ? ORDER BY s.transcript_id DESC, s.t_start LIMIT ?",
                (terms, limit * 5)).fetchall()
    except sqlite3.OperationalError:
        rows = None
    if rows is None:                                  # FTS unavailable or bad query → LIKE
        like = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        rows = conn.execute(
            "SELECT s.transcript_id, s.text, t.created_at, "
            "COALESCE(sp.name, 'Unknown_' || sp.id, '?') AS who "
            "FROM segments s JOIN transcripts t ON t.id = s.transcript_id "
            "LEFT JOIN speakers sp ON sp.id = s.speaker_id "
            "WHERE s.text LIKE ? ESCAPE '\\' "
            "ORDER BY s.transcript_id DESC, s.t_start LIMIT ?", (like, limit * 5)).fetchall()
    groups = {}
    for r in rows:
        g = groups.setdefault(r["transcript_id"], {
            "id": r["transcript_id"], "created_at": r["created_at"], "matches": []})
        if len(g["matches"]) < 3:
            g["matches"].append({"who": r["who"], "text": r["text"]})
    return list(groups.values())[:limit]


def transcript_segments(conn, tid):
    return conn.execute(
        "SELECT s.*, COALESCE(sp.name, 'Unknown_' || sp.id, '?') AS who "
        "FROM segments s LEFT JOIN speakers sp ON sp.id = s.speaker_id "
        "WHERE s.transcript_id = ? ORDER BY s.t_start", (tid,)).fetchall()


def list_speakers(conn):
    return conn.execute(
        "SELECT sp.id, sp.name, sp.status, sp.relationship, sp.is_self, sp.do_not_profile, "
        "COALESCE(sp.name, 'Unknown_' || sp.id) AS label, "
        "(SELECT COUNT(*) FROM segments s WHERE s.speaker_id = sp.id) AS n_segments "
        "FROM speakers sp ORDER BY (sp.status = 'unknown'), sp.is_self DESC, sp.id").fetchall()


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


def speaker_roster(conn):
    """Named people for LLM context — resolves speaker labels + task ownership
    (who is 'me', who is the wife/coworker/etc.). ADR-038."""
    return conn.execute(
        "SELECT name, relationship, is_self FROM speakers "
        "WHERE status='enrolled' AND name IS NOT NULL ORDER BY is_self DESC, name").fetchall()


def list_intents(conn, tier=None):
    base = ("SELECT i.*, COALESCE(sp.name, 'Unknown_' || sp.id, '—') AS who "
            "FROM intents i LEFT JOIN speakers sp ON sp.id = i.speaker_id "
            "WHERE i.status NOT IN ('dismissed','suggested') AND i.close_suggested=0 ")
    if tier:
        return conn.execute(base + "AND i.tier=? ORDER BY i.due_at IS NULL, i.due_at",
                            (tier,)).fetchall()
    return conn.execute(base + "ORDER BY i.due_at IS NULL, i.due_at").fetchall()


def speaker_intents(conn, sid):
    return conn.execute(
        "SELECT i.*, COALESCE(sp.name, 'Unknown_' || sp.id) AS who FROM intents i "
        "LEFT JOIN speakers sp ON sp.id = i.speaker_id "
        "WHERE i.speaker_id=? AND i.status NOT IN ('dismissed','suggested') AND i.close_suggested=0 "
        "ORDER BY i.due_at IS NULL, i.due_at", (sid,)).fetchall()


def dismiss_intent(conn, iid):
    conn.execute("UPDATE intents SET status='dismissed' WHERE id=?", (iid,))
    conn.commit()


# --- triage (ADR-033) + closure reconciliation (ADR-032) ---

def suggested_intents(conn):
    """Low-confidence new items awaiting your approval (Review queue, ADR-033)."""
    return conn.execute(
        "SELECT i.*, COALESCE(sp.name, 'Unknown_' || sp.id, '—') AS who "
        "FROM intents i LEFT JOIN speakers sp ON sp.id = i.speaker_id "
        "WHERE i.status='suggested' "
        "ORDER BY COALESCE(i.importance,3) DESC, i.created_at DESC, i.id DESC").fetchall()


def close_pending_intents(conn):
    """Open items the reconciler flagged as probably resolved, awaiting confirmation
    (events; tasks auto-close). (ADR-032)"""
    return conn.execute(
        "SELECT i.*, COALESCE(sp.name, 'Unknown_' || sp.id, '—') AS who "
        "FROM intents i LEFT JOIN speakers sp ON sp.id = i.speaker_id "
        "WHERE i.close_suggested=1 AND i.status NOT IN ('dismissed','suggested') "
        "ORDER BY i.id DESC").fetchall()


def recent_auto_closed(conn, hours=48, limit=8):
    """Recently auto-closed items, for the undo affordance + feed (ADR-032)."""
    return conn.execute(
        "SELECT i.*, COALESCE(sp.name, 'Unknown_' || sp.id, '—') AS who "
        "FROM intents i LEFT JOIN speakers sp ON sp.id = i.speaker_id "
        "WHERE i.status='dismissed' AND i.close_note IS NOT NULL "
        "AND i.closed_at > datetime('now', ?) ORDER BY i.closed_at DESC LIMIT ?",
        (f"-{int(hours)} hours", limit)).fetchall()


def open_intents_for_reconcile(conn, limit=80):
    """Compact list of currently-open items for the closure reconciler (ADR-032)."""
    return conn.execute(
        "SELECT i.id, i.action, i.kind, i.due_at, "
        "COALESCE(sp.name, 'Unknown_' || sp.id, '—') AS who FROM intents i "
        "LEFT JOIN speakers sp ON sp.id = i.speaker_id "
        "WHERE i.status NOT IN ('dismissed','suggested') AND i.close_suggested=0 "
        "ORDER BY i.id DESC LIMIT ?", (limit,)).fetchall()


def approve_intent(conn, iid):
    """Promote a suggested item to active (caller then syncs it to Google)."""
    conn.execute("UPDATE intents SET status='pending', close_suggested=0 WHERE id=?", (iid,))
    conn.commit()


def approve_all_suggested(conn):
    """Promote every suggested item to active. Returns how many."""
    n = conn.execute("UPDATE intents SET status='pending' WHERE status='suggested'").rowcount
    conn.commit()
    return n


def dismiss_all_suggested(conn):
    """Dismiss every suggested item at once. Returns how many."""
    n = conn.execute("UPDATE intents SET status='dismissed' WHERE status='suggested'").rowcount
    conn.commit()
    return n


def suggest_close(conn, iid, kind, note):
    """Flag an open item as probably resolved — held for user confirmation (events)."""
    conn.execute("UPDATE intents SET close_suggested=1, close_kind=?, close_note=? WHERE id=?",
                 (kind, note, iid))
    conn.commit()


def close_intent(conn, iid, kind=None, note=None):
    """Close an item out (auto-complete/cancel, or confirmed). Records why + when so
    it can show in the feed and be undone. COALESCE keeps any reason set earlier."""
    conn.execute("UPDATE intents SET status='dismissed', close_suggested=0, "
                 "close_kind=COALESCE(?, close_kind), close_note=COALESCE(?, close_note), "
                 "closed_at=datetime('now') WHERE id=?", (kind, note, iid))
    conn.commit()


def keep_open(conn, iid):
    """User rejected a close suggestion — keep the item active, clear the flag."""
    conn.execute("UPDATE intents SET close_suggested=0, close_kind=NULL, close_note=NULL "
                 "WHERE id=?", (iid,))
    conn.commit()


def undo_close(conn, iid):
    """Reopen an auto-closed item and clear its Google linkage so the next sync
    re-creates the Calendar event / Task."""
    conn.execute("UPDATE intents SET status='pending', close_suggested=0, closed_at=NULL, "
                 "close_note=NULL, close_kind=NULL, synced_at=NULL, "
                 "calendar_event_id=NULL, calendar_link=NULL, gtask_id=NULL WHERE id=?", (iid,))
    conn.commit()


def intent_brief(conn, iid):
    """(kind, confidence, was_suggested) for a single intent — for decision logging."""
    r = conn.execute("SELECT kind, confidence, status FROM intents WHERE id=?", (iid,)).fetchone()
    if not r:
        return None, None, 0
    return r["kind"], r["confidence"], 1 if r["status"] == "suggested" else 0


def log_decision(conn, action, intent_kind=None, confidence=None, was_suggested=0):
    """Record a Review-queue decision as a supervision signal (ADR-041)."""
    conn.execute("INSERT INTO decisions(action, intent_kind, confidence, was_suggested) "
                 "VALUES(?,?,?,?)", (action, intent_kind, confidence, 1 if was_suggested else 0))
    conn.commit()


def decision_stats(conn, days=21):
    """Approve/dismiss tallies for triaged ('suggested') items over the window."""
    r = conn.execute(
        "SELECT SUM(action='approve') AS a, SUM(action='dismiss') AS d FROM decisions "
        "WHERE was_suggested=1 AND created_at > datetime('now', ?)",
        (f"-{int(days)} days",)).fetchone()
    a, d = (r["a"] or 0), (r["d"] or 0)
    total = a + d
    return {"approves": a, "dismisses": d, "total": total,
            "dismiss_rate": (d / total) if total else 0.0}


def unsynced_intents(conn):
    """Pending intents not yet pushed to Google Calendar/Tasks (ADR-026)."""
    return conn.execute(
        "SELECT i.*, COALESCE(sp.name, 'Unknown_' || sp.id) AS who FROM intents i "
        "LEFT JOIN speakers sp ON sp.id = i.speaker_id "
        "WHERE i.synced_at IS NULL AND i.status NOT IN ('dismissed','suggested') "
        "AND i.close_suggested=0 ORDER BY i.id").fetchall()


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


# --- live tunables (meta-backed, dashboard-editable; ADR-035) ---
# Precedence: a dashboard override (meta `cfg_<key>`) wins; else the caller's
# `default` (which already folds in any env var / module constant). Every consumer
# reads these at runtime, so changes take effect with no restart, across processes.

def cfg(conn, key, default):
    raw = meta_get(conn, "cfg_" + key)
    if raw is None:
        return default
    try:
        return type(default)(raw)        # cast to match the default's type (float/int)
    except (ValueError, TypeError):
        return default


def cfg_set(conn, key, value):
    meta_set(conn, "cfg_" + key, value)


def cfg_clear(conn, key):
    conn.execute("DELETE FROM meta WHERE key=?", ("cfg_" + key,))
    conn.commit()


def activity_count(conn, since):
    """Bell badge: ALL new conversations collapse to ONE notification; each new action item
    counts on its own. So 96 convos + 2 items = 3, not 98."""
    row = conn.execute(
        "SELECT (SELECT COUNT(*) FROM transcripts WHERE created_at > ?), "
        "       (SELECT COUNT(*) FROM intents WHERE created_at > ?)", (since, since)).fetchone()
    n_convos, n_items = row[0], row[1]
    return (1 if n_convos else 0) + n_items


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


# --- debounced profile refresh (ADR-038): mark dirty per chunk, flush periodically ---

def mark_speakers_dirty(conn, tid):
    """Flag the named, profile-able speakers in transcript `tid` for a later refresh."""
    conn.execute(
        "UPDATE speakers SET profile_dirty=1 WHERE id IN "
        "(SELECT DISTINCT speaker_id FROM segments WHERE transcript_id=? AND speaker_id IS NOT NULL) "
        "AND status='enrolled' AND name IS NOT NULL AND do_not_profile=0", (tid,))
    conn.commit()


def dirty_speaker_ids(conn):
    return [r[0] for r in conn.execute(
        "SELECT id FROM speakers WHERE profile_dirty=1").fetchall()]


def clear_profile_dirty(conn, sid):
    conn.execute("UPDATE speakers SET profile_dirty=0 WHERE id=?", (sid,))
    conn.commit()


def speaker_transcripts_since(conn, sid, since):
    """Transcript ids where `sid` spoke, newer than `since` (ISO ts) — for catch-up."""
    return [r[0] for r in conn.execute(
        "SELECT DISTINCT s.transcript_id FROM segments s JOIN transcripts t "
        "ON t.id = s.transcript_id WHERE s.speaker_id=? AND t.created_at > ? "
        "ORDER BY s.transcript_id", (sid, since or "1970-01-01")).fetchall()]


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
        "SUM(transcribed=-1) AS failed, "
        "MIN(CASE WHEN transcribed=0 THEN created_at END) AS oldest_pending FROM chunks").fetchone()
    return {"pending": row["pending"] or 0, "done": row["done"] or 0,
            "failed": row["failed"] or 0, "oldest_pending": row["oldest_pending"]}


# --- device telemetry (ADR-031) ---

def _i(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def upsert_device_status(conn, d):
    conn.execute(
        "INSERT INTO device_status(device_id, battery_mv, rssi, ssid, ip, uptime_s, "
        "  free_heap, fw, seq, updated_at) VALUES(?,?,?,?,?,?,?,?,?,datetime('now')) "
        "ON CONFLICT(device_id) DO UPDATE SET battery_mv=excluded.battery_mv, "
        "  rssi=excluded.rssi, ssid=excluded.ssid, ip=excluded.ip, uptime_s=excluded.uptime_s, "
        "  free_heap=excluded.free_heap, fw=excluded.fw, seq=excluded.seq, "
        "  updated_at=datetime('now')",
        (str(d.get("device", "device"))[:64], _i(d.get("battery_mv")), _i(d.get("rssi")),
         str(d.get("ssid", ""))[:64], str(d.get("ip", ""))[:40], _i(d.get("uptime_s")),
         _i(d.get("free_heap")), str(d.get("fw", ""))[:24], _i(d.get("seq"))))
    conn.commit()


def device_status_list(conn):
    return conn.execute("SELECT * FROM device_status ORDER BY updated_at DESC").fetchall()


# --- per-device HMAC keys + revocation (ADR-042) ---

def create_device_key(conn, device_id, label=None):
    """Issue (or rotate) a per-device key; returns the new key to flash into firmware."""
    key = secrets.token_hex(32)
    conn.execute(
        "INSERT INTO devices(device_id, key, label, active, revoked_at) VALUES(?,?,?,1,NULL) "
        "ON CONFLICT(device_id) DO UPDATE SET key=excluded.key, label=excluded.label, "
        "active=1, revoked_at=NULL", (device_id, key, label))
    conn.commit()
    return key


def device_key(conn, device_id):
    """The active key for a device, or None if unknown/revoked."""
    r = conn.execute("SELECT key FROM devices WHERE device_id=? AND active=1",
                     (device_id,)).fetchone()
    return r["key"] if r else None


def list_devices(conn):
    return conn.execute(
        "SELECT device_id, label, active, created_at, revoked_at FROM devices "
        "ORDER BY active DESC, device_id").fetchall()


def set_device_active(conn, device_id, active):
    conn.execute("UPDATE devices SET active=?, revoked_at=CASE WHEN ? THEN NULL "
                 "ELSE datetime('now') END WHERE device_id=?",
                 (1 if active else 0, 1 if active else 0, device_id))
    conn.commit()


def lipo_pct(mv):
    """Rough single-cell LiPo state-of-charge from resting voltage (mV)."""
    if not mv or mv <= 0:
        return None
    pts = [(3000, 0), (3300, 6), (3600, 20), (3700, 40), (3750, 55),
           (3850, 70), (3950, 85), (4100, 95), (4200, 100)]
    if mv <= pts[0][0]:
        return 0
    if mv >= pts[-1][0]:
        return 100
    for (v0, p0), (v1, p1) in zip(pts, pts[1:]):
        if v0 <= mv <= v1:
            return int(p0 + (p1 - p0) * (mv - v0) / (v1 - v0))
    return None


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
