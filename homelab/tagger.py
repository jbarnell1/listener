#!/usr/bin/env python3
"""Topic tagging (local LLM, via Ollama).  ADR-029.

Conversations don't have one subject — they have several, and the same snippet can
belong to many ("house hunting" AND "in-law troubles"). So each transcript gets
MULTI-LABEL topic tags. One LLM pass per snippet both (a) assigns tags — reusing
existing topics or coining new ones — and (b) returns an updated running summary for
each assigned topic, MERGING this snippet in (so a topic's summary compounds, like
profiles). Browsing/filtering by tag, and the MCP 'what did we decide about X' query,
read those.

    python tagger.py [transcript_id]   # tag one transcript (default: latest)
    python tagger.py --backfill        # tag every transcript, oldest first
"""
import json
import sys
import urllib.request

import db

OLLAMA = "http://127.0.0.1:11434/api/chat"
MODEL = "qwen3:8b"
MAX_TAGS = 5

SYSTEM = """You organize a personal audio journal by TOPIC TAGS. Given a new
conversation snippet and the EXISTING topics (name + running summary), decide which
topics this snippet belongs to. A snippet can have SEVERAL tags. Return ONLY JSON:
{"tags": [{"name": str, "summary": str}, ...]}

Rules:
- REUSE an existing topic name verbatim when the snippet fits it; only coin a NEW
  topic when nothing fits. Prefer a few durable topics over many near-duplicates.
- Topic names: short, lowercase, 1-3 words — e.g. "house hunting", "in-law troubles",
  "garden beds", "kids schedule", "finances", "date night".
- For EACH topic you assign, return an updated `summary`: a running digest of what's
  been discussed/decided on that topic, MERGING this snippet into the prior summary
  (keep earlier points, add new ones, stay concise — a few sentences). Capture
  decisions, preferences, and must-haves specifically.
- Tag only real subjects; skip pure pleasantries. 1-4 tags per snippet is typical;
  if there's truly no substantive topic, return {"tags": []}."""


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


def tag_transcript(conn, tid):
    """Assign topic tags to transcript `tid` and refresh each topic's summary."""
    convo = _convo(conn, tid)
    if not convo.strip():
        return []
    # Retrieve the most RELEVANT existing topics rather than dumping all of them
    # (ADR-038): rank by name-token overlap with this snippet, keeping recency order
    # (list_tags is recency-sorted) as a stable tiebreak; cap the window.
    low = convo.lower()

    def _overlap(t):
        return sum(1 for tok in (t["name"] or "").lower().split()
                   if len(tok) > 2 and tok in low)
    ranked = sorted(db.list_tags(conn), key=_overlap, reverse=True)[:25]
    existing = [{"name": t["name"], "summary": t["summary"]} for t in ranked]
    payload = {"existing_topics": existing, "new_snippet": convo}
    raw = _chat(SYSTEM, json.dumps(payload))
    data = json.loads(raw[raw.find("{"):raw.rfind("}") + 1])
    names = []
    for t in (data.get("tags") or [])[:MAX_TAGS]:
        name = (t.get("name") or "").strip().lower()
        if not name:
            continue
        tag_id = db.get_or_create_tag(conn, name)
        summary = (t.get("summary") or "").strip()
        if summary:
            db.set_tag_summary(conn, tag_id, summary)
        db.tag_transcript(conn, tid, tag_id)
        names.append(name)
    conn.commit()
    return names


def main():
    conn = db.connect()
    if "--backfill" in sys.argv:
        ids = [r[0] for r in conn.execute("SELECT id FROM transcripts ORDER BY id").fetchall()]
        for tid in ids:
            names = tag_transcript(conn, tid)
            print(f"#{tid}: {', '.join(names) if names else '(no tags)'}")
        return
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    tid = int(args[0]) if args else conn.execute(
        "SELECT MAX(id) FROM transcripts").fetchone()[0]
    print(f"#{tid} tagged: {', '.join(tag_transcript(conn, tid)) or '(no tags)'}")


if __name__ == "__main__":
    main()
