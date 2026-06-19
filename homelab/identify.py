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


def _merge_word_spans(words, max_gap=0.4):
    """Merge a speaker's words into contiguous speech spans, bridging tiny inter-word
    gaps. This trims the silence padding a diarization turn carries before/after speech."""
    spans = []
    for w in sorted(words, key=lambda x: x["start"]):
        if spans and w["start"] - spans[-1][1] <= max_gap:
            spans[-1][1] = max(spans[-1][1], w["end"])
        else:
            spans.append([w["start"], w["end"]])
    return [(s, e) for s, e in spans if e > s]


def _word_speaker(w, turn_list):
    """Which diarized speaker's turns overlap this word most (or None)."""
    best, best_ov = None, 0.0
    for s, e, spk in turn_list:
        ov = min(w["end"], e) - max(w["start"], s)
        if ov > best_ov:
            best_ov, best = ov, spk
    return best


def identify_turns(audio, words=None, log=lambda m: None):
    """Diarize + identify. The ECAPA embedding (which becomes the speaker's voiceprint) is
    computed on the actual transcribed WORD spans, not the raw diarization turn — so leading/
    trailing/inter-turn silence doesn't dilute identification or training (ADR-051). Falls
    back to raw turns only when no word timing is supplied (CLI). SpeakerDB is re-read each
    call so new enrollments take effect immediately; pyannote + ECAPA stay warm."""
    pipeline = _pipeline()
    sdb = SpeakerDB()                       # cheap: reads the speaker library from sqlite
    output = pipeline(audio)
    ann = output if hasattr(output, "itertracks") else getattr(output, "speaker_diarization", output)
    turn_list = [(t.start, t.end, spk) for t, _, spk in ann.itertracks(yield_label=True)]
    speakers, seen = [], set()
    for _, _, spk in turn_list:
        if spk not in seen:
            seen.add(spk); speakers.append(spk)

    # Embedding spans per speaker: word-trimmed in production, raw turns as CLI fallback.
    if words:
        wmap = {}
        for w in words:
            spk = _word_speaker(w, turn_list)
            if spk is not None:
                wmap.setdefault(spk, []).append(w)
        spans_by_spk = {spk: _merge_word_spans(ws) for spk, ws in wmap.items()}
    else:
        spans_by_spk = {}
        for s, e, spk in turn_list:
            spans_by_spk.setdefault(spk, []).append((s, e))

    names, sids = {}, {}
    for spk in speakers:
        spans = spans_by_spk.get(spk) or []
        emb = embed_segments(audio, spans) if spans else None
        if emb is None:                     # a turn with no transcribed words = noise, not a person
            names[spk], sids[spk] = f"Unknown_{spk[-2:]}", None
            continue
        label, score, sid = sdb.identify(emb)
        names[spk], sids[spk] = label, sid
        log(f"  {spk} -> {label}  (best cosine {score:.2f})")
    return [{"start": round(s, 2), "end": round(e, 2),
             "speaker": spk, "name": names[spk], "speaker_id": sids.get(spk)}
            for s, e, spk in turn_list]


def serve():
    """Warm server: load models once, then one JSON request ({"audio":..}) per stdin line."""
    print("diar serve: ready", file=sys.stderr, flush=True)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            rows = identify_turns(req["audio"], req.get("words"),
                                  log=lambda m: print(m, file=sys.stderr, flush=True))
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
    rows = identify_turns(audio, None, log)
    if as_json:
        print(json.dumps(rows))
    else:
        print()
        for r in rows:
            print(f"  [{r['start']:6.2f} -> {r['end']:6.2f}]  {r['name']}  ({r['speaker']})")


if __name__ == "__main__":
    serve() if "--serve" in sys.argv else main()
