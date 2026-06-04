#!/usr/bin/env python3
"""Tool functions for the MCP server + page assistant (ADR-020).

Everything a user can do on the dashboard, as plain functions returning
JSON-serializable results. Shared by mcp_server.py (exposes them over MCP) and
the in-process fallback. Pure DB ops — no raw SQL exposed to the model.
"""
import db


def list_speakers() -> list:
    """List every speaker (known and unknown) with id, name, status, segment count."""
    return [{"id": r["id"], "name": r["name"], "label": r["label"],
             "status": r["status"], "segments": r["n_segments"]}
            for r in db.list_speakers(db.connect())]


def get_speaker(speaker_id: int) -> dict:
    """Get one speaker's profile and their open tasks, by id."""
    c = db.connect()
    sp = db.get_speaker(c, speaker_id)
    if not sp:
        return {"error": f"no speaker with id {speaker_id}"}
    return {"id": sp["id"], "name": sp["name"], "label": sp["label"],
            "status": sp["status"], "relationship": sp["relationship"],
            "tasks": [{"id": t["id"], "action": t["action"], "tier": t["tier"],
                       "due_at": t["due_at"]} for t in db.speaker_intents(c, speaker_id)]}


def rename_speaker(speaker_id: int, name: str) -> dict:
    """Set a speaker's name (turns an unknown voice into a recognized person)."""
    db.rename_speaker(db.connect(), speaker_id, name)
    return {"ok": True, "message": f"speaker {speaker_id} is now named '{name}'"}


def merge_speakers(source_id: int, target_id: int) -> dict:
    """Merge source speaker into target: improves target's voiceprint and moves
    source's segments/tasks to target. Use when an unknown is actually a known person."""
    db.merge_speakers(db.connect(), source_id, target_id)
    return {"ok": True, "message": f"merged speaker {source_id} into {target_id}"}


def list_unknown_speakers() -> list:
    """List speakers that haven't been identified/named yet."""
    return [{"id": r["id"], "label": r["label"], "segments": r["n_segments"]}
            for r in db.unknown_speakers(db.connect())]


def list_tasks(tier: str = "") -> list:
    """List open (not dismissed) tasks. tier is optional: 'SOON' or 'LATER'."""
    rows = db.list_intents(db.connect(), tier or None)
    return [{"id": r["id"], "action": r["action"], "tier": r["tier"],
             "due_at": r["due_at"], "who": r["who"], "speaker_id": r["speaker_id"]}
            for r in rows]


def dismiss_task(task_id: int) -> dict:
    """Dismiss (mark done/cancel) a task by id."""
    db.dismiss_intent(db.connect(), task_id)
    return {"ok": True, "message": f"task {task_id} dismissed"}


def list_transcripts() -> list:
    """List recent transcripts (id, source, segment count, time)."""
    return [{"id": r["id"], "source": r["audio_path"], "segments": r["n_segments"],
             "created_at": r["created_at"]} for r in db.recent_transcripts(db.connect())]


def get_transcript(transcript_id: int) -> dict:
    """Get a transcript's speaker-attributed lines, by id."""
    segs = db.transcript_segments(db.connect(), transcript_id)
    return {"id": transcript_id,
            "lines": [{"who": s["who"], "text": s["text"]} for s in segs]}


# registry (order = how they're registered with the MCP server)
TOOLS = [list_speakers, get_speaker, rename_speaker, merge_speakers,
         list_unknown_speakers, list_tasks, dismiss_task, list_transcripts, get_transcript]
