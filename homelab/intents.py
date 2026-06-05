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

# Confidence triage (ADR-033): a new item below TRIAGE_THRESHOLD is "suggested"
# (held in the dashboard Review queue) instead of auto-pushed to Google. The
# reconciler (ADR-032) must be at least CLOSE_THRESHOLD sure before it acts.
TRIAGE_THRESHOLD = 0.75
CLOSE_THRESHOLD = 0.70

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
- confidence (0-1) = how sure you are this is a REAL, intended commitment or plan
  (not a casual hypothetical, joke, or passing aside). A firm explicit task/plan is
  ~0.9+; "we should maybe sometime…" is ~0.4. Be calibrated — this gates whether it
  is auto-added to the calendar or held for the person to confirm.
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


def find_duplicate(conn, action, due_iso, threshold=None):
    """Is there already an OPEN intent for the same thing? Same day-bucket (or both
    undated) + similar action text. Different day = distinct (e.g. weekly trash)."""
    if threshold is None:
        threshold = db.cfg(conn, "dedupe_similarity", SIM_THRESHOLD)
    bucket = _local_date(due_iso)
    for r in conn.execute(
            "SELECT id, action, due_at FROM intents WHERE status != 'dismissed'").fetchall():
        if _local_date(r["due_at"]) == bucket and _sim(action, r["action"]) >= threshold:
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
    triage_thr = db.cfg(conn, "triage_threshold", TRIAGE_THRESHOLD)
    sim_thr = db.cfg(conn, "dedupe_similarity", SIM_THRESHOLD)
    stored = []
    for it in intents:
        due = to_utc(it.get("due_local"), it.get("due_text"), now_local)
        due_iso = due.isoformat() if due else None
        if find_duplicate(conn, it.get("action"), due_iso, sim_thr):  # already tracked → skip
            if verbose:
                print(f"  (dup, skipped) {it.get('action')}")
            continue
        sp = conn.execute("SELECT id FROM speakers WHERE name=?",
                          (it.get("speaker"),)).fetchone()
        try:
            conf = float(it.get("confidence"))
        except (TypeError, ValueError):
            conf = None
        kind = (it.get("kind") or "task").lower()
        # Triage (ADR-033): followups only ever hit the digest (low stakes), so they
        # flow straight through. Everything else is gated — uncertain items are held
        # as "suggested" for the Review queue instead of auto-pushed to Google.
        status = ("pending" if kind == "followup" or conf is None or conf >= triage_thr
                  else "suggested")
        conn.execute(
            "INSERT INTO intents(speaker_id, action, kind, tier, due_at, status, "
            "source_quote, confidence) VALUES (?,?,?,?,?,?,?,?)",
            (sp["id"] if sp else None, it.get("action"), kind, it.get("tier"),
             due_iso, status, it.get("source_quote"), conf))
        stored.append(it)
        if verbose:
            due_str = due.strftime("%Y-%m-%d %H:%M UTC") if due else "(no time)"
            print(f"  [{it.get('kind')}/{it.get('tier')}] {it.get('action')}  "
                  f"({it.get('speaker')}, due {due_str})")
    conn.commit()
    return stored


RECONCILE_SYSTEM = """You decide whether a new conversation RESOLVES any of a
person's currently-open action items (marks them done or cancelled).

You are given (1) a numbered list of their open tasks/events and (2) a recent
conversation. An item is resolved ONLY if the conversation clearly indicates it
ALREADY HAPPENED or was CALLED OFF — not if it is merely mentioned, planned, or
restated.

Return ONLY JSON: {"resolved":[{"id": int, "resolution": "completed"|"cancelled",
"evidence": str, "confidence": number}]}.
- id MUST be one of the listed item ids.
- "completed" = it was done / it occurred ("I called the dentist", "we already had
  dinner with your parents", "trash is out").
- "cancelled" = called off / no longer happening ("we cancelled Saturday", "pickup
  got skipped this week").
- evidence = the short quote that shows it.
- confidence (0-1) = how sure you are. Be conservative: when it is only being
  re-mentioned, planned, or you are unsure, leave it OUT.
- Nothing resolved → {"resolved":[]}."""


def reconcile_for_transcript(conn, tid, verbose=False):
    """Close out open items a new conversation resolves (ADR-032). Tasks/followups
    auto-close (and their Google item is deleted); events are flagged for the user
    to confirm before anything is removed. Returns the list acted on."""
    open_items = db.open_intents_for_reconcile(conn)
    if not open_items:
        return []
    listing = "\n".join(
        f'{r["id"]}. [{(r["kind"] or "task")}] {r["action"]}'
        + (f' (due {r["due_at"]})' if r["due_at"] else "")
        for r in open_items)
    convo = conversation_for(tid)
    try:
        raw = ollama_chat(RECONCILE_SYSTEM, f"OPEN ITEMS:\n{listing}\n\nNEW CONVERSATION:\n{convo}")
        data = json.loads(raw[raw.find("{"):raw.rfind("}") + 1])
    except Exception as e:  # noqa: BLE001 — reconciliation must never fail the chunk
        if verbose:
            print(f"reconcile: skipped ({e})")
        return []

    import google_sync
    close_thr = db.cfg(conn, "close_threshold", CLOSE_THRESHOLD)
    by_id = {r["id"]: r for r in open_items}
    acted = []
    for res in data.get("resolved", []):
        item = by_id.get(res.get("id"))
        if not item:
            continue
        try:
            conf = float(res.get("confidence"))
        except (TypeError, ValueError):
            conf = 0.0
        resolution = (res.get("resolution") or "completed").lower()
        if conf < close_thr or resolution not in ("completed", "cancelled"):
            continue
        iid, kind = item["id"], (item["kind"] or "task").lower()
        note = (res.get("evidence") or "")[:300]
        if kind == "event":                       # high-stakes → confirm before deleting
            db.suggest_close(conn, iid, resolution, note)
        else:                                     # task/followup → auto-close + delete
            try:
                google_sync.remove_intent(conn, iid)
            except Exception as e:  # noqa: BLE001
                print(f"reconcile: google remove failed for {iid}: {e}")
            db.close_intent(conn, iid, kind=resolution, note=note)
        acted.append({"id": iid, "kind": kind, "resolution": resolution, "action": item["action"]})
        if verbose:
            tag = f"{resolution}?" if kind == "event" else resolution
            print(f"  reconcile [{kind}] #{iid} {tag} ({conf:.2f}) — {item['action']}")
    return acted


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
        "INSERT INTO intents(speaker_id, action, kind, tier, due_at, status, "
        "source_quote, confidence) VALUES (?,?,?,?,?, 'pending', ?, 1.0)",
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
    print(f"--- reconciling open items with {MODEL} ---", flush=True)
    closed = reconcile_for_transcript(conn, tid, verbose=True)
    print(f"reconciled {len(closed)} open item(s)")
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
