#!/usr/bin/env python3
"""Word-level speaker-attributed transcript (replaces segment-level attribute.py).

WhisperX forced-aligned WORDS (cu128 venv) + our diarization+ID turns (cu13 venv),
assigned per-word so segments split exactly at speaker changes. Persists to SQLite.

    python wordattribute.py [audio] [whisper_model]
"""
import json
import os
import subprocess
import sys
import threading

import db

HOME = os.path.expanduser("~")
WX_PY = f"{HOME}/listener-wx/bin/python"      # WhisperX (transcribe + align)
DIAR_PY = f"{HOME}/listener-diar/bin/python"  # pyannote diarize + ECAPA identify
HERE = os.path.dirname(os.path.abspath(__file__))

# --- persistent model servers (ADR-049): load models once, feed many chunks ---
# Each heavy stage runs as a long-lived subprocess with its model warm in VRAM, instead
# of reloading ~3-6 GB of models on every chunk. The worker calls shutdown_servers() after
# an idle stretch to free the shared GPU.
RESULT = "\x02"                               # sentinel marking the JSON result line
_servers = {}                                 # key -> Popen
_srv_lock = threading.Lock()


def _kill(key):
    p = _servers.pop(key, None)
    if not p:
        return
    for fn in (lambda: p.stdin and p.stdin.close(), p.terminate):
        try:
            fn()
        except Exception:  # noqa: BLE001
            pass
    try:
        p.wait(timeout=5)
    except Exception:  # noqa: BLE001
        try:
            p.kill()
        except Exception:  # noqa: BLE001
            pass


def _ensure(key, python, script):
    p = _servers.get(key)
    if p is not None and p.poll() is None:
        return p
    p = subprocess.Popen([python, os.path.join(HERE, script), "--serve"],
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         text=True, cwd=HERE, bufsize=1)   # stderr inherits -> worker log
    _servers[key] = p
    return p


def _server_request(key, python, script, req, timeout):
    """Send one JSON request to the warm server and read its result line. If it hangs
    past `timeout` (a degenerate chunk), kill the server and raise — so one bad chunk
    can't wedge the queue. Next call restarts it (one model reload)."""
    with _srv_lock:
        p = _ensure(key, python, script)
        try:
            p.stdin.write(json.dumps(req) + "\n"); p.stdin.flush()
        except (BrokenPipeError, OSError):
            _kill(key); p = _ensure(key, python, script)
            p.stdin.write(json.dumps(req) + "\n"); p.stdin.flush()
        box = {}

        def reader():
            for line in p.stdout:                 # ignore any non-result chatter on stdout
                if line.startswith(RESULT):
                    box["data"] = line[len(RESULT):].strip()
                    return

        t = threading.Thread(target=reader, daemon=True)
        t.start(); t.join(timeout)
        if t.is_alive() or "data" not in box:
            _kill(key)
            raise RuntimeError(f"{script} timed out after {timeout}s (degenerate chunk; server killed)")
        data = json.loads(box["data"])
        if isinstance(data, dict) and "error" in data:
            raise RuntimeError(f"{script}: {data['error']}")
        return data


def shutdown_servers():
    """Free the warm models (called by the worker after an idle stretch)."""
    with _srv_lock:
        for key in list(_servers):
            _kill(key)


def _duration_s(path):
    """Audio length in seconds (the normalized 16k wav), for scaling stage timeouts."""
    try:
        import wave
        with wave.open(path, "rb") as w:
            return w.getnframes() / float(w.getframerate() or 16000)
    except Exception:  # noqa: BLE001 — unknown -> assume a full max-length segment
        return 20.0


def run_json(python, script, *args, timeout=None):
    try:
        proc = subprocess.run([python, os.path.join(HERE, script), *args, "--json"],
                              capture_output=True, text=True, cwd=HERE, timeout=timeout)
    except subprocess.TimeoutExpired:
        # RuntimeError (not SystemExit) so the worker catches it, marks the chunk
        # failed, and moves on instead of one bad chunk wedging the whole queue.
        raise RuntimeError(f"{script} timed out after {timeout}s (likely a degenerate chunk)")
    for line in reversed(proc.stdout.strip().splitlines()):
        line = line.strip()
        if line.startswith("[") or line.startswith("{"):
            return json.loads(line)
    sys.stderr.write(proc.stdout + proc.stderr)
    raise RuntimeError(f"no JSON from {script} (exit {proc.returncode})")


