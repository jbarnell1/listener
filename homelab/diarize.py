#!/usr/bin/env python3
"""H3.5 — speaker diarization with pyannote.audio on the GPU.

Usage:
    python diarize.py [audio.wav]

Prints who spoke when (SPEAKER_00 / SPEAKER_01 / ...). Uses the HuggingFace token
stored by `hf auth login`. First run downloads the pyannote models.
"""
import sys
import time

import torch
from pyannote.audio import Pipeline


def main() -> None:
    audio = sys.argv[1] if len(sys.argv) > 1 else "samples/jfk.wav"
    # community-1 is pyannote 4.x's open (non-gated) pipeline. Override to
    # "pyannote/speaker-diarization-3.1" if you've accepted its gated terms.
    model = sys.argv[2] if len(sys.argv) > 2 else "pyannote/speaker-diarization-community-1"

    print(f"Loading {model} ...", flush=True)
    t0 = time.time()
    pipeline = Pipeline.from_pretrained(model)
    pipeline.to(torch.device("cuda"))
    print(f"  loaded in {time.time() - t0:.1f}s")

    print(f"Diarizing: {audio}", flush=True)
    t1 = time.time()
    output = pipeline(audio)
    elapsed = time.time() - t1

    # pyannote 4.x community pipelines return a DiarizeOutput wrapper; the
    # pyannote.core.Annotation lives on one of these attributes.
    annotation = output
    if not hasattr(output, "itertracks"):
        for attr in ("speaker_diarization", "diarization", "annotation"):
            if hasattr(output, attr) and hasattr(getattr(output, attr), "itertracks"):
                annotation = getattr(output, attr)
                break
    if not hasattr(annotation, "itertracks"):
        print("Unexpected output type:", type(output),
              [a for a in dir(output) if not a.startswith("_")])
        raise SystemExit(1)

    speakers = set()
    for turn, _, spk in annotation.itertracks(yield_label=True):
        speakers.add(spk)
        print(f"  [{turn.start:6.2f} -> {turn.end:6.2f}]  {spk}")

    print()
    print(f"speakers found: {len(speakers)}  ({', '.join(sorted(speakers))})")
    print(f"diarize time  : {elapsed:.2f}s")


if __name__ == "__main__":
    main()
