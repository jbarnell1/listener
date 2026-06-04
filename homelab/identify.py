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


def main() -> None:
    as_json = "--json" in sys.argv
    args = [a for a in sys.argv[1:] if a != "--json"]
    audio = args[0] if args else "samples/two.wav"

    def log(m):
        print(m, file=sys.stderr if as_json else sys.stdout, flush=True)

    log("Loading diarization pipeline + speaker DB ...")
    pipeline = Pipeline.from_pretrained("pyannote/speaker-diarization-community-1")
    pipeline.to(torch.device("cuda"))
    sdb = SpeakerDB()

    output = pipeline(audio)
    ann = output if hasattr(output, "itertracks") else getattr(output, "speaker_diarization", output)

    # group turns per diarized speaker, then identify each
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

    rows = [{"start": round(t.start, 2), "end": round(t.end, 2),
             "speaker": spk, "name": names[spk], "speaker_id": sids.get(spk)}
            for t, _, spk in ann.itertracks(yield_label=True)]

    if as_json:
        print(json.dumps(rows))
    else:
        print()
        for r in rows:
            print(f"  [{r['start']:6.2f} -> {r['end']:6.2f}]  {r['name']}  ({r['speaker']})")


if __name__ == "__main__":
    main()
