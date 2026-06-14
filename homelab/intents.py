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
import re
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

# Spoken "remember this" markers — the lowest-friction explicit capture (ADR-038).
# When the owner deliberately flags something, trust it: high confidence, no triage.
EXPLICIT_MARKERS = re.compile(
    r"\b(remind me|note to self|don'?t (?:let me )?forget|remember to|make sure (?:i|to)|"
    r"add (?:this|that|it) to my list|put (?:this|that|it) on my list)\b", re.I)

SYSTEM = """You extract action items and follow-ups from a conversation transcript.
Return ONLY JSON: {"intents":[{"action": str, "kind": "event"|"task"|"followup",
"tier": "SOON"|"LATER", "speaker": str, "owner": str, "due_text": str|null,
"due_local": str|null, "recurrence": str|null, "source_quote": str, "confidence": number}]}.

Use the CONTEXT block in the user message (the people, what's already on the list, and
what's been happening lately) to resolve who's who, whose task it is, and vague references.

kind (decides where it goes — calendar, tasks, or the digest):
- "event"    = a scheduled happening at a specific time the owner attends (dinner,
               meeting, appointment, a call at a set time). Usually has a time.
- "task"     = an actionable to-do to complete (call dentist, take out trash, buy milk),
               with or without a deadline.
- "followup" = something to revisit with NO concrete action or deadline.

Everyday speech rarely sounds like a task — capture the INTENT behind it:
- "we're out of coffee" → task "buy coffee"; "I should call mom" → "call mom";
  "don't let me forget the dentist tomorrow" → "call the dentist".
- Skip pure chatter, opinions, and hypotheticals ("if it rains we'll cancel"), and
  anything negated or already done ("never mind, I already did it").

owner = WHO will do it (NOT who said it):
- Default "me" (the device owner) for the owner's own commitments and things asked OF
  them ("I'll do X" → "me"; "can you do X?" asked of the owner → "me").
- A task that clearly belongs to someone else → that person's name (use CONTEXT people).

Rules:
- SOON = time-sensitive or needs action today. LATER = tomorrow+ or informational.
- Current local time is %(now)s (%(tz)s). Resolve relative times ("tonight", "tomorrow",
  "Saturday at 6") to a concrete LOCAL datetime in due_local as ISO 8601 like
  2026-06-03T19:00. Do NOT convert to UTC. No time → null.
- recurrence: if it repeats, set "daily", "weekly:DD" (DD ∈ MO TU WE TH FR SA SU),
  "biweekly:DD", or "monthly" ("trash every Tuesday" → "weekly:TU"). Otherwise null.
- action must be a SELF-CONTAINED title. Resolve "the thing"/"that"/"it" from CONTEXT;
  if you genuinely cannot tell what it refers to, keep confidence low.
- speaker = who said it, using the transcript's labels.
- confidence (0-1) = how sure this is a REAL, intended commitment/plan (not a casual
  hypothetical, joke, or aside). Firm explicit task/plan ~0.9+; "maybe someday" ~0.4.
  This gates whether it is auto-added or held for the person to confirm.
- Don't recreate something already on the list (see CONTEXT). None → {"intents":[]}.
"""

DEMO = """Sarah: Hey, can you take out the trash tonight? Pickup is early tomorrow.
Jon: Sure, I'll do it right after dinner.
Sarah: Also don't forget dinner with my parents this Saturday at 6.
Jon: Got it. And I need to call the dentist tomorrow to reschedule my cleaning."""


