#!/usr/bin/env python3
"""Shared local-LLM access (Ollama) with validate + retry (ADR-043).

One place for: the hot-swappable pipeline model (cfg `llm_model`, ADR-040), JSON-mode
chat, and — the point of this module — **schema-key validation with a single retry**.
A small 8B model occasionally emits malformed or partial JSON; rather than silently
dropping a whole chunk's extraction, we re-ask once ("valid JSON only") and, if it still
fails, return {} so the caller degrades gracefully. (Hermes-style structured-output
discipline; issue #29.)
"""
import json
import urllib.request

import db

OLLAMA = "http://127.0.0.1:11434/api/chat"
DEFAULT_MODEL = "qwen3:8b"


def model(conn=None):
    return db.cfg(conn or db.connect(), "llm_model", DEFAULT_MODEL)


def chat(system, user, *, fmt="json", think=False, temperature=0, timeout=180):
    body = json.dumps({
        "model": model(), "stream": False, "format": fmt, "think": think,
        "options": {"temperature": temperature},
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
    }).encode()
    req = urllib.request.Request(OLLAMA, body, {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)["message"]["content"]


def _parse(raw):
    try:
        return json.loads(raw[raw.find("{"):raw.rfind("}") + 1])
    except (ValueError, TypeError):
        return None


def chat_json(system, user, *, want=None, retries=1):
    """Chat expecting a JSON object; validate (optional required top-level key `want`)
    and retry once with a firmer instruction. Returns the parsed dict, or {} if the
    model never produced valid JSON — so a bad response degrades, never crashes."""
    sys_prompt = system
    for _ in range(retries + 1):
        data = _parse(chat(sys_prompt, user))
        if isinstance(data, dict) and (want is None or want in data):
            return data
        sys_prompt = (system + "\n\nIMPORTANT: reply with ONLY valid minified JSON exactly "
                      "matching the schema above — no prose, no markdown.")
    return {}
