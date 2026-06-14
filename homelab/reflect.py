#!/usr/bin/env python3
"""Reflection pass (ADR-043).

Inspired by Stanford Generative Agents' *reflection*: periodically synthesize the
recent low-level captures into a few HIGH-LEVEL observations the owner would actually
find useful — recurring themes, things they keep deferring, notable changes — rather
than just restating tasks. Runs scheduled + GPU-gated; surfaced on the dashboard and in
the nightly brief.

    python reflect.py        # run a reflection now and print it
"""
import json
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import db
import llm

TZ = ZoneInfo("America/Chicago")

SYSTEM = """You review a person's recent captured notes to surface a few HIGH-LEVEL
observations they'd find genuinely useful — recurring themes, things they keep putting
off, notable changes or tensions, patterns across conversations. Ground every point in
the material given; no generic life advice, and don't just restate a single task. Return
ONLY JSON {"insights": [str, ...]} with 2-5 short, specific insights, or {"insights": []}
if nothing rises above the noise."""


def _material(conn):
    items = db.list_intents(conn)
    tasks = []
    for t in items[:40]:
        bits = [t["action"], f'importance {t["importance"] or 3}']
        if t["due_at"]:
            bits.append(f'due {t["due_at"][:10]}')
        if t["owner"] and t["owner"] != "me":
            bits.append(f'for {t["owner"]}')
        tasks.append(" · ".join(bits))
    topics = [f'{t["name"]}: {t["summary"]}' for t in db.list_tags(conn) if t["summary"]][:20]
    return {"about_owner": db.meta_get(conn, "core_memory", ""),
            "recently": db.meta_get(conn, "recent_context", ""),
            "open_items": tasks, "topics": topics}


def reflect(conn):
    mat = _material(conn)
    if not mat["open_items"] and not mat["topics"]:
        return []
    data = llm.chat_json(SYSTEM, json.dumps(mat), want="insights")
    insights = [s.strip() for s in (data.get("insights") or [])
                if isinstance(s, str) and s.strip()][:5]
    db.meta_set(conn, "reflection", json.dumps(insights))
    db.meta_set(conn, "reflection_at", datetime.now(TZ).isoformat())
    return insights


def latest(conn):
    try:
        return json.loads(db.meta_get(conn, "reflection") or "[]")
    except (ValueError, TypeError):
        return []


if __name__ == "__main__":
    for s in reflect(db.connect()):
        print("•", s)
    if "--quiet" not in sys.argv:
        print("(stored in meta.reflection)")
