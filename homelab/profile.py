#!/usr/bin/env python3
"""Continuously-improving speaker profiles (local LLM, via Ollama).  ADR-023.

After each new transcript, refine what we know about each NAMED speaker: a short
summary, their relationship to you, emotional tenor, recurring topics/habits, and
durable facts. Every pass MERGES into the existing profile (never starts over), so
the dossier compounds as the person is heard more. Fully local; honors the per-
speaker opt-out (speakers.do_not_profile, Q-S5).

Usage:
    python profile.py [transcript_id]   # update everyone in that transcript (default: latest)
    python profile.py --speaker ID      # rebuild one speaker from all their transcripts
    python profile.py --backfill        # (re)build profiles for every named speaker
"""
import json
import sys
import urllib.request

import db

OLLAMA = "http://127.0.0.1:11434/api/chat"
MODEL = "qwen3:8b"
LIMITS = {"topics": 8, "recurring": 8, "facts": 15}

SYSTEM = """You maintain a concise, evolving dossier on ONE person, built from
conversation transcripts. You are given their CURRENT profile (JSON) and a NEW
conversation. Return ONLY JSON of this exact shape, MERGING new info into the old:
{"summary": str, "relationship": str|null, "emotion_trend": str,
 "topics": [str], "recurring": [str], "facts": [str]}

Rules:
- The profile is about %(name)s specifically; other speakers are context only.
- PRESERVE prior facts unless the new conversation contradicts them; ADD what's new;
  merge duplicates. If over a limit, keep the most important.
- summary: 1-2 sentences. relationship: their relationship to the device owner if it
  can be inferred (e.g. spouse, parent, coworker, friend), else null.
- emotion_trend: their recent emotional tenor in a few words.
- facts: durable, useful specifics — family/pet names, job, where they live,
  preferences, key dates. NOT one-off chatter. Be factual; never invent.
- Limits: topics<=8, recurring<=8, facts<=15."""


def _chat(system, user):
    body = json.dumps({
        "model": MODEL, "stream": False, "format": "json", "think": False,
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
        "summary": "", "emotion_trend": "", "topics": [], "recurring": [], "facts": []}
    payload = {
        "name": sp["name"],
        "current_profile": {k: cur.get(k) for k in
                            ("summary", "emotion_trend", "topics", "recurring", "facts")},
        "new_conversation": convo,
    }
    raw = _chat(SYSTEM % {"name": sp["name"]}, json.dumps(payload))
    data = json.loads(raw[raw.find("{"):raw.rfind("}") + 1])

    db.upsert_profile(
        conn, sid,
        summary=(data.get("summary") or "").strip(),
        emotion_trend=(data.get("emotion_trend") or "").strip(),
        topics=(data.get("topics") or [])[:LIMITS["topics"]],
        recurring=(data.get("recurring") or [])[:LIMITS["recurring"]],
        facts=(data.get("facts") or [])[:LIMITS["facts"]],
        last_seen=db.transcript(conn, tid)["created_at"])
    rel = (data.get("relationship") or "").strip()
    if rel and not sp["relationship"]:
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


def rebuild_speaker(conn, sid):
    """Replay every transcript a speaker appears in, in order, rebuilding their profile."""
    conn.execute("DELETE FROM profiles WHERE speaker_id=?", (sid,))
    conn.commit()
    tids = db.speaker_transcript_ids(conn, sid)
    for tid in tids:
        update_profile(conn, sid, tid)
    return len(tids)


def main():
    conn = db.connect()
    if "--backfill" in sys.argv:
        for sp in db.enrolled_speakers(conn):
            n = rebuild_speaker(conn, sp["id"])
            p = db.get_profile(conn, sp["id"])
            print(f"[{sp['name']}] rebuilt from {n} transcript(s): "
                  f"{(p or {}).get('summary', '(none)')}")
        return
    if "--speaker" in sys.argv:
        sid = int(sys.argv[sys.argv.index("--speaker") + 1])
        n = rebuild_speaker(conn, sid)
        print(f"speaker {sid}: rebuilt from {n} transcript(s)")
        print(json.dumps(db.get_profile(conn, sid), indent=2))
        return

    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    tid = int(args[0]) if args else conn.execute(
        "SELECT MAX(id) FROM transcripts").fetchone()[0]
    print(f"--- updating profiles from transcript #{tid} ({MODEL}) ---", flush=True)
    done = update_for_transcript(conn, tid)
    for sid in done:
        p = db.get_profile(conn, sid)
        sp = db.get_speaker(conn, sid)
        print(f"\n[{sp['label']}] {p['summary']}")
        if p["facts"]:
            print("  facts: " + " · ".join(p["facts"]))
    print(f"\nupdated {len(done)} profile(s).")


if __name__ == "__main__":
    main()
