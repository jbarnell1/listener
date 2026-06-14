#!/usr/bin/env python3
"""Continuously-improving speaker profiles (local LLM, via Ollama).  ADR-023.

Builds a true, human PROFILE of each named speaker — the kind of understanding
useful for picking gifts, planning surprises, and being a thoughtful partner/friend:
personality traits, interests, dislikes, important dates, durable facts, and a
short read on how they've been lately. NOT a list of their tasks (those live in
`intents`). Every pass MERGES into the existing profile and is deliberately
NON-DESTRUCTIVE: durable fields evolve slowly and additively, while only the
transient `recent` (mood) field is overwritten — so one grumpy day never rewrites
who someone is. Fully local; honors the per-speaker opt-out (do_not_profile).

Usage:
    python profiles.py [transcript_id]   # update everyone in that transcript (default: latest)
    python profiles.py --speaker ID      # rebuild one speaker from all their transcripts
    python profiles.py --backfill        # (re)build profiles for every named speaker
"""
import json
import sys
import urllib.request

import db

OLLAMA = "http://127.0.0.1:11434/api/chat"
MODEL = "qwen3:8b"
LIMITS = {"traits": 10, "interests": 12, "dislikes": 12, "dates": 12, "notable": 12}

SYSTEM = """You maintain a durable, human PROFILE of ONE person, %(name)s, so the
device owner can know them well — the kind of understanding useful for choosing
gifts, planning surprises, and being thoughtful. You are given their CURRENT
profile (JSON) and a NEW conversation. Return ONLY JSON of this exact shape,
MERGING the new conversation into the current profile:
{"summary": str, "relationship": str|null, "traits": [str], "interests": [str],
 "dislikes": [str], "important_dates": [{"label": str, "date": str}],
 "notable": [str], "recent": str}

What goes where:
- summary: 1-2 sentences capturing who %(name)s is as a person.
- traits: personality/temperament — e.g. "whimsical", "argumentative when stressed",
  "deeply loyal", "dry sense of humor". This is the heart of the profile.
- interests: hobbies, passions, things they love (gift-relevant).
- dislikes: things they dislike or are sensitive to (also gift-relevant).
- important_dates: PERSONAL RECURRING dates only — birthdays, anniversaries.
  {"label","date"}; date may be partial ("March 14"). NOT one-off appointments,
  meetings, or event times (those are tasks — leave them out).
- notable: durable life facts — family/pet names, job, where they live, context.
- recent: a short read on their current mood / what's going on for them lately.

CRITICAL rules:
- Do NOT include tasks, reminders, errands, chores, appointments, or to-dos anywhere
  — those are tracked separately. Capture who the person IS, not what they have to do.
- Attribute a trait/interest/dislike/fact to %(name)s ONLY when it describes
  %(name)s themselves. When %(name)s is talking about, teasing, or describing
  SOMEONE ELSE, that information belongs to that other person — do NOT copy it onto
  %(name)s. (e.g. if %(name)s says "you love gardening", gardening is NOT theirs.)
- DURABLE fields (summary, traits, interests, dislikes, important_dates, notable)
  change SLOWLY and ADDITIVELY: add well-supported items, refine wording, merge
  duplicates. NEVER drop a durable item because of a single conversation. A one-off
  bad mood is NOT a trait — only add a trait with clear or repeated evidence.
- `recent` is the ONLY transient field and is REPLACED every time. Put today's mood
  here; it must NEVER edit the durable traits.
- %(self_note)s
- Be factual; never invent. Keep lists tight (traits<=10, others<=12)."""


def _chat(system, user):
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


def _convo(conn, tid):
    rows = db.transcript_segments(conn, tid)
    return "\n".join(f"{r['who']}: {r['text']}" for r in rows)


