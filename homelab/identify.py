#!/usr/bin/env python3
"""H3.6 — diarize + identify known speakers (names, not just SPEAKER_xx).

Usage:
    python identify.py [audio] [--json]

Diarizes, computes an ECAPA embedding per speaker, matches the speaker library,
and labels each turn with a name (or Unknown_xx). Runs in ~/listener-diar.
"""
import json
import sys

import torch
from pyannote.audio import Pipeline

from speakerid import SpeakerDB, embed_segments

# --- warm pipeline cache (loaded once, reused across requests in --serve, ADR-049) ---
_PIPELINE = None
RESULT = "\x02"          # sentinel prefix marking the JSON result line on stdout


def _pipeline():
    global _PIPELINE
    if _PIPELINE is None:
        _PIPELINE = Pipeline.from_pretrained("pyannote/speaker-diarization-community-1")
        _PIPELINE.to(torch.device("cuda"))
    return _PIPELINE


def identify_turns(audio, log=lambda m: None):
    """Diarize + identify. SpeakerDB is re-read each call so new enrollments take effect
    immediately; the pyannote + ECAPA models stay warm across calls."""
    pipeline = _pipeline()
    sdb = SpeakerDB()                       # cheap: reads the speaker library from sqlite
    output = pipeline(audio)
    ann = output if hasattr(output, "itertracks") else getattr(output, "speaker_diarization", output)
    per_speaker = {}
    for turn, _, spk in ann.itertracks(yield_label=True):
        per_speaker.setdefault(spk, []).append((turn.start, turn.end))
    names, sids = {}, {}
    for spk, turns in per_speaker.items():
        emb = embed_segments(audio, turns)
        if emb is None:
            names[spk], sids[spk] = f"Unknown_{spk[-2:]}", None
            continue
        label, score, sid = sdb.identify(emb)
        names[spk], sids[spk] = label, sid
        log(f"  {spk} -> {label}  (best cosine {score:.2f})")
    return [{"start": round(t.start, 2), "end": round(t.end, 2),
             "speaker": spk, "name": names[spk], "speaker_id": sids.get(spk)}
            for t, _, spk in ann.itertracks(yield_label=True)]


def serve():
    """Warm server: load models once, then one JSON request ({"audio":..}) per stdin line."""
    print("diar serve: ready", file=sys.stderr, flush=True)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            rows = identify_turns(req["audio"], log=lambda m: print(m, file=sys.stderr, flush=True))
            out = json.dumps(rows)
        except Exception as e:  # noqa: BLE001
            out = json.dumps({"error": str(e)})
            print(f"diar serve error: {e}", file=sys.stderr, flush=True)
        sys.stdout.write(RESULT + out + "\n")
        sys.stdout.flush()


def main() -> None:
    as_json = "--json" in sys.argv
    args = [a for a in sys.argv[1:] if a != "--json"]
    audio = args[0] if args else "samples/two.wav"

    def log(m):
        print(m, file=sys.stderr if as_json else sys.stdout, flush=True)

    log("Loading diarization pipeline + speaker DB ...")
    rows = identify_turns(audio, log)
    if as_json:
        print(json.dumps(rows))
    else:
        print()
        for r in rows:
            print(f"  [{r['start']:6.2f} -> {r['end']:6.2f}]  {r['name']}  ({r['speaker']})")


if __name__ == "__main__":
    serve() if "--serve" in sys.argv else main()