def turn_of_word(w, turns):
    """The diarization turn overlapping this word most (nearest if it's in a gap)."""
    best, best_ov = None, 0.0
    for t in turns:
        ov = min(w["end"], t["end"]) - max(w["start"], t["start"])
        if ov > best_ov:
            best_ov, best = ov, t
    if best:
        return best
    mid = (w["start"] + w["end"]) / 2
    return min(turns, key=lambda t: abs(mid - (t["start"] + t["end"]) / 2)) if turns else None


def process_audio(audio, model="large-v3", chunk_id=None, verbose=False):
    """Transcribe + diarize + identify `audio`, persist a word-level speaker-
    attributed transcript, and return its transcript id. GPU-heavy (the caller is
    responsible for the GPU gate). `chunk_id` links it back to the ingested chunk."""
    if verbose:
        print("[1/2] whisperx transcribe + align (cu128) ...", file=sys.stderr, flush=True)
    # Name-bias the decoder with known speakers + pass tunable no-speech gates (ADR-038)
    _c = db.connect()
    _names = ",".join(r["name"] for r in db.enrolled_speakers(_c))
    # Timeout scales with audio length so a long, genuinely multi-speaker conversation
    # isn't killed, but a degenerate micro-turn explosion on a noise chunk is (ADR-048).
    dur = _duration_s(audio)
    words = _server_request("wx", WX_PY, "wx_align.py",
                            {"audio": audio, "model": model, "names": _names,
                             "nospeech": db.cfg(_c, "asr_no_speech_max", 0.6),
                             "minlogprob": db.cfg(_c, "asr_min_logprob", -1.0)},
                            timeout=int(90 + 12 * dur))
    if not words:
        # (b) Whisper heard nothing — skip the expensive diarize + ECAPA stages entirely
        # (that's where noise chunks stall). Store an empty transcript so the chunk closes.
        if verbose:
            print("no speech — skipped diarization/ECAPA", file=sys.stderr, flush=True)
        conn = db.connect()
        cur = conn.cursor()
        cur.execute("INSERT INTO transcripts(chunk_id, audio_path, lang) VALUES (?, ?, 'en')",
                    (chunk_id, audio))
        tid = cur.lastrowid
        conn.commit()
        return tid
    if verbose:
        print("[2/2] diarize + identify       (cu13)  ...", file=sys.stderr, flush=True)
    # Pass the word timings so the diarizer trims each speaker's ECAPA embedding to the
    # actual speech (not the silence-padded turn) — better ID + voiceprints (ADR-051).
    turns = _server_request("diar", DIAR_PY, "identify.py",
                            {"audio": audio, "words": words},
                            timeout=int(60 + 10 * dur))

    # assign each word to a speaker, then group consecutive same-speaker words
    blocks = []
    for w in words:
        t = turn_of_word(w, turns) or {}
        name, sid = t.get("name", "?"), t.get("speaker_id")
        if not blocks or blocks[-1]["name"] != name:
            blocks.append({"name": name, "sid": sid, "start": w["start"], "words": []})
        blocks[-1]["words"].append(w["word"])
        blocks[-1]["end"] = w["end"]

    conn = db.connect()
    cur = conn.cursor()
    cur.execute("INSERT INTO transcripts(chunk_id, audio_path, lang) VALUES (?, ?, 'en')",
                (chunk_id, audio))
    tid = cur.lastrowid
    for b in blocks:
        text = " ".join(b["words"]).replace("  ", " ").strip()
        cur.execute("INSERT INTO segments(transcript_id, speaker_id, t_start, t_end, text)"
                    " VALUES (?,?,?,?,?)", (tid, b["sid"], b["start"], b["end"], text))
        if verbose:
            print(f"\n{b['name']}:\n  [{b['start']:6.2f}] {text}")
    conn.commit()
    return tid


def main():
    audio = sys.argv[1] if len(sys.argv) > 1 else "samples/two.wav"
    model = sys.argv[2] if len(sys.argv) > 2 else "small.en"
    print(f"=== Word-level speaker-attributed transcript: {audio} ===")
    tid = process_audio(audio, model, verbose=True)
    print(f"\nsaved transcript #{tid} -> listener.db", file=sys.stderr)


if __name__ == "__main__":
    main()
