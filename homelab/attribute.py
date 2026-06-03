#!/usr/bin/env python3
"""H3.5 — speaker-attributed transcript ("who said what").

Runs transcription (CUDA-12 venv) and diarization (CUDA-13 venv) as subprocesses
— they can't share a process (cuDNN conflict) — then merges by timestamp overlap.

Usage:
    python attribute.py [audio] [whisper_model]
"""
import json
import os
import subprocess
import sys

HOME = os.path.expanduser("~")
WHISPER_PY = f"{HOME}/listener-venv/bin/python"   # CUDA-12 / faster-whisper
DIAR_PY = f"{HOME}/listener-diar/bin/python"      # CUDA-13 / pyannote
HERE = os.path.dirname(os.path.abspath(__file__))


def run_json(python: str, script: str, *args: str):
    """Run a venv script in --json mode and parse the JSON it prints to stdout."""
    proc = subprocess.run(
        [python, os.path.join(HERE, script), *args, "--json"],
        capture_output=True, text=True, cwd=HERE,
    )
    for line in reversed(proc.stdout.strip().splitlines()):
        line = line.strip()
        if line.startswith("[") or line.startswith("{"):
            return json.loads(line)
    sys.stderr.write(proc.stdout + proc.stderr)
    raise SystemExit(f"no JSON from {script} (exit {proc.returncode})")


def speaker_for(seg, turns):
    """The speaker (name if identified) whose turns overlap this segment most."""
    best, best_overlap = "?", 0.0
    for t in turns:
        overlap = max(0.0, min(seg["end"], t["end"]) - max(seg["start"], t["start"]))
        if overlap > best_overlap:
            best_overlap, best = overlap, t.get("name", t["speaker"])
    return best


def main() -> None:
    audio = sys.argv[1] if len(sys.argv) > 1 else "samples/two.wav"
    model = sys.argv[2] if len(sys.argv) > 2 else "small.en"

    print(f"[1/2] transcribing      (CUDA-12 venv) ...", file=sys.stderr, flush=True)
    segments = run_json(WHISPER_PY, "transcribe.py", audio, model)
    print(f"[2/2] diarize+identify  (CUDA-13 venv) ...", file=sys.stderr, flush=True)
    turns = run_json(DIAR_PY, "identify.py", audio)

    print(f"\n=== Speaker-attributed transcript: {audio} ===")
    last_speaker = None
    for seg in segments:
        spk = speaker_for(seg, turns)
        if spk != last_speaker:
            print(f"\n{spk}:")
            last_speaker = spk
        print(f"  [{seg['start']:6.2f}] {seg['text']}")


if __name__ == "__main__":
    main()