def update_profile(conn, sid, tid):
    """Refine speaker `sid`'s profile using transcript `tid`. Returns the new
    profile dict, or None if skipped (unnamed / opted-out / empty)."""
    sp = db.get_speaker(conn, sid)
    if not sp or not sp["name"] or sp["do_not_profile"]:
        return None
    convo = _convo(conn, tid)
    if not convo.strip():
        return None

    cur = db.get_profile(conn, sid) or {
        "summary": "", "traits": [], "interests": [], "dislikes": [],
        "dates": [], "notable": []}
    is_self = bool(sp["is_self"])
    owner = db.get_self(conn)
    if is_self:
        self_note = "This person IS the device owner ('myself'); set relationship to 'self'."
    else:
        who = (owner["name"] if owner and owner["name"] else "the device owner")
        self_note = (f"relationship = their relationship to {who} (e.g. wife, friend, "
                     "coworker, parent) if it can be inferred, else null.")
    payload = {
        "name": sp["name"],
        "current_profile": {
            "summary": cur.get("summary"), "traits": cur.get("traits"),
            "interests": cur.get("interests"), "dislikes": cur.get("dislikes"),
            "important_dates": cur.get("dates"), "notable": cur.get("notable")},
        "new_conversation": convo,
    }
    raw = _chat(SYSTEM % {"name": sp["name"], "self_note": self_note}, json.dumps(payload))
    data = json.loads(raw[raw.find("{"):raw.rfind("}") + 1])

    db.upsert_profile(
        conn, sid,
        summary=(data.get("summary") or "").strip(),
        recent=(data.get("recent") or "").strip(),
        traits=(data.get("traits") or [])[:LIMITS["traits"]],
        interests=(data.get("interests") or [])[:LIMITS["interests"]],
        dislikes=(data.get("dislikes") or [])[:LIMITS["dislikes"]],
        dates=(data.get("important_dates") or [])[:LIMITS["dates"]],
        notable=(data.get("notable") or [])[:LIMITS["notable"]],
        last_seen=db.transcript(conn, tid)["created_at"])
    rel = (data.get("relationship") or "").strip()
    if rel and rel.lower() != "self" and not sp["relationship"]:
        conn.execute("UPDATE speakers SET relationship=? WHERE id=?", (rel, sid))
        conn.commit()
    return data


def update_for_transcript(conn, tid):
    """Update profiles for every named speaker appearing in transcript `tid`."""
    sids = [r["speaker_id"] for r in conn.execute(
        "SELECT DISTINCT speaker_id FROM segments "
        "WHERE transcript_id=? AND speaker_id IS NOT NULL", (tid,)).fetchall()]
    done = []
    for sid in sids:
        try:
            if update_profile(conn, sid, tid):
                done.append(sid)
        except Exception as e:                       # one bad speaker shouldn't abort the rest
            print(f"  profile update failed for speaker {sid}: {e}", flush=True)
    return done


def flush_dirty(conn, max_tx=10):
    """Refresh every speaker flagged dirty (ADR-038) — runs off the per-chunk hot path
    (scheduled, GPU-gated), processing only their transcripts since the last refresh so
    profiles get MORE context per update and fewer total LLM calls. Returns updated sids."""
    done = []
    for sid in db.dirty_speaker_ids(conn):
        try:
            prof = db.get_profile(conn, sid)
            since = prof["last_seen"] if prof else None
            tids = (db.speaker_transcripts_since(conn, sid, since) if since
                    else db.speaker_transcript_ids(conn, sid))[-max_tx:]
            for tid in tids:
                update_profile(conn, sid, tid)
            db.clear_profile_dirty(conn, sid)
            if tids:
                done.append(sid)
        except Exception as e:  # noqa: BLE001 — one bad speaker shouldn't block the rest
            print(f"  profile flush failed for speaker {sid}: {e}", flush=True)
    return done


def rebuild_speaker(conn, sid):
    """Replay every transcript a speaker appears in, in order, rebuilding their profile."""
    conn.execute("DELETE FROM profiles WHERE speaker_id=?", (sid,))
    conn.commit()
    tids = db.speaker_transcript_ids(conn, sid)
    for tid in tids:
        update_profile(conn, sid, tid)
    return len(tids)


def _print(conn, sid):
    p, sp = db.get_profile(conn, sid), db.get_speaker(conn, sid)
    if not p:
        print(f"[{sp['label']}] (no profile)")
        return
    print(f"\n[{sp['label']}] {p['summary']}")
    for k in ("traits", "interests", "dislikes", "notable"):
        if p[k]:
            print(f"  {k}: " + ", ".join(str(x) for x in p[k]))
    if p["dates"]:
        print("  dates: " + ", ".join(f"{d.get('label')}={d.get('date')}" for d in p["dates"]))
    if p["recent"]:
        print(f"  recent: {p['recent']}")


def main():
    conn = db.connect()
    if "--backfill" in sys.argv:
        for sp in db.enrolled_speakers(conn):
            n = rebuild_speaker(conn, sp["id"])
            print(f"rebuilt {sp['name']} from {n} transcript(s)")
            _print(conn, sp["id"])
        return
    if "--speaker" in sys.argv:
        sid = int(sys.argv[sys.argv.index("--speaker") + 1])
        n = rebuild_speaker(conn, sid)
        print(f"speaker {sid}: rebuilt from {n} transcript(s)")
        _print(conn, sid)
        return

    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    tid = int(args[0]) if args else conn.execute(
        "SELECT MAX(id) FROM transcripts").fetchone()[0]
    print(f"--- updating profiles from transcript #{tid} ({MODEL}) ---", flush=True)
    for sid in update_for_transcript(conn, tid):
        _print(conn, sid)


if __name__ == "__main__":
    main()