def ollama_chat(system, user):
    body = json.dumps({
        "model": db.cfg(db.connect(), "llm_model", MODEL),    # hot-swappable (ADR-040)
        "stream": False, "format": "json", "think": False,
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


def _due_label(iso):
    if not iso:
        return ""
    try:
        return datetime.fromisoformat(iso).astimezone(TZ).strftime("%a %b %-d")
    except ValueError:
        return ""


CTX_MAX_TASKS = 15            # bound the open-task list injected into context (ADR-041)


def context_preamble(conn, convo=""):
    """Compact 'world snapshot' injected into extraction so the small model resolves
    people, task ownership, vague references, and dups by RETRIEVAL not guesswork
    (ADR-038): who's 'me' and how others relate, what's already on the list, and a
    rolling note of what's been happening lately. The open-task list is RELEVANCE-RANKED
    against this chunk and capped (ADR-041) so the context never becomes the noise."""
    parts = []
    roster = db.speaker_roster(conn)
    if roster:
        ppl = []
        for r in roster:
            if r["is_self"]:
                ppl.append(f'{r["name"]} = the device owner ("me"/"I")')
            elif r["relationship"]:
                ppl.append(f'{r["name"]} = {r["relationship"]}')
            else:
                ppl.append(r["name"])
        parts.append("PEOPLE (resolve speaker labels + task ownership with these): "
                     + "; ".join(ppl))
    open_tasks = db.list_intents(conn)
    if open_tasks:
        low = (convo or "").lower()

        def _rel(t):                                  # tasks this chunk likely refers to, first
            return sum(1 for tok in (t["action"] or "").lower().split()
                       if len(tok) > 3 and tok in low)
        ranked = sorted(open_tasks, key=_rel, reverse=True)[:CTX_MAX_TASKS]
        rows = [f'- {t["action"]}' + (f" ({_due_label(t['due_at'])})" if t["due_at"] else "")
                for t in ranked]
        more = len(open_tasks) - len(ranked)
        if more > 0:
            rows.append(f"- (+{more} more not shown)")
        parts.append("ALREADY ON THE LIST (do NOT recreate these):\n" + "\n".join(rows))
    recent = db.meta_get(conn, "recent_context")
    if recent:
        parts.append("RECENTLY (for resolving 'that thing' / 'the usual'): " + recent)
    return "\n\n".join(parts)


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


def run_for_transcript(conn, tid, verbose=False, marked=False):
    """Extract action items from transcript `tid` (speaker-aware) and store them.
    `marked` = the device REC/"remember" button was pressed for this chunk, so its
    items are deliberate captures (high confidence, never triaged). ADR-038.
    Returns the list of intents. Used by the worker and the CLI."""
    convo = conversation_for(tid)
    now_local = datetime.now(TZ)
    system = SYSTEM % {"now": now_local.strftime("%Y-%m-%d %H:%M (%A)"), "tz": TZ_NAME}
    preamble = context_preamble(conn, convo)
    user = (f"CONTEXT:\n{preamble}\n\n" if preamble else "") + f"CONVERSATION:\n{convo}"
    raw = ollama_chat(system, user)
    data = json.loads(raw[raw.find("{"):raw.rfind("}") + 1])
    intents = data.get("intents", [])
    triage_thr = db.cfg(conn, "triage_threshold", TRIAGE_THRESHOLD)
    sim_thr = db.cfg(conn, "dedupe_similarity", SIM_THRESHOLD)
    self_sp = db.get_self(conn)                       # normalize the owner's own name -> "me"
    self_names = {"me", "myself", "i", "owner"}
    if self_sp and self_sp["name"]:
        self_names.add(self_sp["name"].lower())
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
        owner = (it.get("owner") or "me").strip() or "me"
        if owner.lower() in self_names:
            owner = "me"
        recurrence = (it.get("recurrence") or "").strip().lower() or None
        if recurrence in ("none", "null", "no", "false"):
            recurrence = None
        # Explicit "remember this" capture (ADR-038): the owner deliberately flagged it
        # ("remind me…", "note to self…"), so trust it — high confidence, never triaged.
        flagged = marked or bool(EXPLICIT_MARKERS.search(
            f'{it.get("source_quote") or ""} {it.get("action") or ""}'))
        if flagged and (conf is None or conf < 0.95):
            conf = 0.95
        # Triage (ADR-033): followups only ever hit the digest (low stakes), so they
        # flow straight through. Everything else is gated — uncertain items are held
        # as "suggested" for the Review queue instead of auto-pushed to Google.
        status = ("pending" if kind == "followup" or conf is None or conf >= triage_thr
                  else "suggested")
        conn.execute(
            "INSERT INTO intents(speaker_id, transcript_id, action, kind, owner, recurrence, "
            "tier, due_at, status, source_quote, confidence) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (sp["id"] if sp else None, tid, it.get("action"), kind, owner, recurrence,
             it.get("tier"), due_iso, status, it.get("source_quote"), conf))
        stored.append(it)
        if verbose:
            due_str = due.strftime("%Y-%m-%d %H:%M UTC") if due else "(no time)"
            print(f"  [{it.get('kind')}/{it.get('tier')}] {it.get('action')}  "
                  f"({it.get('speaker')}, due {due_str})")
    conn.commit()
    return stored


RECENT_SYSTEM = """You keep a SHORT running note of what's going on in the owner's
day so later snippets can resolve references like "that", "the thing", "the usual".
Given the PRIOR note and a NEW snippet, return ONLY JSON {"recent": str}: a terse note
(<= ~70 words) merging the new snippet in — people, plans, decisions, open threads.
Drop stale detail. This is CONTEXT, not a task list."""

RECENT_MAX_GAP_S = 4 * 3600     # quiet this long → start a fresh "session" note


def update_recent_context(conn, tid):
    """Maintain a small rolling 'what's been happening' note in meta (ADR-038),
    fed into the next chunk's extraction context. Session-decays after a quiet gap."""
    convo = conversation_for(tid)
    if not convo.strip():
        return
    prior = db.meta_get(conn, "recent_context", "")
    last = db.meta_get(conn, "recent_context_at")
    if last:
        try:
            if (datetime.now(UTC) - datetime.fromisoformat(last)).total_seconds() > RECENT_MAX_GAP_S:
                prior = ""                       # new session — don't carry stale context
        except ValueError:
            pass
    try:
        raw = ollama_chat(RECENT_SYSTEM, json.dumps({"prior_note": prior, "new_snippet": convo}))
        note = (json.loads(raw[raw.find("{"):raw.rfind("}") + 1]).get("recent") or "").strip()[:600]
    except Exception as e:  # noqa: BLE001 — context is best-effort, never fail the chunk
        print(f"recent-context update skipped: {e}", flush=True)
        return
    if note:
        db.meta_set(conn, "recent_context", note)
        db.meta_set(conn, "recent_context_at", datetime.now(UTC).isoformat())


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
    action, kind, tier, due_iso, recurrence = text, "task", "SOON", None, None
    try:
        raw = ollama_chat(system, f"CONVERSATION:\nMe: {text}")
        data = json.loads(raw[raw.find("{"):raw.rfind("}") + 1])
        items = data.get("intents") or []
        if items:
            it = items[0]
            due = to_utc(it.get("due_local"), it.get("due_text"), now_local)
            action = it.get("action") or text
            kind = it.get("kind") or "task"
            tier = it.get("tier") or "SOON"
            due_iso = due.isoformat() if due else None
            recurrence = (it.get("recurrence") or "").strip().lower() or None
            if recurrence in ("none", "null", "no", "false"):
                recurrence = None
    except Exception as e:  # noqa: BLE001 — fall back to a plain task
        print(f"add_manual parse failed: {e}")
    sp = db.get_self(conn)
    cur = conn.execute(
        "INSERT INTO intents(speaker_id, action, kind, owner, recurrence, tier, due_at, "
        "status, source_quote, confidence) VALUES (?,?,?,'me',?,?,?, 'pending', ?, 1.0)",
        (sp["id"] if sp else None, action, kind, recurrence, tier, due_iso, text))
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
