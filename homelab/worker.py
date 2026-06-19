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
import google_sync
import gpu_gate
import intents
import profiles
import tagger
import wordattribute

MODEL = os.environ.get("LISTENER_ASR_MODEL", "large-v3")
POLL_SECS = int(os.environ.get("LISTENER_WORKER_POLL", "5"))      # idle queue poll
DEFER_SECS = int(os.environ.get("LISTENER_WORKER_DEFER", "90"))   # wait when GPU busy (retry fast)
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


def process_audio_file(audio, conn=None, chunk_id=None, marked=False):
    """Full pipeline for one normalized wav. Returns the transcript id."""
    conn = conn or db.connect()
    tid = wordattribute.process_audio(audio, MODEL, chunk_id=chunk_id)
    has_text = conn.execute("SELECT 1 FROM segments WHERE transcript_id=? "
                            "AND length(trim(text))>0 LIMIT 1", (tid,)).fetchone()
    if not has_text:                              # no speech — skip LLM intents/tagging (ADR-048)
        return tid
    intents.reconcile_for_transcript(conn, tid)   # close out items this convo resolved (ADR-032)
    intents.run_for_transcript(conn, tid, marked=marked)  # marked = deliberate capture (ADR-038)
    intents.update_recent_context(conn, tid)      # rolling context for the next chunk (ADR-038)
    db.mark_speakers_dirty(conn, tid)             # profiles flushed off the hot path (ADR-038)
    tagger.tag_transcript(conn, tid)              # topic tags (ADR-029)
    try:                                  # push events/tasks to Google (ADR-026)
        google_sync.sync_pending(conn)
    except Exception as e:  # noqa: BLE001 — Google issues must not fail the chunk
        print(f"worker: google sync skipped: {e}", flush=True)
    return tid


def process_chunk(conn, chunk):
    cid = chunk["id"]
    _status("processing", f"chunk #{cid}", chunk=cid)
    wav = normalize(chunk["path"])
    tid = process_audio_file(wav, conn, chunk_id=cid, marked=bool(chunk["marked"]))
    db.mark_chunk_done(conn, cid)
    _status("idle", f"done chunk #{cid} → transcript #{tid}", last_tid=tid)


IDLE_UNLOAD_SECS = int(os.environ.get("LISTENER_MODEL_IDLE", "180"))  # free warm models after idle


WARM_RETRY_SECS = 6          # brief GPU blip while warm — wait a moment, stay loaded
WARM_DEFERS_TO_UNLOAD = 3    # only unload after this many consecutive defers (sustained contention)


def loop():
    conn = db.connect()
    _status("idle", "started")
    warm = False
    warm_defers = 0
    last_active = time.time()
    while True:
        chunk = db.next_pending_chunk(conn, MAX_ATTEMPTS)
        if not chunk:
            if warm and time.time() - last_active > IDLE_UNLOAD_SECS:
                wordattribute.shutdown_servers()      # free VRAM for the desktop GPU (ADR-049)
                warm = False
                _status("idle", "models unloaded (idle)")
            time.sleep(POLL_SECS)
            continue
        clear, why = gpu_gate.status(assume_loaded=warm)
        if not clear:
            if warm and warm_defers < WARM_DEFERS_TO_UNLOAD:
                # brief blip (often residual util from our own last chunk) — stay warm,
                # retry quickly so we don't thrash model loads (ADR-050).
                warm_defers += 1
                _status("deferred", f"GPU blip — {why}; staying warm",
                        pending=db.queue_stats(conn)["pending"])
                time.sleep(WARM_RETRY_SECS)
                continue
            if warm:                                  # sustained contention → free VRAM, back off
                wordattribute.shutdown_servers()
                warm = False
            warm_defers = 0
            wait_txt = f"{DEFER_SECS//60} min" if DEFER_SECS >= 60 else f"{DEFER_SECS}s"
            _status("deferred", f"GPU busy — {why}; retry in {wait_txt}",
                    pending=db.queue_stats(conn)["pending"])
            time.sleep(DEFER_SECS)
            continue
        try:
            process_chunk(conn, chunk)
            warm = True
            warm_defers = 0
            last_active = time.time()
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
