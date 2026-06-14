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

import db

HOME = os.path.expanduser("~")
WX_PY = f"{HOME}/listener-wx/bin/python"      # WhisperX (transcribe + align)
DIAR_PY = f"{HOME}/listener-diar/bin/python"  # pyannote diarize + ECAPA identify
HERE = os.path.dirname(os.path.abspath(__file__))


def run_json(python, script, *args):
    proc = subprocess.run([python, os.path.join(HERE, script), *args, "--json"],
                          capture_output=True, text=True, cwd=HERE)
    for line in reversed(proc.stdout.strip().splitlines()):
        line = line.strip()
        if line.startswith("[") or line.startswith("{"):
            return json.loads(line)
    sys.stderr.write(proc.stdout + proc.stderr)
    raise SystemExit(f"no JSON from {script} (exit {proc.returncode})")


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
    _extra = ["--nospeech", str(db.cfg(_c, "asr_no_speech_max", 0.6)),
              "--minlogprob", str(db.cfg(_c, "asr_min_logprob", -1.0))]
    if _names:
        _extra = ["--names", _names] + _extra
    words = run_json(WX_PY, "wx_align.py", audio, model, *_extra)
    if verbose:
        print("[2/2] diarize + identify       (cu13)  ...", file=sys.stderr, flush=True)
    turns = run_json(DIAR_PY, "identify.py", audio)

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
