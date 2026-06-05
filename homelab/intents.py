#!/usr/bin/env python3
"""H4 — LLM intent extraction (local, via Ollama).  ADR-016 / ADR-017.

Turns a (speaker-attributed) transcript into structured action items:
    {action, tier(SOON|LATER), speaker, due_text, due_local, source_quote}
The model resolves *relative* times to a LOCAL datetime; CODE converts to UTC via
the IANA zone (DST automatic). due_at is stored UTC.

Usage:
    python intents.py [transcript_id]      # from listener.db, stores intents
    python intents.py --demo               # built-in sample convo (with tasks)
"""
import difflib
import json
import sys
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

import db

TZ_NAME = "America/Chicago"
TZ = ZoneInfo(TZ_NAME)
UTC = ZoneInfo("UTC")
OLLAMA = "http://127.0.0.1:11434/api/chat"
MODEL = "qwen3:8b"

SYSTEM = """You extract action items and follow-ups from a conversation transcript.
Return ONLY JSON: {"intents":[{"action": str, "kind": "event"|"task"|"followup",
"tier": "SOON"|"LATER", "speaker": str, "due_text": str|null, "due_local": str|null,
"source_quote": str, "confidence": number}]}.

kind (this decides where it goes — calendar, tasks, or the digest):
- "event"    = a scheduled happening at a specific time the person attends (dinner,
               meeting, appointment, a call at a set time). Usually has a time.
- "task"     = an actionable to-do to complete (call dentist, take out trash, buy
               milk), with or without a deadline.
- "followup" = something to revisit or think about, with NO concrete action or
               deadline (a topic raised, "we should look into X someday").

Rules:
- SOON = time-sensitive or needs action today. LATER = tomorrow or later, or informational.
- Current local time is %(now)s (%(tz)s). Resolve relative times ("tonight",
  "in 2 hours", "tomorrow", "Saturday at 6") to a concrete LOCAL datetime in
  due_local as ISO 8601 like 2026-06-03T19:00. Do NOT convert to UTC. No time → null.
- Capture real action items, commitments, and notable follow-ups. None → {"intents":[]}.
- speaker = who said it, using the transcript's speaker labels.
"""

DEMO = """Sarah: Hey, can you take out the trash tonight? Pickup is early tomorrow.
Jon: Sure, I'll do it right after dinner.
Sarah: Also don't forget dinner with my parents this Saturday at 6.
Jon: Got it. And I need to call the dentist tomorrow to reschedule my cleaning."""


