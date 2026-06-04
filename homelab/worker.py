#!/usr/bin/env python3
"""Pipeline worker (ADR-015/ADR-025) — drains the ingest queue, end-to-end.

The ESP32 uploads to /ingest, which stores a chunk (transcribed=0) and ACKs
instantly. This worker watches that queue and, whenever the **GPU gate** is clear,
runs the full chain on each chunk:

    chunk → ffmpeg-normalize (16k mono) → wordattribute (WhisperX + diarize + ID)
          → intents (LLM)  → profiles (LLM)  → mark done

It runs in the light `listener-web` venv and shells out to the heavy CUDA venvs
(as wordattribute.py already does). One chunk at a time (the GPU can't parallelise
these). Backlog self-heals: chunks persist until processed, so a PC that was off or
gaming just drains the queue on its next clear window.

    python worker.py              # run the daemon
    python worker.py --once FILE   # process one local audio file (manual test)
"""
import json
import os
import subprocess
import sys
import time

import db
import gpu_gate
import intents
import profiles
import wordattribute

MODEL = os.environ.get("LISTENER_ASR_MODEL", "large-v3")
POLL_SECS = int(os.environ.get("LISTENER_WORKER_POLL", "5"))      # idle queue poll
DEFER_SECS = int(os.environ.get("LISTENER_WORKER_DEFER", "900"))  # wait when GPU busy (15 min)
MAX_ATTEMPTS = 3
STATUS_FILE = "/tmp/listener-worker.json"


def _status(state, detail="", **extra):
    try:
        with open(STATUS_FILE, "w") as f:
            json.dump({"state": state, "detail": detail, "model": MODEL,
                       "ts": int(time.time()), **extra}, f)
    except OSError:
        pass
    print(f"worker: {state} {detail}".rstrip(), flush=True)


def normalize(src):
    """Decode any ingested audio (wav/opus/…) to a persistent 16k mono wav that
    every stage (WhisperX, pyannote) can read; returns the wav path."""
    out = os.path.splitext(src)[0] + ".16k.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", src,
                    "-ac", "1", "-ar", "16000", out], check=True)
    return out


def process_audio_file(audio, conn=None, chunk_id=None):
    """Full pipeline for one normalized wav. Returns the transcript id."""
    conn = conn or db.connect()
    tid = wordattribute.process_audio(audio, MODEL, chunk_id=chunk_id)
    intents.run_for_transcript(conn, tid)
    profiles.update_for_transcript(conn, tid)
    return tid


def process_chunk(conn, chunk):
    cid = chunk["id"]
    _status("processing", f"chunk #{cid}", chunk=cid)
    wav = normalize(chunk["path"])
    tid = process_audio_file(wav, conn, chunk_id=cid)
    db.mark_chunk_done(conn, cid)
    _status("idle", f"done chunk #{cid} → transcript #{tid}", last_tid=tid)


def loop():
    conn = db.connect()
    _status("idle", "started")
    while True:
        chunk = db.next_pending_chunk(conn, MAX_ATTEMPTS)
        if not chunk:
            time.sleep(POLL_SECS)
            continue
        clear, why = gpu_gate.status()
        if not clear:
            _status("deferred", f"GPU busy — {why}; retry in {DEFER_SECS//60} min")
            time.sleep(DEFER_SECS)
            continue
        try:
            process_chunk(conn, chunk)
        except Exception as e:  # noqa: BLE001 — one bad chunk must not wedge the queue
            db.mark_chunk_error(conn, chunk["id"], e, MAX_ATTEMPTS)
            _status("idle", f"chunk #{chunk['id']} failed: {e}")
            time.sleep(2)


def main():
    db.init_db()
    if "--once" in sys.argv:
        rest = [a for a in sys.argv[1:] if a != "--once"]
        src = rest[0] if rest else "samples/two.wav"
        print(f"worker --once: {src} (model {MODEL})", flush=True)
        tid = process_audio_file(normalize(src))
        print(f"done → transcript #{tid}", flush=True)
        return
    loop()


if __name__ == "__main__":
    main()
