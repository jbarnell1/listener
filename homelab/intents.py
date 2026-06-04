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

SYSTEM = """You extract action items from a conversation transcript.
Return ONLY JSON: {"intents":[{"action": str, "tier": "SOON"|"LATER",
"speaker": str, "due_text": str|null, "due_local": str|null, "source_quote": str,
"confidence": number}]}.

Rules:
- SOON = time-sensitive or needs action today. LATER = tomorrow or later, or informational.
- Current local time is %(now)s (%(tz)s). Resolve relative times ("tonight",
  "in 2 hours", "tomorrow", "Saturday at 6") to a concrete LOCAL datetime in
  due_local as ISO 8601 like 2026-06-03T19:00. Do NOT convert to UTC. No time → null.
- Only real action items / reminders / commitments. None → {"intents":[]}.
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


def main():
    demo = "--demo" in sys.argv
    args = [a for a in sys.argv[1:] if not a.startswith("--")]

    conn = db.connect()
    if demo:
        tid = seed_demo(conn)
    else:
        tid = int(args[0]) if args else conn.execute(
            "SELECT MAX(id) FROM transcripts").fetchone()[0]
    convo = conversation_for(tid)

    now_local = datetime.now(TZ)
    system = SYSTEM % {"now": now_local.strftime("%Y-%m-%d %H:%M (%A)"), "tz": TZ_NAME}

    print(f"--- conversation ---\n{convo}\n", flush=True)
    print(f"--- extracting with {MODEL} ---", flush=True)
    raw = ollama_chat(system, convo)
    data = json.loads(raw[raw.find("{"):raw.rfind("}") + 1])
    intents = data.get("intents", [])

    print(f"\n=== {len(intents)} intent(s) ===")
    for it in intents:
        due = to_utc(it.get("due_local"), it.get("due_text"), now_local)
        due_str = due.strftime("%Y-%m-%d %H:%M UTC") if due else "(no time)"
        local_str = due.astimezone(TZ).strftime("%a %H:%M") if due else "-"
        print(f"  [{it.get('tier')}] {it.get('action')}")
        print(f"        speaker={it.get('speaker')}  due={due_str}  (local {local_str})")
        print(f"        quote: \"{it.get('source_quote')}\"")
        sp = conn.execute("SELECT id FROM speakers WHERE name=?", (it.get("speaker"),)).fetchone()
        conn.execute(
            "INSERT INTO intents(speaker_id, action, tier, due_at, status, source_quote)"
            " VALUES (?,?,?,?, 'pending', ?)",
            (sp["id"] if sp else None, it.get("action"), it.get("tier"),
             due.isoformat() if due else None, it.get("source_quote")))
    conn.commit()
    print(f"\nstored {len(intents)} intent(s) for transcript #{tid} -> listener.db")

    # continuously enrich speaker profiles from this same transcript (ADR-023).
    try:
        import profile
        done = profile.update_for_transcript(conn, tid)
        print(f"enriched {len(done)} speaker profile(s)")
    except Exception as e:
        print(f"(profile enrichment skipped: {e})")


if __name__ == "__main__":
    main()