def ollama_chat(system, user):
    body = json.dumps({
        "model": MODEL, "stream": False, "format": "json", "think": False,
        "options": {"temperature": 0},
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
    }).encode()
    req = urllib.request.Request(OLLAMA, body, {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.load(r)["message"]["content"]


def to_utc(due_local, due_text, now_local):
    if due_local:
        try:
            dt = datetime.fromisoformat(due_local)
            return (dt.replace(tzinfo=TZ) if dt.tzinfo is None else dt).astimezone(UTC)
        except ValueError:
            pass
    if due_text:
        import dateparser
        dt = dateparser.parse(due_text, settings={
            "RELATIVE_BASE": now_local.replace(tzinfo=None), "TIMEZONE": TZ_NAME,
            "TO_TIMEZONE": "UTC", "RETURN_AS_TIMEZONE_AWARE": True,
            "PREFER_DATES_FROM": "future"})
        if dt:
            return dt.astimezone(UTC)
    return None


def conversation_for(tid):
    rows = db.transcript_segments(db.connect(), tid)
    return "\n".join(f"{r['who']}: {r['text']}" for r in rows)


# --- de-duplication: one intent per real-world thing, across all conversations ---
SIM_THRESHOLD = 0.82


def _norm(s):
    return " ".join((s or "").lower().split())


def _sim(a, b):
    return difflib.SequenceMatcher(None, _norm(a), _norm(b)).ratio()


def _local_date(iso):
    """Local calendar day of a UTC ISO timestamp (the dedup bucket); None if no date."""
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso).astimezone(TZ).date().isoformat()
    except ValueError:
        return None


def find_duplicate(conn, action, due_iso):
    """Is there already an OPEN intent for the same thing? Same day-bucket (or both
    undated) + similar action text. Different day = distinct (e.g. weekly trash)."""
    bucket = _local_date(due_iso)
    for r in conn.execute(
            "SELECT id, action, due_at FROM intents WHERE status != 'dismissed'").fetchall():
        if _local_date(r["due_at"]) == bucket and _sim(action, r["action"]) >= SIM_THRESHOLD:
            return r["id"]
    return None


def dedupe_existing(conn, verbose=True):
    """One-time cleanup: collapse already-stored duplicates (keep oldest, dismiss rest)."""
    kept, removed = [], 0
    for r in conn.execute("SELECT id, action, due_at FROM intents "
                          "WHERE status != 'dismissed' ORDER BY id").fetchall():
        if any(_local_date(k["due_at"]) == _local_date(r["due_at"])
               and _sim(r["action"], k["action"]) >= SIM_THRESHOLD for k in kept):
            conn.execute("UPDATE intents SET status='dismissed' WHERE id=?", (r["id"],))
            removed += 1
        else:
            kept.append(r)
    conn.commit()
    if verbose:
        print(f"dedupe: dismissed {removed} duplicate(s), kept {len(kept)} open intent(s)")
    return removed


def _get_or_create_speaker(cur, name):
    r = cur.execute("SELECT id FROM speakers WHERE name=?", (name,)).fetchone()
    if r:
        return r["id"]
    cur.execute("INSERT INTO speakers(name, status) VALUES (?, 'enrolled')", (name,))
    return cur.lastrowid


def seed_demo(conn):
    """Persist the demo conversation as a real transcript + speakers + segments."""
    cur = conn.cursor()
    ids = {nm: _get_or_create_speaker(cur, nm) for nm in ("Sarah", "Jon")}
    cur.execute("INSERT INTO transcripts(audio_path, lang) VALUES ('(demo conversation)', 'en')")
    tid = cur.lastrowid
    t = 0.0
    for line in DEMO.strip().splitlines():
        who, _, text = line.partition(": ")
        cur.execute("INSERT INTO segments(transcript_id, speaker_id, t_start, t_end, text)"
                    " VALUES (?,?,?,?,?)", (tid, ids.get(who), t, t + 4, text))
        t += 4
    conn.commit()
    return tid


def run_for_transcript(conn, tid, verbose=False):
    """Extract action items from transcript `tid` (speaker-aware) and store them.
    Returns the list of intents. Used by the worker and the CLI."""
    convo = conversation_for(tid)
    now_local = datetime.now(TZ)
    system = SYSTEM % {"now": now_local.strftime("%Y-%m-%d %H:%M (%A)"), "tz": TZ_NAME}
    raw = ollama_chat(system, convo)
    data = json.loads(raw[raw.find("{"):raw.rfind("}") + 1])
    intents = data.get("intents", [])
    stored = []
    for it in intents:
        due = to_utc(it.get("due_local"), it.get("due_text"), now_local)
        due_iso = due.isoformat() if due else None
        if find_duplicate(conn, it.get("action"), due_iso):    # already tracked → skip
            if verbose:
                print(f"  (dup, skipped) {it.get('action')}")
            continue
        sp = conn.execute("SELECT id FROM speakers WHERE name=?",
                          (it.get("speaker"),)).fetchone()
        conn.execute(
            "INSERT INTO intents(speaker_id, action, kind, tier, due_at, status, source_quote)"
            " VALUES (?,?,?,?,?, 'pending', ?)",
            (sp["id"] if sp else None, it.get("action"), it.get("kind"), it.get("tier"),
             due_iso, it.get("source_quote")))
        stored.append(it)
        if verbose:
            due_str = due.strftime("%Y-%m-%d %H:%M UTC") if due else "(no time)"
            print(f"  [{it.get('kind')}/{it.get('tier')}] {it.get('action')}  "
                  f"({it.get('speaker')}, due {due_str})")
    conn.commit()
    return stored


def add_manual(conn, text):
    """Parse a hand-typed task/note through the same LLM (kind + due) so it routes to
    Calendar/Tasks like a spoken one. Returns the new intent id."""
    now_local = datetime.now(TZ)
    system = SYSTEM % {"now": now_local.strftime("%Y-%m-%d %H:%M (%A)"), "tz": TZ_NAME}
    action, kind, tier, due_iso = text, "task", "SOON", None
    try:
        raw = ollama_chat(system, f"Me: {text}")
        data = json.loads(raw[raw.find("{"):raw.rfind("}") + 1])
        items = data.get("intents") or []
        if items:
            it = items[0]
            due = to_utc(it.get("due_local"), it.get("due_text"), now_local)
            action = it.get("action") or text
            kind = it.get("kind") or "task"
            tier = it.get("tier") or "SOON"
            due_iso = due.isoformat() if due else None
    except Exception as e:  # noqa: BLE001 — fall back to a plain task
        print(f"add_manual parse failed: {e}")
    sp = db.get_self(conn)
    cur = conn.execute(
        "INSERT INTO intents(speaker_id, action, kind, tier, due_at, status, source_quote) "
        "VALUES (?,?,?,?,?, 'pending', ?)",
        (sp["id"] if sp else None, action, kind, tier, due_iso, text))
    conn.commit()
    return cur.lastrowid


def main():
    if "--dedup" in sys.argv:                  # one-time cleanup of existing duplicates
        dedupe_existing(db.connect())
        return
    demo = "--demo" in sys.argv
    args = [a for a in sys.argv[1:] if not a.startswith("--")]

    conn = db.connect()
    if demo:
        tid = seed_demo(conn)
    else:
        tid = int(args[0]) if args else conn.execute(
            "SELECT MAX(id) FROM transcripts").fetchone()[0]

    print(f"--- conversation ---\n{conversation_for(tid)}\n", flush=True)
    print(f"--- extracting with {MODEL} ---", flush=True)
    intents = run_for_transcript(conn, tid, verbose=True)
    print(f"\nstored {len(intents)} intent(s) for transcript #{tid} -> listener.db")

    try:                                      # continuously enrich profiles (ADR-023)
        import profiles
        done = profiles.update_for_transcript(conn, tid)
        print(f"enriched {len(done)} speaker profile(s)")
    except Exception as e:
        print(f"(profile enrichment skipped: {e})")


if __name__ == "__main__":
    main()
